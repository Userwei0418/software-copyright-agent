import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

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
        static_entries = [path for path in paths if path.lower() in {
            "index.html", "public/index.html", "dist/index.html", "build/index.html"
        }]
        unsafe_markers = [path for path in paths if PurePosixPath(path).name.lower() in {
            ".env", ".env.local", "docker-compose.yml", "docker-compose.yaml"
        }]
        if not has_ui_section:
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
                     title: str, description: dict) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        self._validate_section(job_id, section_key)
        clean_description = self._validate_description(description)
        source = source_path.expanduser().resolve()
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
        except (UnidentifiedImageError, OSError) as error:
            raise ManualScreenshotError("无法读取截图，请选择 PNG、JPEG 或 WebP 图片") from error
        key_base = re.sub(r"[^a-z0-9_-]", "-", source.stem.lower()).strip("-") or "screen"
        screenshot_key = self._unique_key(job_id, key_base)
        relative = Path("artifacts") / "manual" / "screenshots" / (screenshot_key + ".png")
        target = self._data_root / "tasks" / context["task_id"] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        sanitized.save(target, format="PNG", optimize=True)
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_screenshot_revisions(id, job_id, screenshot_key,
                version, section_key, title, source, image_relative_path, description_json,
                width, height, sha256, created_at) VALUES
                (?, ?, ?, 1, ?, ?, 'user', ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), job_id, screenshot_key, section_key, title.strip(),
                 relative.as_posix(),
                 json.dumps(clean_description, ensure_ascii=False, separators=(",", ":")),
                 width, height, sha256, now),
            )
            connection.execute(
                """INSERT INTO manual_screenshot_artifacts(id, job_id, screenshot_key,
                section_key, title, source, image_relative_path, description_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'user', ?, ?, ?)""",
                (str(uuid4()), job_id, screenshot_key, section_key, title.strip(),
                 relative.as_posix(),
                 json.dumps(clean_description, ensure_ascii=False, separators=(",", ":")), now),
            )
        return {"screenshot_key": screenshot_key, "section_key": section_key,
                "title": title.strip(), "source": "user",
                "image_relative_path": relative.as_posix(),
                "description": clean_description, "width": width, "height": height,
                "sha256": sha256, "created_at": now}

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

    def list(self, job_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT msa.*, msr.width, msr.height, msr.sha256
                FROM manual_screenshot_artifacts msa
                LEFT JOIN manual_screenshot_revisions msr ON msr.job_id=msa.job_id
                    AND msr.screenshot_key=msa.screenshot_key AND msr.version=(
                        SELECT MAX(version) FROM manual_screenshot_revisions
                        WHERE job_id=msa.job_id AND screenshot_key=msa.screenshot_key)
                WHERE msa.job_id=? ORDER BY msa.created_at""", (job_id,),
            ).fetchall()
        return [{"screenshot_key": row["screenshot_key"],
                 "section_key": row["section_key"], "title": row["title"],
                 "source": row["source"], "image_relative_path": row["image_relative_path"],
                 "description": json.loads(row["description_json"]),
                 "width": row["width"], "height": row["height"],
                 "sha256": row["sha256"], "created_at": row["created_at"]} for row in rows]

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
                """SELECT j.task_id, psn.manifest_relative_path, psn.scan_root_mode,
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

    def _validate_section(self, job_id: str, section_key: str) -> None:
        with self._database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM manual_section_artifacts WHERE job_id=? AND section_key=?",
                (job_id, section_key),
            ).fetchone()
        if exists is None:
            raise ManualScreenshotError("截图绑定章节不存在")

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
