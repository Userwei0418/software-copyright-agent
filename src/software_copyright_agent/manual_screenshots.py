import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Optional
from uuid import uuid4

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

from .service import utc_now
from .storage import Database


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MIN_WIDTH, MIN_HEIGHT = 640, 360
MAX_DIMENSION = 12_000
DESCRIPTION_FIELDS = (
    "page_purpose", "entry_conditions", "visible_regions", "typical_workflow",
    "backend_interactions", "result_validation_recovery",
)


class ManualScreenshotError(ValueError):
    pass


class ManualScreenshotService:
    """Persists safe capture decisions and sanitized user/adapter screenshots."""

    def __init__(self, database: Database, data_root: Path,
                 capture_adapter_available: bool = False) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._capture_adapter_available = capture_adapter_available

    def assess(self, job_id: str) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        task_root = self._data_root / "tasks" / context["task_id"]
        manifest = task_root / PurePosixPath(context["manifest_relative_path"])
        paths = self._manifest_paths(manifest)
        has_ui_section = self._has_ui_section(job_id)
        has_ui_evidence = has_ui_section or self._has_ui_evidence(paths)
        static_entries = [path for path in paths if any(
            path.lower() == candidate or path.lower().endswith("/" + candidate)
            for candidate in ("index.html", "public/index.html", "dist/index.html",
                              "build/index.html")
        )]
        unsafe_markers = [path for path in paths if PurePosixPath(path).name.lower() in {
            ".env", ".env.local", "docker-compose.yml", "docker-compose.yaml"
        }]
        if not has_ui_evidence:
            status = "not_applicable"
            reason = "结构化正文未识别出适用的用户界面章节"
        elif static_entries and self._capture_adapter_available and not unsafe_markers:
            status = "auto_available"
            reason = "发现静态界面入口且本地截图适配器可用"
        else:
            status = "manual_import"
            if not static_entries:
                reason = "未发现无需执行项目代码即可打开的静态界面入口"
            elif not self._capture_adapter_available:
                reason = "当前安装包未提供可控浏览器截图适配器"
            else:
                reason = "项目包含可能依赖外部环境的启动配置，不自动执行"
        assessment = {
            "job_id": job_id, "status": status, "reason": reason,
            "has_ui_section": has_ui_section, "static_entries": static_entries,
            "has_ui_evidence": has_ui_evidence,
            "capture_adapter_available": self._capture_adapter_available,
            "automatic_capture_policy": {
                "runs_project_commands": False, "allows_external_network": False,
                "allows_paid_calls": False, "allows_real_credentials": False,
            },
            "next_action": {
                "auto_available": "可由安全截图适配器采集静态页面",
                "manual_import": "请导入真实界面截图；也可以跳过截图继续生成说明书",
                "not_applicable": "跳过截图章节，不阻塞正式文档生成",
            }[status],
        }
        now = utc_now()
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value
                FROM manual_capture_assessments WHERE job_id=?""", (job_id,),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO manual_capture_assessments(id, job_id, version, status,
                assessment_json, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), job_id, version, status,
                 json.dumps(assessment, ensure_ascii=False, separators=(",", ":")), now),
            )
        assessment.update({"version": version, "created_at": now})
        return assessment

    def latest_assessment(self, job_id: str):
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT version, assessment_json, created_at FROM manual_capture_assessments
                WHERE job_id=? ORDER BY version DESC LIMIT 1""", (job_id,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["assessment_json"])
        result.update({"version": row["version"], "created_at": row["created_at"]})
        return result

    def import_image(self, job_id: str, source_path: Path, section_key: str,
                     title: str, description: dict, capture_source: str = "user") -> dict:
        self._database.initialize()
        if capture_source not in {"user", "automated"}:
            raise ManualScreenshotError("截图来源无效")
        context = self._context(job_id)
        self._validate_section(job_id, section_key)
        clean_description = self._validate_description(description)
        source = source_path.expanduser().resolve()
        sanitized, width, height = self._sanitize_source(source)
        duplicate = self._near_duplicate(job_id, context["task_id"], sanitized)
        if duplicate:
            raise ManualScreenshotError(
                "该页面与已有截图“{0}”高度重复；请切换到不同业务页面或等待数据加载后再采集".format(
                    duplicate
                )
            )
        key_base = re.sub(r"[^a-z0-9_-]", "-", source.stem.lower()).strip("-") or "screen"
        screenshot_key = self._unique_key(job_id, key_base)
        relative = (Path("artifacts") / "manual" / "jobs" /
                    "job-v{0}".format(context["job_version"]) / "screenshots" /
                    (screenshot_key + ".png"))
        target = self._data_root / "tasks" / context["task_id"] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        sanitized.save(target, format="PNG", optimize=True)
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_screenshot_revisions(id, job_id, screenshot_key,
                version, section_key, title, source, image_relative_path, description_json,
                width, height, sha256, created_at, edit_source, parent_revision_id,
                change_summary_json, archived) VALUES
                (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', NULL, ?, 0)""",
                (str(uuid4()), job_id, screenshot_key, section_key, title.strip(),
                 capture_source, relative.as_posix(),
                 json.dumps(clean_description, ensure_ascii=False, separators=(",", ":")),
                 width, height, sha256, now,
                 json.dumps({"fields": ["image", "section_key", "title", "description"]},
                            ensure_ascii=False, separators=(",", ":"))),
            )
            connection.execute(
                """INSERT INTO manual_screenshot_artifacts(id, job_id, screenshot_key,
                section_key, title, source, image_relative_path, description_json, created_at,
                updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (str(uuid4()), job_id, screenshot_key, section_key, title.strip(),
                 capture_source, relative.as_posix(),
                 json.dumps(clean_description, ensure_ascii=False, separators=(",", ":")),
                 now, now),
            )
        self._mark_capture_available(job_id)
        return {"screenshot_key": screenshot_key, "section_key": section_key,
                "title": title.strip(), "source": capture_source,
                "image_relative_path": relative.as_posix(),
                "description": clean_description, "width": width, "height": height,
                "sha256": sha256, "version": 1, "archived": False,
                "created_at": now, "updated_at": now}

    def _near_duplicate(self, job_id: str, task_id: str, candidate: Image.Image):
        sample = candidate.convert("L").resize((64, 64))
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT title,image_relative_path FROM manual_screenshot_artifacts
                WHERE job_id=? AND archived_at IS NULL""", (job_id,),
            ).fetchall()
        for row in rows:
            path = (self._data_root / "tasks" / task_id /
                    Path(row["image_relative_path"])).resolve()
            try:
                existing = Image.open(path).convert("L").resize((64, 64))
                delta = ImageStat.Stat(ImageChops.difference(sample, existing)).mean[0]
            except (OSError, ValueError):
                continue
            if delta < 2.2:
                return row["title"]
        return None

    def _mark_capture_available(self, job_id: str) -> None:
        now = utc_now()
        with self._database.connect() as connection:
            count = connection.execute(
                """SELECT COUNT(*) value FROM manual_screenshot_artifacts
                WHERE job_id=? AND archived_at IS NULL""", (job_id,),
            ).fetchone()["value"]
            summary = json.dumps({"screenshot_count": count, "assessment_status": "captured"},
                                 ensure_ascii=False, separators=(",", ":"))
            step = connection.execute(
                """SELECT id FROM manual_generation_steps WHERE job_id=?
                AND step_key='screenshots' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if step is not None:
                connection.execute(
                    """UPDATE manual_generation_steps SET status='completed',summary_json=?,
                    started_at=COALESCE(started_at,?),finished_at=?,safe_error_message=NULL
                    WHERE id=?""", (summary, now, now, step["id"]),
                )
            connection.execute(
                """UPDATE manual_execution_nodes SET status='completed',output_json=?,
                heartbeat_at=?,finished_at=?,updated_at=?,error_category=NULL,
                safe_error_message=NULL WHERE job_id=? AND node_key='screenshots'""",
                (summary, now, now, now, job_id),
            )

    def update_metadata(self, job_id: str, screenshot_key: str, section_key: str,
                        title: str, description: dict) -> dict:
        self._validate_section(job_id, section_key)
        clean_title = title.strip()
        if not clean_title:
            raise ManualScreenshotError("截图标题不能为空")
        snapshot = self._snapshot(job_id, screenshot_key, require_active=True)
        clean_description = self._validate_description(description)
        changed = []
        for key, value in (("section_key", section_key), ("title", clean_title),
                           ("description", clean_description)):
            if snapshot[key] != value:
                changed.append(key)
        if not changed:
            raise ManualScreenshotError("截图信息没有变化")
        snapshot.update({"section_key": section_key, "title": clean_title,
                         "description": clean_description})
        return self._persist_snapshot(job_id, snapshot, "manual", {"fields": changed})

    def replace_image(self, job_id: str, screenshot_key: str,
                      source_path: Path) -> dict:
        context = self._context(job_id)
        snapshot = self._snapshot(job_id, screenshot_key, require_active=True)
        source = source_path.expanduser().resolve()
        sanitized, width, height = self._sanitize_source(source)
        version = snapshot["version"] + 1
        relative = (Path("artifacts") / "manual" / "jobs" /
                    "job-v{0}".format(context["job_version"]) / "screenshots" /
                    screenshot_key /
                    "v{0}.png".format(version))
        target = self._data_root / "tasks" / context["task_id"] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        sanitized.save(target, format="PNG", optimize=True)
        snapshot.update({"source": "user", "image_relative_path": relative.as_posix(),
                         "width": width, "height": height,
                         "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        return self._persist_snapshot(job_id, snapshot, "replacement", {
            "fields": ["image"], "original_filename": source.name[:200],
        })

    def revisions(self, job_id: str, screenshot_key: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id, version, section_key, title, source, image_relative_path,
                description_json, width, height, sha256, edit_source, parent_revision_id,
                change_summary_json, archived, created_at
                FROM manual_screenshot_revisions WHERE job_id=? AND screenshot_key=?
                ORDER BY version DESC""", (job_id, screenshot_key),
            ).fetchall()
        return [{"revision_id": row["id"], "version": row["version"],
                 "section_key": row["section_key"], "title": row["title"],
                 "source": row["source"], "image_relative_path": row["image_relative_path"],
                 "description": json.loads(row["description_json"]),
                 "width": row["width"], "height": row["height"], "sha256": row["sha256"],
                 "edit_source": row["edit_source"],
                 "parent_revision_id": row["parent_revision_id"],
                 "change_summary": json.loads(row["change_summary_json"]),
                 "archived": bool(row["archived"]), "created_at": row["created_at"]}
                for row in rows]

    def rollback(self, job_id: str, screenshot_key: str, version: int) -> dict:
        self._snapshot(job_id, screenshot_key)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT section_key, title, source, image_relative_path, description_json,
                width, height, sha256 FROM manual_screenshot_revisions
                WHERE job_id=? AND screenshot_key=? AND version=?""",
                (job_id, screenshot_key, version),
            ).fetchone()
        if row is None:
            raise ManualScreenshotError("指定的截图历史版本不存在")
        snapshot = {"screenshot_key": screenshot_key, "section_key": row["section_key"],
                    "title": row["title"], "source": row["source"],
                    "image_relative_path": row["image_relative_path"],
                    "description": json.loads(row["description_json"]),
                    "width": row["width"], "height": row["height"],
                    "sha256": row["sha256"]}
        return self._persist_snapshot(job_id, snapshot, "rollback", {
            "restored_from_version": version,
        })

    def set_archived(self, job_id: str, screenshot_key: str, archived: bool) -> dict:
        snapshot = self._snapshot(job_id, screenshot_key)
        if snapshot["archived"] == archived:
            raise ManualScreenshotError("截图归档状态没有变化")
        return self._persist_snapshot(job_id, snapshot,
                                      "archive" if archived else "restore",
                                      {"archived": archived}, archived=archived)

    def finalize(self, job_id: str) -> dict:
        self._database.initialize()
        assessment = self.latest_assessment(job_id) or self.assess(job_id)
        screenshots = self.list(job_id)
        now = utc_now()
        with self._database.connect() as connection:
            step = connection.execute(
                """SELECT id, attempt FROM manual_generation_steps WHERE job_id=?
                AND step_key='screenshots' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if step is None:
                raise ManualScreenshotError("说明书任务缺少截图阶段")
            status = "completed" if screenshots else "skipped"
            summary = {"screenshot_count": len(screenshots),
                       "assessment_status": assessment["status"],
                       "reason": assessment["reason"] if not screenshots else None}
            connection.execute(
                """UPDATE manual_generation_steps SET status=?, summary_json=?,
                started_at=COALESCE(started_at, ?), finished_at=?, safe_error_message=NULL
                WHERE id=?""",
                (status, json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                 now, now, step["id"]),
            )
            progress = {"completed": 4, "total": 6, "percent": 67}
            connection.execute(
                """UPDATE manual_generation_jobs SET status='running',
                current_step='assemble_docx', progress_json=?, updated_at=?,
                safe_error_message=NULL WHERE id=?""",
                (json.dumps(progress, separators=(",", ":")), now, job_id),
            )
        return {"job_id": job_id, "status": status, "assessment": assessment,
                "screenshots": screenshots}

    def list(self, job_id: str, include_archived: bool = False) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT msa.*, msr.width, msr.height, msr.sha256, msr.version
                FROM manual_screenshot_artifacts msa
                LEFT JOIN manual_screenshot_revisions msr ON msr.job_id=msa.job_id
                    AND msr.screenshot_key=msa.screenshot_key AND msr.version=(
                        SELECT MAX(version) FROM manual_screenshot_revisions
                        WHERE job_id=msa.job_id AND screenshot_key=msa.screenshot_key)
                WHERE msa.job_id=? AND (?=1 OR msa.archived_at IS NULL)
                ORDER BY msa.archived_at IS NOT NULL, msa.updated_at DESC""",
                (job_id, 1 if include_archived else 0),
            ).fetchall()
        return [{"screenshot_key": row["screenshot_key"],
                 "section_key": row["section_key"], "title": row["title"],
                 "source": row["source"], "image_relative_path": row["image_relative_path"],
                 "description": json.loads(row["description_json"]),
                 "width": row["width"], "height": row["height"],
                 "sha256": row["sha256"], "version": row["version"],
                 "archived": row["archived_at"] is not None,
                 "created_at": row["created_at"],
                 "updated_at": row["updated_at"] or row["created_at"]} for row in rows]

    def read_image(self, job_id: str, screenshot_key: str) -> bytes:
        context = self._context(job_id)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT image_relative_path FROM manual_screenshot_artifacts
                WHERE job_id=? AND screenshot_key=?""", (job_id, screenshot_key),
            ).fetchone()
        if row is None:
            raise ManualScreenshotError("截图不存在")
        task_root = (self._data_root / "tasks" / context["task_id"]).resolve()
        path = (task_root / row["image_relative_path"]).resolve()
        if task_root not in path.parents or not path.is_file():
            raise ManualScreenshotError("截图文件缺失或路径无效")
        content = path.read_bytes()
        with self._database.connect() as connection:
            digest = connection.execute(
                """SELECT sha256 FROM manual_screenshot_revisions WHERE job_id=?
                AND screenshot_key=? ORDER BY version DESC LIMIT 1""",
                (job_id, screenshot_key),
            ).fetchone()
        if digest and hashlib.sha256(content).hexdigest() != digest["sha256"]:
            raise ManualScreenshotError("截图完整性校验失败")
        return content

    def _context(self, job_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.task_id, j.version job_version,
                psn.manifest_relative_path, psn.scan_root_mode,
                psn.scan_root_path FROM manual_generation_jobs j
                JOIN tasks t ON t.id=j.task_id
                JOIN project_snapshots psn ON psn.id=t.snapshot_id WHERE j.id=?""", (job_id,),
            ).fetchone()
        if row is None:
            raise ManualScreenshotError("说明书任务或项目快照不存在")
        return dict(row)

    def _has_ui_section(self, job_id: str) -> bool:
        with self._database.connect() as connection:
            return connection.execute(
                """SELECT 1 FROM manual_section_artifacts WHERE job_id=?
                AND section_key='ui_operations'""", (job_id,),
            ).fetchone() is not None

    @staticmethod
    def _has_ui_evidence(paths: list) -> bool:
        markers = ("/src/views/", "/src/pages/", "/src/components/", "/frontend/",
                   "/fronted/", "/web/", "/client/")
        for value in paths:
            path = "/" + str(value).replace("\\", "/").lower().lstrip("/")
            name = PurePosixPath(path).name
            if name in {"index.html", "vite.config.ts", "vite.config.js",
                        "vue.config.js", "next.config.js", "next.config.mjs"}:
                return True
            if path.endswith((".vue", ".tsx", ".jsx")) or any(marker in path for marker in markers):
                return True
        return False

    def _validate_section(self, job_id: str, section_key: str) -> None:
        with self._database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM manual_section_artifacts WHERE job_id=? AND section_key=?",
                (job_id, section_key),
            ).fetchone()
        if exists is None:
            raise ManualScreenshotError("截图绑定章节不存在")

    @staticmethod
    def _sanitize_source(source: Path) -> tuple:
        if not source.is_file():
            raise ManualScreenshotError("所选截图文件不存在")
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ManualScreenshotError("截图文件超过 20 MiB 限制")
        try:
            with Image.open(source) as opened:
                opened.verify()
            with Image.open(source) as opened:
                width, height = opened.size
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    raise ManualScreenshotError("截图分辨率至少需要 640×360")
                if width > MAX_DIMENSION or height > MAX_DIMENSION:
                    raise ManualScreenshotError("截图尺寸超过安全限制")
                sanitized = opened.convert("RGB")
                sanitized.load()
                return sanitized, width, height
        except (UnidentifiedImageError, OSError) as error:
            raise ManualScreenshotError("无法读取截图，请选择 PNG、JPEG 或 WebP 图片") from error

    def _snapshot(self, job_id: str, screenshot_key: str,
                  require_active: bool = False) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT msa.screenshot_key, msa.section_key, msa.title, msa.source,
                msa.image_relative_path, msa.description_json, msa.archived_at,
                msr.id revision_id, msr.version, msr.width, msr.height, msr.sha256
                FROM manual_screenshot_artifacts msa
                JOIN manual_screenshot_revisions msr ON msr.job_id=msa.job_id
                    AND msr.screenshot_key=msa.screenshot_key
                WHERE msa.job_id=? AND msa.screenshot_key=?
                ORDER BY msr.version DESC LIMIT 1""", (job_id, screenshot_key),
            ).fetchone()
        if row is None:
            raise ManualScreenshotError("截图资产不存在")
        if require_active and row["archived_at"] is not None:
            raise ManualScreenshotError("已归档截图需先恢复后再修改")
        return {"screenshot_key": row["screenshot_key"],
                "section_key": row["section_key"], "title": row["title"],
                "source": row["source"], "image_relative_path": row["image_relative_path"],
                "description": json.loads(row["description_json"]),
                "archived": row["archived_at"] is not None,
                "revision_id": row["revision_id"], "version": row["version"],
                "width": row["width"], "height": row["height"], "sha256": row["sha256"]}

    def _persist_snapshot(self, job_id: str, snapshot: dict, edit_source: str,
                          change_summary: dict, archived: Optional[bool] = None) -> dict:
        now = utc_now()
        current = self._snapshot(job_id, snapshot["screenshot_key"])
        resolved_archived = current["archived"] if archived is None else archived
        version = current["version"] + 1
        revision_id = str(uuid4())
        description_json = json.dumps(snapshot["description"], ensure_ascii=False,
                                      separators=(",", ":"))
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_screenshot_revisions(id, job_id, screenshot_key,
                version, section_key, title, source, image_relative_path, description_json,
                width, height, sha256, created_at, edit_source, parent_revision_id,
                change_summary_json, archived) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (revision_id, job_id, snapshot["screenshot_key"], version,
                 snapshot["section_key"], snapshot["title"], snapshot["source"],
                 snapshot["image_relative_path"], description_json, snapshot["width"],
                 snapshot["height"], snapshot["sha256"], now, edit_source,
                 current["revision_id"], json.dumps(change_summary, ensure_ascii=False,
                                                     separators=(",", ":")),
                 1 if resolved_archived else 0),
            )
            connection.execute(
                """UPDATE manual_screenshot_artifacts SET section_key=?, title=?, source=?,
                image_relative_path=?, description_json=?, updated_at=?, archived_at=?
                WHERE job_id=? AND screenshot_key=?""",
                (snapshot["section_key"], snapshot["title"], snapshot["source"],
                 snapshot["image_relative_path"], description_json, now,
                 now if resolved_archived else None, job_id, snapshot["screenshot_key"]),
            )
        return {"revision_id": revision_id, "version": version,
                "screenshot_key": snapshot["screenshot_key"],
                "section_key": snapshot["section_key"], "title": snapshot["title"],
                "source": snapshot["source"],
                "image_relative_path": snapshot["image_relative_path"],
                "description": snapshot["description"], "width": snapshot["width"],
                "height": snapshot["height"], "sha256": snapshot["sha256"],
                "edit_source": edit_source, "archived": resolved_archived,
                "updated_at": now}

    @staticmethod
    def _manifest_paths(path: Path) -> list:
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line)["path"])
            except (json.JSONDecodeError, KeyError):
                continue
        return result

    @staticmethod
    def _validate_description(description: dict) -> dict:
        clean = {}
        for field in DESCRIPTION_FIELDS:
            value = str(description.get(field, "")).strip()
            if len(value) < 12:
                raise ManualScreenshotError("截图说明“{0}”内容过少".format(field))
            clean[field] = value[:2000]
        return clean

    def _unique_key(self, job_id: str, base: str) -> str:
        with self._database.connect() as connection:
            existing = {row["screenshot_key"] for row in connection.execute(
                "SELECT screenshot_key FROM manual_screenshot_artifacts WHERE job_id=?",
                (job_id,),
            ).fetchall()}
        if base not in existing:
            return base
        index = 2
        while "{0}-{1}".format(base, index) in existing:
            index += 1
        return "{0}-{1}".format(base, index)
