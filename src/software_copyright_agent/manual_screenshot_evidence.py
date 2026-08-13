import base64
import binascii
import hashlib
import io
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from uuid import uuid4
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat, UnidentifiedImageError

from .app_settings import AppSettingsService
from .credential_vault import CredentialVault
from .manual_execution import ManualExecutionNodeService, manual_job_slot
from .manual_generation import ManualGenerationError, ManualGenerationService, MODEL_READ_TIMEOUT_SECONDS
from .service import utc_now
from .storage import Database, encode_json


PROMPT_VERSION = "screenshot-interpretation-v1"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MIN_WIDTH, MIN_HEIGHT = 640, 360
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 12_000
INTERPRETATION_FIELDS = {
    "page_title": str, "page_type": str, "purpose": str, "target_roles": list,
    "entry_conditions": list, "visible_regions": list, "key_controls": list,
    "workflow_steps": list, "success_state": str, "failure_and_recovery": str,
    "related_backend_actions": list, "route_guess": str,
    "related_evidence_refs": list, "suggested_group": str,
    "suggested_order": int, "suggested_caption": str, "confidence": (int, float),
    "warnings": list,
}


class ScreenshotEvidenceError(ValueError):
    pass


class ScreenshotEvidenceService:
    """Project-level screenshot evidence, independent of a document job."""

    def __init__(self, database: Database, data_root: Path, model_call=None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._vault = CredentialVault(database, data_root)
        self._model_call = model_call

    def recover_interrupted(self) -> dict:
        """Turn process-local work left by a prior sidecar into explicit retryable failure."""
        self._database.initialize()
        now = utc_now()
        message = "上次应用退出时截图处理被中断；图片和已有解读均已保留，请重试该图片"
        with self._database.connect() as connection:
            assets = connection.execute(
                """SELECT id FROM manual_project_screenshot_assets
                WHERE analysis_status IN ('queued','running')"""
            ).fetchall()
            batches = connection.execute(
                """SELECT id FROM manual_screenshot_import_batches
                WHERE status IN ('queued','running')"""
            ).fetchall()
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET analysis_status='failed',
                failure_reason=?,updated_at=? WHERE analysis_status IN ('queued','running')""",
                (message, now),
            )
            connection.execute(
                """UPDATE manual_screenshot_import_batches SET status='failed',failure_count=
                CASE WHEN failure_count=0 THEN input_count ELSE failure_count END,
                summary_json=?,finished_at=? WHERE status IN ('queued','running')""",
                (encode_json({"reason": message, "recovered_after_restart": True}), now),
            )
        downgraded = self._reconcile_false_vision_capabilities()
        return {"asset_count": len(assets), "batch_count": len(batches),
                "downgraded_model_count": downgraded, "message": message}

    def _reconcile_false_vision_capabilities(self) -> int:
        """Persist explicit provider rejections that survived a prior process exit."""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT m.* FROM model_configs m
                JOIN manual_screenshot_interpretation_revisions i
                  ON i.model_config_id=m.id
                WHERE i.status='failed' AND i.failure_reason IS NOT NULL"""
            ).fetchall()
            reasons = connection.execute(
                """SELECT model_config_id,failure_reason
                FROM manual_screenshot_interpretation_revisions
                WHERE status='failed' AND failure_reason IS NOT NULL
                ORDER BY created_at DESC"""
            ).fetchall()
        rejected_reason = {}
        for row in reasons:
            if (row["model_config_id"] not in rejected_reason
                    and self._is_false_vision_capability_error(row["failure_reason"])):
                rejected_reason[row["model_config_id"]] = row["failure_reason"]
        changed = 0
        for row in rows:
            reason = rejected_reason.get(row["id"])
            if reason and self._disable_false_vision_capability(dict(row), reason):
                changed += 1
            if reason:
                friendly = ("模型“{0}”不支持图片输入，已自动关闭其图片能力；"
                            "请在截图页选择真正的视觉模型").format(row["model_name"])
                with self._database.connect() as connection:
                    connection.execute(
                        """UPDATE manual_project_screenshot_assets
                        SET failure_reason=?,updated_at=? WHERE analysis_status='failed'
                        AND id IN (SELECT asset_id FROM
                        manual_screenshot_interpretation_revisions
                        WHERE model_config_id=? AND status='failed')""",
                        (friendly, utc_now(), row["id"]),
                    )
        return changed

    def prepare_profile(self, task_id: str) -> dict:
        self._database.initialize()
        task = self._task(task_id)
        with self._database.connect() as connection:
            research = connection.execute(
                """SELECT r.* FROM manual_research_artifacts r
                JOIN manual_generation_jobs j ON j.id=r.job_id
                WHERE j.task_id=? ORDER BY r.created_at DESC,r.version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            facts = connection.execute(
                """SELECT fact_key,value_json,confidence FROM facts
                WHERE task_id=? ORDER BY confidence DESC,fact_key LIMIT 80""", (task_id,),
            ).fetchall()
            evidence = connection.execute(
                """SELECT e.id,e.kind,e.relative_path,e.locator_json,e.excerpt
                FROM evidence e JOIN tasks t ON t.snapshot_id=e.snapshot_id
                WHERE t.id=? ORDER BY e.id LIMIT 80""", (task_id,),
            ).fetchall()
        project_profile = json.loads(research["project_profile_json"] or "{}") if research else {}
        notes_payload = json.loads(research["notes_json"] or "[]") if research else []
        if isinstance(notes_payload, dict):
            notes = notes_payload.get("research_notes") or notes_payload.get("notes") or []
        else:
            notes = notes_payload
        if not isinstance(notes, list):
            notes = [str(notes)]
        fact_items = [{"key": row["fact_key"], "value": json.loads(row["value_json"]),
                       "confidence": row["confidence"]} for row in facts]
        evidence_items = [{"id": row["id"], "kind": row["kind"],
                           "relative_path": row["relative_path"],
                           "locator": json.loads(row["locator_json"] or "{}"),
                           "excerpt": row["excerpt"]} for row in evidence]
        serialized = json.dumps({"profile": project_profile, "facts": fact_items},
                                ensure_ascii=False).lower()
        profile = {
            "software_name": project_profile.get("software_name") or task["display_name"],
            "purpose": project_profile.get("purpose") or project_profile.get("software_purpose") or "待确认",
            "target_users": self._pick(project_profile, "target_users", "users", "user_roles"),
            "user_roles": self._pick(project_profile, "user_roles", "roles"),
            "core_modules": self._pick(project_profile, "core_modules", "modules"),
            "technology_stack": self._pick(project_profile, "technology_stack", "tech_stack", "frameworks"),
            "page_route_clues": self._fact_values(fact_items, ("route", "page", "screen"), 18),
            "main_operations": self._fact_values(fact_items, ("operation", "workflow", "feature"), 16),
            "source_evidence": evidence_items[:30],
            "related_api_component_evidence": [item for item in evidence_items if any(
                marker in json.dumps(item, ensure_ascii=False).lower()
                for marker in ("api", "route", "component", "controller", "page", "view")
            )][:24],
            "unconfirmed": notes[:12] or (["目标用户、页面状态和真实业务数据仍需结合截图确认"]
                                          if "frontend" in serialized or "vue" in serialized or
                                          "react" in serialized else []),
        }
        fingerprint = hashlib.sha256(encode_json(profile).encode()).hexdigest()
        with self._database.connect() as connection:
            latest = connection.execute(
                """SELECT * FROM manual_project_profile_revisions
                WHERE task_id=? ORDER BY version DESC LIMIT 1""", (task_id,),
            ).fetchone()
            if latest and latest["fingerprint"] == fingerprint:
                result = self._profile_dict(latest)
                self._ensure_legacy_interpretations(task_id, result)
                return result
            version = 1 if latest is None else latest["version"] + 1
            profile_id, now = str(uuid4()), utc_now()
            connection.execute(
                """INSERT INTO manual_project_profile_revisions(
                id,task_id,version,research_artifact_id,origin,profile_json,fingerprint,created_at)
                VALUES(?,?,?,?,'research',?,?,?)""",
                (profile_id, task_id, version, research["id"] if research else None,
                 encode_json(profile), fingerprint, now),
            )
            self._mark_analysis_outdated(connection, task_id)
        result = {"id": profile_id, "task_id": task_id, "version": version,
                "origin": "research", "profile": profile, "fingerprint": fingerprint,
                "created_at": now}
        self._ensure_legacy_interpretations(task_id, result)
        return result

    def save_profile(self, task_id: str, profile: dict) -> dict:
        self._task(task_id)
        clean = self._normalize_profile(profile)
        fingerprint = hashlib.sha256(encode_json(clean).encode()).hexdigest()
        now, profile_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            latest = connection.execute(
                """SELECT version,fingerprint FROM manual_project_profile_revisions
                WHERE task_id=? ORDER BY version DESC LIMIT 1""", (task_id,),
            ).fetchone()
            if latest and latest["fingerprint"] == fingerprint:
                raise ScreenshotEvidenceError("项目概要没有变化")
            version = 1 if latest is None else latest["version"] + 1
            connection.execute(
                """INSERT INTO manual_project_profile_revisions(
                id,task_id,version,research_artifact_id,origin,profile_json,fingerprint,created_at)
                VALUES(?,?,?,NULL,'user',?,?,?)""",
                (profile_id, task_id, version, encode_json(clean), fingerprint, now),
            )
            self._mark_analysis_outdated(connection, task_id)
        return {"id": profile_id, "task_id": task_id, "version": version,
                "origin": "user", "profile": clean, "fingerprint": fingerprint,
                "created_at": now}

    def get_ui_decision(self, task_id: str) -> dict:
        self._task(task_id)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM manual_ui_evidence_decisions WHERE task_id=?
                ORDER BY version DESC LIMIT 1""", (task_id,),
            ).fetchone()
        if row is None:
            return {"task_id": task_id, "version": 0,
                    "decision": "waiting_for_screenshots", "reason": "", "created_at": None}
        return dict(row)

    def set_ui_decision(self, task_id: str, decision: str, reason: str) -> dict:
        self._task(task_id)
        allowed = {"waiting_for_screenshots", "source_inferred", "not_applicable"}
        if decision not in allowed:
            raise ScreenshotEvidenceError("用户界面证据决策无效")
        reason = reason.strip()[:1000]
        if decision != "waiting_for_screenshots" and len(reason) < 4:
            raise ScreenshotEvidenceError("选择跳过真实截图时必须填写明确原因")
        now, decision_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value
                FROM manual_ui_evidence_decisions WHERE task_id=?""", (task_id,),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO manual_ui_evidence_decisions(
                id,task_id,version,decision,reason,created_at) VALUES(?,?,?,?,?,?)""",
                (decision_id, task_id, version, decision, reason, now),
            )
        self._mark_ui_outdated(task_id)
        return {"id": decision_id, "task_id": task_id, "version": version,
                "decision": decision, "reason": reason, "created_at": now}

    def import_batch(self, task_id: str, paths: list, source: str = "user",
                     job_id: Optional[str] = None) -> dict:
        self._task(task_id)
        if source not in {"user", "clipboard", "folder", "automated"}:
            raise ScreenshotEvidenceError("截图来源无效")
        if not paths:
            raise ScreenshotEvidenceError("没有选择任何图片")
        normalized = sorted({str(Path(value).expanduser().resolve()) for value in paths},
                            key=self._natural_key)
        batch_id, now = str(uuid4()), utc_now()
        if job_id:
            self._assert_job(task_id, job_id)
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_screenshot_import_batches(
                id,task_id,job_id,source,status,input_count,summary_json,created_at,started_at)
                VALUES(?,?,?,?, 'running',?, '{}',?,?)""",
                (batch_id, task_id, job_id, source, len(normalized), now, now),
            )
        node = self._prepare_node(job_id, "screenshot-import:" + batch_id,
                                  "screenshot_import", "截图批次导入")
        results, imported, warnings, failures = [], 0, 0, 0
        for order, value in enumerate(normalized, 1):
            path = Path(value)
            try:
                item = self._import_one(task_id, batch_id, path, source, order)
                results.append({"path": value, "status": "imported", "asset": item})
                imported += 1
            except ScreenshotEvidenceError as error:
                result_status = "warning" if "过小" in str(error) or "重复" in str(error) else "failed"
                results.append({"path": value, "status": result_status, "message": str(error)})
                if result_status == "warning": warnings += 1
                else: failures += 1
        finished = utc_now()
        status = ("failed" if not imported else
                  "completed_with_warnings" if warnings or failures else "completed")
        summary = {"results": results, "input_count": len(normalized),
                   "imported_count": imported, "warning_count": warnings,
                   "failure_count": failures}
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_screenshot_import_batches SET status=?,imported_count=?,
                warning_count=?,failure_count=?,summary_json=?,finished_at=? WHERE id=?""",
                (status, imported, warnings, failures, encode_json(summary), finished, batch_id),
            )
        if node:
            execution = ManualExecutionNodeService(self._database)
            if imported:
                execution.complete(job_id, node, summary, warnings=bool(warnings or failures))
            else:
                execution.fail(job_id, node, "本批次没有可导入图片", "screenshot_import")
        return {"id": batch_id, "task_id": task_id, "job_id": job_id, "source": source,
                "status": status, **summary, "created_at": now, "finished_at": finished}

    def import_folder(self, task_id: str, folder: Path, recursive: bool = False,
                      job_id: Optional[str] = None) -> dict:
        root = folder.expanduser().resolve()
        if not root.is_dir():
            raise ScreenshotEvidenceError("所选文件夹不存在")
        iterator = root.rglob("*") if recursive else root.iterdir()
        paths = [str(path) for path in iterator
                 if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS]
        if len(paths) > 500:
            raise ScreenshotEvidenceError("单批最多导入 500 张图片")
        if not paths:
            raise ScreenshotEvidenceError("文件夹中没有 PNG、JPG、JPEG 或 WebP 图片")
        return self.import_batch(task_id, paths, "folder", job_id)

    def import_base64(self, task_id: str, data: str, filename: str,
                      job_id: Optional[str] = None, source: str = "clipboard") -> dict:
        self._task(task_id)
        try:
            payload = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ScreenshotEvidenceError("剪贴板图片编码无效") from error
        if not payload or len(payload) > MAX_IMAGE_BYTES:
            raise ScreenshotEvidenceError("剪贴板图片为空或超过 20MB 限制")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            suffix = ".png"
        staging = self._data_root / "tasks" / task_id / "artifacts" / "manual" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="clipboard-", suffix=suffix,
                                         dir=str(staging), delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        try:
            return self.import_batch(task_id, [str(temporary)], source, job_id)
        finally:
            temporary.unlink(missing_ok=True)

    def list_assets(self, task_id: str, include_archived: bool = False) -> list:
        self._task(task_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT a.*,r.id revision_id,r.version revision_version,
                i.id interpretation_id,i.version interpretation_version,
                i.interpretation_json,i.reviewed interpretation_reviewed,
                i.project_profile_revision_id,i.model_name interpretation_model,
                i.elapsed_ms interpretation_elapsed_ms,i.attempt_count interpretation_attempts
                FROM manual_project_screenshot_assets a
                JOIN manual_project_screenshot_revisions r ON r.asset_id=a.id
                    AND r.version=(SELECT MAX(version) FROM manual_project_screenshot_revisions x
                        WHERE x.asset_id=a.id)
                LEFT JOIN manual_screenshot_interpretation_revisions i ON i.asset_id=a.id
                    AND i.version=(SELECT MAX(version) FROM manual_screenshot_interpretation_revisions y
                        WHERE y.asset_id=a.id)
                WHERE a.task_id=? AND (?=1 OR a.archived_at IS NULL)
                ORDER BY a.group_key,a.sort_order,a.created_at""",
                (task_id, 1 if include_archived else 0),
            ).fetchall()
        return [self._asset_dict(row) for row in rows]

    def read_image(self, task_id: str, asset_id: str) -> bytes:
        task_root = (self._data_root / "tasks" / task_id).resolve()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT image_relative_path FROM manual_project_screenshot_assets
                WHERE id=? AND task_id=?""", (asset_id, task_id),
            ).fetchone()
        if row is None:
            raise ScreenshotEvidenceError("截图不存在")
        path = (task_root / row["image_relative_path"]).resolve()
        if task_root not in path.parents or not path.is_file():
            raise ScreenshotEvidenceError("截图文件缺失或路径无效")
        return path.read_bytes()

    def replace_image(self, task_id: str, asset_id: str, path: Path) -> dict:
        """Create a new immutable image revision and invalidate dependent analysis."""
        asset = self._asset_row(task_id, asset_id)
        source_path = path.expanduser().resolve()
        if source_path.suffix.lower() not in ALLOWED_EXTENSIONS or not source_path.is_file():
            raise ScreenshotEvidenceError("替换图片必须是现有 PNG、JPG、JPEG 或 WebP 文件")
        if source_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ScreenshotEvidenceError("图片超过 20MB 限制")
        try:
            with Image.open(source_path) as opened:
                opened.load()
                width, height = opened.size
                if width > MAX_DIMENSION or height > MAX_DIMENSION:
                    raise ScreenshotEvidenceError("图片尺寸超过安全限制")
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    raise ScreenshotEvidenceError(
                        "图片过小（至少 {0}×{1}）".format(MIN_WIDTH, MIN_HEIGHT))
                image = opened.convert("RGB")
        except (UnidentifiedImageError, OSError) as error:
            raise ScreenshotEvidenceError("图片损坏或无法识别") from error
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        duplicate = self._duplicate(task_id, digest, image, exclude_asset_id=asset_id)
        if duplicate:
            raise ScreenshotEvidenceError("与已有截图“{0}”重复或高度相似".format(duplicate))
        with self._database.connect() as connection:
            previous = connection.execute(
                """SELECT id,version FROM manual_project_screenshot_revisions
                WHERE asset_id=? ORDER BY version DESC LIMIT 1""", (asset_id,),
            ).fetchone()
        version = int(previous["version"]) + 1
        relative = (Path("artifacts/manual/screenshots/project") / asset["asset_key"] /
                    ("v{0}.png".format(version)))
        target = self._image_path(task_id, relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, "PNG", optimize=True)
        file_sha, now, revision_id = hashlib.sha256(target.read_bytes()).hexdigest(), utc_now(), str(uuid4())
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_project_screenshot_revisions(
                id,asset_id,version,title,image_relative_path,width,height,image_format,sha256,
                edit_source,parent_revision_id,created_at)
                VALUES(?,?,?,?,?,?,?,'PNG',?,'replacement',?,?)""",
                (revision_id, asset_id, version, asset["title"], relative.as_posix(), width,
                 height, file_sha, previous["id"], now),
            )
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET image_relative_path=?,width=?,
                height=?,image_format='PNG',sha256=?,analysis_status='outdated',
                review_status='pending',adoption_status='pending',sensitive_status='unreviewed',
                failure_reason=NULL,updated_at=? WHERE id=?""",
                (relative.as_posix(), width, height, file_sha, now, asset_id),
            )
        self._mark_ui_outdated(task_id)
        return next(item for item in self.list_assets(task_id) if item["id"] == asset_id)

    def history(self, task_id: str, asset_id: str) -> dict:
        self._asset_row(task_id, asset_id)
        with self._database.connect() as connection:
            images = connection.execute(
                """SELECT id,version,title,image_relative_path,width,height,image_format,
                sha256,edit_source,parent_revision_id,created_at
                FROM manual_project_screenshot_revisions WHERE asset_id=?
                ORDER BY version DESC""", (asset_id,),
            ).fetchall()
            interpretations = connection.execute(
                """SELECT id,version,asset_revision_id,project_profile_revision_id,
                model_name,prompt_version,status,interpretation_json,origin,reviewed,
                attempt_count,elapsed_ms,failure_reason,created_at
                FROM manual_screenshot_interpretation_revisions WHERE asset_id=?
                ORDER BY version DESC""", (asset_id,),
            ).fetchall()
        return {"asset_id": asset_id, "image_revisions": [dict(row) for row in images],
                "interpretation_revisions": [{**dict(row),
                    "interpretation": json.loads(row["interpretation_json"] or "{}")}
                    for row in interpretations]}

    def rollback(self, task_id: str, asset_id: str, *, image_version: Optional[int] = None,
                 interpretation_version: Optional[int] = None) -> dict:
        asset = self._asset_row(task_id, asset_id)
        if image_version is None and interpretation_version is None:
            raise ScreenshotEvidenceError("至少选择一个要恢复的图片或解读版本")
        profile = self.prepare_profile(task_id)
        now = utc_now()
        with self._database.connect() as connection:
            latest_image = connection.execute(
                """SELECT * FROM manual_project_screenshot_revisions WHERE asset_id=?
                ORDER BY version DESC LIMIT 1""", (asset_id,),
            ).fetchone()
            current_image = latest_image
            if image_version is not None:
                source_image = connection.execute(
                    """SELECT * FROM manual_project_screenshot_revisions
                    WHERE asset_id=? AND version=?""", (asset_id, image_version),
                ).fetchone()
                if source_image is None:
                    raise ScreenshotEvidenceError("要恢复的图片版本不存在")
                if not self._image_path(task_id, source_image["image_relative_path"]).is_file():
                    raise ScreenshotEvidenceError("要恢复的历史图片文件缺失")
                revision_id = str(uuid4())
                connection.execute(
                    """INSERT INTO manual_project_screenshot_revisions(
                    id,asset_id,version,title,image_relative_path,width,height,image_format,sha256,
                    edit_source,parent_revision_id,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'rollback',?,?)""",
                    (revision_id, asset_id, latest_image["version"] + 1, source_image["title"],
                     source_image["image_relative_path"], source_image["width"],
                     source_image["height"], source_image["image_format"], source_image["sha256"],
                     latest_image["id"], now),
                )
                current_image = {**dict(source_image), "id": revision_id,
                                 "version": latest_image["version"] + 1}
            restored_interpretation = None
            if interpretation_version is not None:
                source_interpretation = connection.execute(
                    """SELECT * FROM manual_screenshot_interpretation_revisions
                    WHERE asset_id=? AND version=? AND status='completed'""",
                    (asset_id, interpretation_version),
                ).fetchone()
                if source_interpretation is None:
                    raise ScreenshotEvidenceError("要恢复的解读版本不存在或不是成功版本")
                version = connection.execute(
                    """SELECT COALESCE(MAX(version),0)+1 value
                    FROM manual_screenshot_interpretation_revisions WHERE asset_id=?""",
                    (asset_id,),
                ).fetchone()["value"]
                interpretation_id = str(uuid4())
                connection.execute(
                    """INSERT INTO manual_screenshot_interpretation_revisions(
                    id,asset_id,asset_revision_id,project_profile_revision_id,version,
                    model_config_id,model_name,prompt_version,cache_key,status,
                    interpretation_json,origin,reviewed,attempt_count,elapsed_ms,created_at)
                    VALUES(?,?,?,?,?,NULL,'历史版本恢复',?,'','completed',?,'user',1,1,0,?)""",
                    (interpretation_id, asset_id, current_image["id"], profile["id"], version,
                     PROMPT_VERSION, source_interpretation["interpretation_json"], now),
                )
                restored_interpretation = json.loads(source_interpretation["interpretation_json"])
            title = ((restored_interpretation or {}).get("page_title") or
                     current_image["title"] or asset["title"])
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET title=?,image_relative_path=?,
                width=?,height=?,image_format=?,sha256=?,analysis_status=?,review_status=?,
                adoption_status='pending',sensitive_status=CASE WHEN ?=1 THEN 'unreviewed'
                ELSE sensitive_status END,failure_reason=NULL,updated_at=? WHERE id=?""",
                (title, current_image["image_relative_path"], current_image["width"],
                 current_image["height"], current_image["image_format"], current_image["sha256"],
                 "completed" if restored_interpretation else "outdated",
                 "reviewed" if restored_interpretation else "pending",
                 1 if image_version is not None else 0, now, asset_id),
            )
        self._mark_ui_outdated(task_id)
        return next(item for item in self.list_assets(task_id) if item["id"] == asset_id)

    def analyze_many(self, task_id: str, asset_ids: list, model_config_id: str,
                     job_id: Optional[str] = None) -> dict:
        profile = self.prepare_profile(task_id)
        capability = self.model_capability(model_config_id)
        if capability["status"] != "supported":
            raise ScreenshotEvidenceError(capability["message"])
        assets = {item["id"]: item for item in self.list_assets(task_id)}
        selected = [assets[value] for value in asset_ids if value in assets]
        if not selected:
            raise ScreenshotEvidenceError("没有可分析的截图")
        concurrency = min(4, AppSettingsService(self._database).effective_concurrency(
            model_config_id))
        results = []
        with ThreadPoolExecutor(max_workers=concurrency,
                                thread_name_prefix="screenshot-analysis") as executor:
            futures = {executor.submit(self._analyze_one, task_id, asset, profile,
                                       model_config_id, job_id): asset for asset in selected}
            for future in as_completed(futures):
                asset = futures[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    results.append({"asset_id": asset["id"], "status": "failed",
                                    "message": str(error)})
        return {"task_id": task_id, "profile_version": profile["version"],
                "model_config_id": model_config_id, "concurrency": concurrency,
                "completed": sum(item.get("status") == "completed" for item in results),
                "failed": sum(item.get("status") == "failed" for item in results),
                "results": results}

    def retry_analysis(self, task_id: str, asset_id: str, model_config_id: str,
                       job_id: Optional[str] = None) -> dict:
        return self.analyze_many(task_id, [asset_id], model_config_id, job_id)["results"][0]

    def review(self, task_id: str, asset_id: str, interpretation: dict, *,
               adopted: bool, group_title: str, sort_order: int,
               sensitive_status: str = "confirmed_safe") -> dict:
        normalized = self._normalize_interpretation(interpretation)
        if sensitive_status not in {"unreviewed", "confirmed_safe", "contains_sensitive"}:
            raise ScreenshotEvidenceError("敏感信息审核状态无效")
        if adopted and sensitive_status != "confirmed_safe":
            raise ScreenshotEvidenceError("采用截图前必须确认图片不含需要处理的敏感信息")
        asset, profile = self._asset_row(task_id, asset_id), self.prepare_profile(task_id)
        now, revision_id = utc_now(), str(uuid4())
        group_title = group_title.strip()
        if self._generic_group(group_title):
            group_title = normalized["suggested_group"] or "未分组页面"
        group_key = self._slug(group_title)
        with self._database.connect() as connection:
            latest = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value
                FROM manual_screenshot_interpretation_revisions WHERE asset_id=?""",
                (asset_id,),
            ).fetchone()["value"]
            asset_revision = connection.execute(
                """SELECT id FROM manual_project_screenshot_revisions
                WHERE asset_id=? ORDER BY version DESC LIMIT 1""", (asset_id,),
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO manual_screenshot_interpretation_revisions(
                id,asset_id,asset_revision_id,project_profile_revision_id,version,
                model_config_id,model_name,prompt_version,cache_key,status,
                interpretation_json,origin,reviewed,attempt_count,elapsed_ms,created_at)
                VALUES(?,?,?,?,?,NULL,'人工修订',?,'','completed',?,'user',1,1,0,?)""",
                (revision_id, asset_id, asset_revision, profile["id"], latest,
                 PROMPT_VERSION, encode_json(normalized), now),
            )
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET title=?,analysis_status='completed',
                review_status='reviewed',adoption_status=?,group_key=?,group_title=?,sort_order=?,
                sensitive_status=?,failure_reason=NULL,updated_at=? WHERE id=?""",
                (normalized["page_title"] or asset["title"],
                 "adopted" if adopted else "pending", group_key, group_title,
                 max(0, int(sort_order)), sensitive_status, now, asset_id),
            )
        self._mark_ui_outdated(task_id)
        return next(item for item in self.list_assets(task_id) if item["id"] == asset_id)

    def set_adoption(self, task_id: str, asset_ids: list, adopted: bool) -> list:
        return self.set_adoption_status(
            task_id, asset_ids, "adopted" if adopted else "excluded")

    def set_adoption_status(self, task_id: str, asset_ids: list, status: str) -> list:
        if status not in {"pending", "adopted", "excluded"}:
            raise ScreenshotEvidenceError("截图采用状态无效")
        now = utc_now()
        with self._database.connect() as connection:
            for asset_id in asset_ids:
                row = connection.execute(
                    """SELECT review_status,sensitive_status FROM manual_project_screenshot_assets
                    WHERE id=? AND task_id=?""", (asset_id, task_id),
                ).fetchone()
                if row is None:
                    continue
                if status == "adopted" and row["review_status"] != "reviewed":
                    raise ScreenshotEvidenceError("采用截图前必须先审核其结构化解读")
                if status == "adopted" and row["sensitive_status"] != "confirmed_safe":
                    raise ScreenshotEvidenceError("采用截图前必须确认图片不含需要处理的敏感信息")
                connection.execute(
                    """UPDATE manual_project_screenshot_assets SET adoption_status=?,
                    review_status=CASE WHEN ?='pending' THEN 'pending'
                    WHEN ?='excluded' THEN 'rejected' ELSE review_status END,updated_at=?
                    WHERE id=?""", (status, status, status, now, asset_id),
                )
        self._mark_ui_outdated(task_id)
        return self.list_assets(task_id)

    def snapshot_for_job(self, job_id: str) -> dict:
        with self._database.connect() as connection:
            job = connection.execute(
                "SELECT task_id FROM manual_generation_jobs WHERE id=?", (job_id,),
            ).fetchone()
        if job is None:
            raise ScreenshotEvidenceError("说明书任务不存在")
        task_id = job["task_id"]
        profile = self.prepare_profile(task_id)
        adopted = []
        for item in self.list_assets(task_id):
            if item["adoption_status"] != "adopted" or item["archived"]:
                continue
            normalized = dict(item)
            if self._generic_group(normalized["group_title"]):
                suggested = str((normalized.get("interpretation") or {}).get(
                    "suggested_group") or "").strip()
                normalized["group_title"] = suggested or "界面说明"
                normalized["group_key"] = self._slug(normalized["group_title"])
            adopted.append(normalized)
        invalid = [item["title"] for item in adopted if
                   item["review_status"] != "reviewed" or not item["interpretation_id"] or
                   not item["interpretation_reviewed"] or
                   item["analysis_status"] in {"outdated", "failed"} or
                   item["sensitive_status"] != "confirmed_safe"]
        if invalid:
            raise ScreenshotEvidenceError(
                "以下采用截图没有最新已审核解读：" + "、".join(invalid[:6]))
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute("DELETE FROM manual_job_screenshot_refs WHERE job_id=?", (job_id,))
            for item in adopted:
                connection.execute(
                    """INSERT INTO manual_job_screenshot_refs(
                    id,job_id,asset_id,asset_revision_id,interpretation_revision_id,
                    group_key,group_title,sort_order,adopted_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()), job_id, item["id"], item["revision_id"],
                     item["interpretation_id"], item["group_key"], item["group_title"],
                     item["sort_order"], now),
                )
        return {"task_id": task_id, "profile": profile,
                "screenshots": sorted(adopted, key=lambda item: (
                    item["sort_order"], item["created_at"], item["group_title"])),
                "adopted_at": now}

    def record_ui_sources(self, job_id: str, section_revision_id: str,
                          profile_revision_id: str, screenshots: list) -> dict:
        sources = [{"asset_id": item["id"], "asset_revision_id": item["revision_id"],
                    "interpretation_revision_id": item["interpretation_id"],
                    "group_key": item["group_key"], "sort_order": item["sort_order"]}
                   for item in screenshots]
        adopted_hash = hashlib.sha256(encode_json(sources).encode()).hexdigest()
        now, source_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_ui_section_sources(
                id,job_id,section_revision_id,project_profile_revision_id,
                adopted_set_hash,sources_json,created_at) VALUES(?,?,?,?,?,?,?)""",
                (source_id, job_id, section_revision_id, profile_revision_id,
                 adopted_hash, encode_json(sources), now),
            )
        return {"id": source_id, "adopted_set_hash": adopted_hash,
                "source_count": len(sources), "created_at": now}

    def ui_update_required(self, job_id: str) -> bool:
        """Return whether the reviewed adoption set differs from chapter 7's source set."""
        with self._database.connect() as connection:
            job = connection.execute(
                "SELECT task_id FROM manual_generation_jobs WHERE id=?", (job_id,)
            ).fetchone()
            latest = connection.execute(
                """SELECT adopted_set_hash FROM manual_ui_section_sources
                WHERE job_id=? ORDER BY created_at DESC LIMIT 1""", (job_id,)
            ).fetchone()
        if job is None:
            raise ScreenshotEvidenceError("说明书任务不存在")
        adopted = []
        for item in self.list_assets(job["task_id"]):
            if item["adoption_status"] != "adopted" or item["archived"]:
                continue
            normalized = dict(item)
            if self._generic_group(normalized["group_title"]):
                suggested = str((normalized.get("interpretation") or {}).get(
                    "suggested_group") or "").strip()
                normalized["group_title"] = suggested or "界面说明"
                normalized["group_key"] = self._slug(normalized["group_title"])
            adopted.append(normalized)
        if not adopted:
            return False
        adopted.sort(key=lambda item: (
            item["sort_order"], item["created_at"], item["group_title"]
        ))
        sources = [{"asset_id": item["id"], "asset_revision_id": item["revision_id"],
                    "interpretation_revision_id": item["interpretation_id"],
                    "group_key": item["group_key"], "sort_order": item["sort_order"]}
                   for item in adopted]
        current_hash = hashlib.sha256(encode_json(sources).encode()).hexdigest()
        return latest is None or latest["adopted_set_hash"] != current_hash

    def model_capability(self, model_config_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT id,name,model_name,settings_json,enabled FROM model_configs
                WHERE id=?""", (model_config_id,),
            ).fetchone()
        if row is None or not row["enabled"]:
            raise ScreenshotEvidenceError("模型不存在或已停用")
        settings = json.loads(row["settings_json"] or "{}")
        explicit = settings.get("supports_vision")
        verified = settings.get("vision_capability_verification") or {}
        if explicit is True and verified.get("passed") is True:
            return {"id": row["id"], "name": row["name"], "model_name": row["model_name"],
                    "status": "supported", "confirmed": True,
                    "message": "模型已通过真实测试图识别验证"}
        if explicit is True:
            return {"id": row["id"], "name": row["name"], "model_name": row["model_name"],
                    "status": "unknown", "confirmed": False,
                    "message": "旧图片能力标记尚未通过测试图验证；请在设置中重新验证"}
        if explicit is False:
            provider_error = settings.get("vision_capability_error")
            return {"id": row["id"], "name": row["name"], "model_name": row["model_name"],
                    "status": "unsupported", "confirmed": True,
                    "message": ("供应商已确认该模型不支持图片输入；请在设置中选择真正的视觉模型"
                                if provider_error else "该模型配置明确不支持图片输入")}
        name = row["model_name"].lower()
        known = any(marker in name for marker in (
            "gpt-4o", "gpt-4.1", "gpt-5", "claude-3", "claude-4", "gemini",
            "qwen-vl", "qwen2.5-vl", "llava", "vision", "pixtral", "glm-4v", "minicpm-v",
        ))
        return {"id": row["id"], "name": row["name"], "model_name": row["model_name"],
                "status": "unknown", "confirmed": False,
                "message": ("模型名称可能支持图片，但必须先通过随机测试图验证"
                            if known else "无法确认图片能力；请在设置中执行随机测试图验证")}

    def list_vision_models(self) -> list:
        with self._database.connect() as connection:
            rows = connection.execute("SELECT id FROM model_configs WHERE enabled=1").fetchall()
        capabilities = [self.model_capability(row["id"]) for row in rows]
        return [item for item in capabilities if item["status"] == "supported"]

    def verify_vision_capability(self, model_config_id: str) -> dict:
        """Run an answer-hidden image challenge before enabling a vision model."""
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_configs WHERE id=? AND enabled=1",
                (model_config_id,),
            ).fetchone()
        if row is None:
            raise ScreenshotEvidenceError("模型不存在或已停用")
        code = secrets.token_hex(2).upper()
        image = Image.new("RGB", (360, 180), "#f8fafc")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 72)
        except OSError:
            font = ImageFont.load_default(size=64)
        draw.rounded_rectangle((18, 18, 342, 162), radius=18, fill="#ffffff",
                               outline="#274c77", width=5)
        draw.text((95, 47), code, fill="#13293d", font=font)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        prompt = ("这是图片输入能力验证。读取图片中央边框内的四个大写字符。"
                  "只返回 JSON：{\"code\":\"你实际看到的四个字符\"}。"
                  "不得猜测；看不到图片时返回 {\"code\":null}。")
        started = time.monotonic()
        try:
            raw = self._vision_request(dict(row), buffer.getvalue(), prompt)
            match = re.search(r'"code"\s*:\s*"([A-F0-9]{4})"', raw or "", re.IGNORECASE)
            if not match or match.group(1).upper() != code:
                raise ScreenshotEvidenceError("模型未正确读取随机测试图中的字符")
        except Exception as error:
            self._record_vision_verification(dict(row), False, str(error))
            raise ScreenshotEvidenceError("图片能力验证失败：{0}".format(error)) from error
        elapsed = round((time.monotonic() - started) * 1000)
        self._record_vision_verification(dict(row), True, "随机图片字符识别通过", elapsed)
        return {"verified": True, "elapsed_ms": elapsed,
                "message": "随机测试图识别通过，图片能力已开启"}

    def _record_vision_verification(self, config: dict, passed: bool, detail: str,
                                    elapsed_ms: int = 0) -> None:
        settings = json.loads(config["settings_json"] or "{}")
        settings["supports_vision"] = passed
        settings["vision_capability_checked_at"] = utc_now()
        settings["vision_capability_verification"] = {
            "passed": passed, "kind": "random_image_ocr", "elapsed_ms": elapsed_ms,
            "detail": detail[:300],
        }
        if passed:
            settings.pop("vision_capability_error", None)
        else:
            settings["vision_capability_error"] = detail[:300]
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE model_configs SET settings_json=?,updated_at=? WHERE id=?",
                (json.dumps(settings, ensure_ascii=False, separators=(",", ":")),
                 utc_now(), config["id"]),
            )

    def _analyze_one(self, task_id: str, asset: dict, profile: dict,
                     model_config_id: str, job_id: Optional[str]) -> dict:
        node_key = "screenshot-analysis:" + asset["id"]
        with self._database.connect() as connection:
            current = connection.execute(
                "SELECT analysis_status FROM manual_project_screenshot_assets WHERE id=?",
                (asset["id"],),
            ).fetchone()
        if current is not None and current["analysis_status"] == "running":
            raise ScreenshotEvidenceError("该截图已在分析中，请勿重复启动")
        node = self._prepare_node(job_id, node_key, "screenshot_analysis",
                                  "AI 解读：" + asset["title"], model_config_id)
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET analysis_status='running',
                failure_reason=NULL,updated_at=? WHERE id=?""", (utc_now(), asset["id"]),
            )
            revision = connection.execute(
                """SELECT * FROM manual_project_screenshot_revisions WHERE asset_id=?
                ORDER BY version DESC LIMIT 1""", (asset["id"],),
            ).fetchone()
            config = connection.execute(
                """SELECT * FROM model_configs WHERE id=? AND enabled=1""",
                (model_config_id,),
            ).fetchone()
        analysis_context = self._analysis_context(task_id, asset)
        cache_key = hashlib.sha256(encode_json({
            "sha256": revision["sha256"], "profile": profile["fingerprint"],
            "model": model_config_id, "prompt": PROMPT_VERSION,
            "related_context": analysis_context["fingerprint"],
        }).encode()).hexdigest()
        with self._database.connect() as connection:
            cached = connection.execute(
                """SELECT * FROM manual_screenshot_interpretation_revisions
                WHERE cache_key=? AND status='completed' ORDER BY created_at DESC LIMIT 1""",
                (cache_key,),
            ).fetchone()
        if cached:
            normalized = json.loads(cached["interpretation_json"])
            result = self._persist_interpretation(asset, revision, profile, config, cache_key,
                                                  normalized, 0, 0)
            if node:
                ManualExecutionNodeService(self._database).complete(
                    job_id, node_key, {"cache_hit": True, "version": result["version"],
                                       "model": config["model_name"]})
            return {"asset_id": asset["id"], "status": "completed", "cache_hit": True,
                    "interpretation": normalized, "version": result["version"]}
        started, attempts = time.monotonic(), 1
        try:
            prompt = self._analysis_prompt(profile["profile"], asset, analysis_context)
            raw = self._vision_call(dict(config), revision["image_relative_path"], task_id,
                                    prompt, analysis_context["related_image_paths"])
            try:
                normalized = self._normalize_interpretation(self._parse_json(raw))
            except ScreenshotEvidenceError as first_error:
                attempts = 2
                repair = (prompt + "\n\n上一次输出未通过 JSON 校验：" + str(first_error) +
                          "。只返回修复后的单个 JSON 对象，不要解释，不要重新推断图片之外的内容。")
                raw = self._vision_call(dict(config), revision["image_relative_path"],
                                        task_id, repair, analysis_context["related_image_paths"])
                normalized = self._normalize_interpretation(self._parse_json(raw))
            elapsed = round((time.monotonic() - started) * 1000)
            result = self._persist_interpretation(asset, revision, profile, config, cache_key,
                                                  normalized, attempts, elapsed)
            if node:
                ManualExecutionNodeService(self._database).complete(job_id, node_key, {
                    "version": result["version"], "model": config["model_name"],
                    "elapsed_ms": elapsed, "attempts": attempts,
                    "next_action": "审核结构化解读，确认分组、顺序和是否采用",
                })
            return {"asset_id": asset["id"], "status": "completed", "cache_hit": False,
                    "interpretation": normalized, "version": result["version"],
                    "elapsed_ms": elapsed, "attempts": attempts}
        except Exception as error:
            elapsed = round((time.monotonic() - started) * 1000)
            provider_rejected = self._is_false_vision_capability_error(error)
            self._disable_false_vision_capability(dict(config), error)
            if provider_rejected:
                error = ScreenshotEvidenceError(
                    "模型“{0}”不支持图片输入，已自动关闭其图片能力；"
                    "请在截图页选择真正的视觉模型".format(config["model_name"])
                )
            self._persist_failure(asset, revision, profile, config, cache_key,
                                  str(error), attempts, elapsed)
            if node:
                ManualExecutionNodeService(self._database).fail(
                    job_id, node_key, str(error), "screenshot_analysis")
            raise error

    def _disable_false_vision_capability(self, config: dict, error) -> bool:
        """Trust a provider's explicit rejection over a manual capability toggle."""
        if not self._is_false_vision_capability_error(error):
            return False
        settings = json.loads(config["settings_json"] or "{}")
        if settings.get("supports_vision") is False:
            return False
        settings["supports_vision"] = False
        settings["vision_capability_error"] = "供应商已明确拒绝图片输入"
        settings["vision_capability_checked_at"] = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE model_configs SET settings_json=?,updated_at=? WHERE id=?",
                (json.dumps(settings, ensure_ascii=False, separators=(",", ":")),
                 utc_now(), config["id"]),
            )
        return True

    @staticmethod
    def _is_false_vision_capability_error(error) -> bool:
        message = str(error).lower()
        markers = (
            "not a multimodal model", "not multimodal", "does not support image",
            "doesn't support image", "image input is not supported", "vision not supported",
            "不是多模态模型", "不支持图片", "不支持图像",
        )
        return any(marker in message for marker in markers)

    def _vision_call(self, config: dict, relative_path: str, task_id: str, prompt: str,
                     related_paths: Optional[list] = None) -> str:
        if self._model_call:
            return self._model_call(config, prompt, self._image_path(task_id, relative_path))
        image_bytes = self._image_path(task_id, relative_path).read_bytes()
        related_bytes = [self._image_path(task_id, value).read_bytes()
                         for value in (related_paths or [])[:3]]
        return self._vision_request(config, image_bytes, prompt, related_bytes)

    def _vision_request(self, config: dict, image_bytes: bytes, prompt: str,
                        related_images: Optional[list] = None) -> str:
        settings = json.loads(config["settings_json"] or "{}")
        mode = settings.get("endpoint_mode") or ManualGenerationService._default_mode(
            config["protocol_id"])
        try:
            api_key = None if config["protocol_id"] == "ollama" else self._vault.read(
                config["credential_ref"] or config["id"])
        except ValueError as error:
            raise ScreenshotEvidenceError("所选视觉模型的 API Key 不存在") from error
        image_data = base64.b64encode(image_bytes).decode()
        related_data = [base64.b64encode(value).decode()
                        for value in (related_images or [])[:3]]
        data_url = "data:image/png;base64," + image_data
        base, model = config["base_url"].rstrip("/"), config["model_name"]
        headers = {"Content-Type": "application/json"}
        if mode == "messages":
            url = base + "/messages"
            content = [{"type": "text", "text": prompt}, {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": image_data}}]
            for index, value in enumerate(related_data, 1):
                content.extend([{"type": "text", "text": "同组参考截图 {0}（仅用于理解页面关系）".format(index)},
                                {"type": "image", "source": {"type": "base64",
                                 "media_type": "image/png", "data": value}}])
            payload = {"model": model, "max_tokens": 4096,
                       "messages": [{"role": "user", "content": content}]}
            if config["protocol_id"] == "anthropic":
                headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            else:
                headers["Authorization"] = "Bearer " + api_key
        elif mode == "responses":
            url = base + "/responses"; headers["Authorization"] = "Bearer " + api_key
            content = [{"type": "input_text", "text": prompt},
                       {"type": "input_image", "image_url": data_url}]
            content.extend({"type": "input_image", "image_url": "data:image/png;base64," + value}
                           for value in related_data)
            payload = {"model": model, "input": [{"role": "user", "content": content}],
                       "max_output_tokens": 4096}
        elif mode == "ollama_chat":
            url = base + "/api/chat"
            payload = {"model": model, "messages": [{"role": "user", "content": prompt,
                       "images": [image_data] + related_data}], "stream": False}
        else:
            url = base + "/chat/completions"; headers["Authorization"] = "Bearer " + api_key
            content = [{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {"url": data_url}}]
            content.extend({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + value}} for value in related_data)
            payload = {"model": model, "messages": [{"role": "user", "content": content}],
                "max_tokens": 4096, "temperature": 0.1}
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=MODEL_READ_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ScreenshotEvidenceError(
                "视觉模型调用失败（HTTP {0}）：{1}".format(error.code, detail)) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ScreenshotEvidenceError("视觉模型调用失败：{0}".format(error)) from error
        return ManualGenerationService._extract_model_content(mode, result)

    def _persist_interpretation(self, asset, revision, profile, config, cache_key,
                                interpretation, attempts, elapsed) -> dict:
        now, revision_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value
                FROM manual_screenshot_interpretation_revisions WHERE asset_id=?""",
                (asset["id"],),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO manual_screenshot_interpretation_revisions(
                id,asset_id,asset_revision_id,project_profile_revision_id,version,
                model_config_id,model_name,prompt_version,cache_key,status,
                interpretation_json,origin,reviewed,attempt_count,elapsed_ms,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,'completed',?,'ai',0,?,?,?)""",
                (revision_id, asset["id"], revision["id"], profile["id"], version,
                 config["id"], config["model_name"], PROMPT_VERSION, cache_key,
                 encode_json(interpretation), max(1, attempts), elapsed, now),
            )
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET analysis_status='completed',
                review_status='pending',failure_reason=NULL,group_title=CASE WHEN group_title=''
                THEN ? ELSE group_title END,group_key=CASE WHEN group_key='' THEN ? ELSE group_key END,
                sort_order=CASE WHEN sort_order=0 THEN ? ELSE sort_order END,updated_at=? WHERE id=?""",
                (interpretation["suggested_group"], self._slug(interpretation["suggested_group"]),
                 interpretation["suggested_order"], now, asset["id"]),
            )
        return {"id": revision_id, "version": version}

    def _persist_failure(self, asset, revision, profile, config, cache_key,
                         reason, attempts, elapsed) -> None:
        now = utc_now()
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value
                FROM manual_screenshot_interpretation_revisions WHERE asset_id=?""",
                (asset["id"],),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO manual_screenshot_interpretation_revisions(
                id,asset_id,asset_revision_id,project_profile_revision_id,version,
                model_config_id,model_name,prompt_version,cache_key,status,
                interpretation_json,origin,reviewed,attempt_count,elapsed_ms,failure_reason,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,'failed','{}','ai',0,?,?,?,?)""",
                (str(uuid4()), asset["id"], revision["id"], profile["id"], version,
                 config["id"], config["model_name"], PROMPT_VERSION, cache_key,
                 max(1, attempts), elapsed, reason[:500], now),
            )
            connection.execute(
                """UPDATE manual_project_screenshot_assets SET analysis_status='failed',
                failure_reason=?,updated_at=? WHERE id=?""", (reason[:500], now, asset["id"]),
            )

    def _ensure_legacy_interpretations(self, task_id: str, profile: dict) -> None:
        """Lift old six-field descriptions into reviewed structured evidence once."""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT a.id asset_id,a.title,a.group_title,a.sort_order,
                r.id revision_id,msa.description_json
                FROM manual_project_screenshot_assets a
                JOIN manual_project_screenshot_revisions r ON r.asset_id=a.id AND
                    r.version=(SELECT MAX(version) FROM manual_project_screenshot_revisions x
                    WHERE x.asset_id=a.id)
                JOIN manual_screenshot_artifacts msa ON msa.job_id=a.legacy_job_id
                    AND msa.screenshot_key=a.legacy_screenshot_key
                WHERE a.task_id=? AND a.legacy_job_id IS NOT NULL AND NOT EXISTS(
                    SELECT 1 FROM manual_screenshot_interpretation_revisions i
                    WHERE i.asset_id=a.id)""", (task_id,),
            ).fetchall()
            for row in rows:
                old = json.loads(row["description_json"] or "{}")
                interpretation = {
                    "page_title": row["title"], "page_type": "legacy_confirmed",
                    "purpose": old.get("page_purpose", ""), "target_roles": [],
                    "entry_conditions": [old["entry_conditions"]]
                    if old.get("entry_conditions") else [],
                    "visible_regions": [old["visible_regions"]]
                    if old.get("visible_regions") else [], "key_controls": [],
                    "workflow_steps": [old["typical_workflow"]]
                    if old.get("typical_workflow") else [], "success_state": "",
                    "failure_and_recovery": old.get("result_validation_recovery", ""),
                    "related_backend_actions": [old["backend_interactions"]]
                    if old.get("backend_interactions") else [],
                    "route_guess": "", "related_evidence_refs": [],
                    "suggested_group": row["group_title"] or "历史界面截图",
                    "suggested_order": row["sort_order"],
                    "suggested_caption": row["title"], "confidence": 1.0,
                    "warnings": ["由旧版已确认六字段说明兼容迁移；可重新使用视觉模型分析"],
                }
                now, interpretation_id = utc_now(), str(uuid4())
                connection.execute(
                    """INSERT INTO manual_screenshot_interpretation_revisions(
                    id,asset_id,asset_revision_id,project_profile_revision_id,version,
                    model_config_id,model_name,prompt_version,cache_key,status,
                    interpretation_json,origin,reviewed,attempt_count,elapsed_ms,created_at)
                    VALUES(?,?,?,?,1,NULL,'旧版人工说明',?,'','completed',?,
                    'legacy_migration',1,1,0,?)""",
                    (interpretation_id, row["asset_id"], row["revision_id"], profile["id"],
                     PROMPT_VERSION, encode_json(interpretation), now),
                )
                connection.execute(
                    """UPDATE manual_project_screenshot_assets SET analysis_status='completed',
                    review_status='reviewed',updated_at=? WHERE id=?""",
                    (now, row["asset_id"]),
                )

    def _import_one(self, task_id, batch_id, source_path, source, order):
        if source_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ScreenshotEvidenceError("不支持的图片格式")
        if not source_path.is_file():
            raise ScreenshotEvidenceError("图片文件不存在")
        if source_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ScreenshotEvidenceError("图片超过 20MB 限制")
        try:
            with Image.open(source_path) as opened:
                opened.load()
                width, height = opened.size
                if width > MAX_DIMENSION or height > MAX_DIMENSION:
                    raise ScreenshotEvidenceError("图片尺寸超过安全限制")
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    raise ScreenshotEvidenceError(
                        "图片过小（至少 {0}×{1}）".format(MIN_WIDTH, MIN_HEIGHT))
                image = opened.convert("RGB")
        except (UnidentifiedImageError, OSError) as error:
            raise ScreenshotEvidenceError("图片损坏或无法识别") from error
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        duplicate = self._duplicate(task_id, digest, image)
        if duplicate:
            raise ScreenshotEvidenceError("与已有截图“{0}”重复或高度相似".format(duplicate))
        asset_id = str(uuid4())
        asset_key = self._slug(source_path.stem) + "-" + asset_id[:8]
        relative = Path("artifacts/manual/screenshots/project") / asset_key / "v1.png"
        target = self._image_path(task_id, relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, "PNG", optimize=True)
        file_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        now, revision_id = utc_now(), str(uuid4())
        title = source_path.stem.strip()[:120] or "未命名截图"
        group_title = source_path.parent.name[:80] if source == "folder" else "未分组页面"
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_project_screenshot_assets(
                id,task_id,asset_key,import_batch_id,source,title,image_relative_path,width,
                height,image_format,sha256,analysis_status,review_status,adoption_status,
                group_key,group_title,sort_order,sensitive_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'PNG',?,'pending','pending','pending',?,?,?,
                'unreviewed',?,?)""",
                (asset_id, task_id, asset_key, batch_id, source, title,
                 relative.as_posix(), width, height, file_sha, self._slug(group_title),
                 group_title, order, now, now),
            )
            connection.execute(
                """INSERT INTO manual_project_screenshot_revisions(
                id,asset_id,version,title,image_relative_path,width,height,image_format,sha256,
                edit_source,parent_revision_id,created_at)
                VALUES(?,?,1,?,?,?,?, 'PNG',?,'import',NULL,?)""",
                (revision_id, asset_id, title, relative.as_posix(), width, height, file_sha, now),
            )
        return next(item for item in self.list_assets(task_id) if item["id"] == asset_id)

    def _duplicate(self, task_id, digest, candidate, exclude_asset_id=None):
        sample = candidate.convert("L").resize((64, 64))
        for item in self.list_assets(task_id):
            if item["id"] == exclude_asset_id:
                continue
            if item["sha256"] == digest:
                return item["title"]
            try:
                existing = Image.open(self._image_path(task_id, item["image_relative_path"]))
                delta = ImageStat.Stat(ImageChops.difference(
                    sample, existing.convert("L").resize((64, 64)))).mean[0]
            except (OSError, ValueError):
                continue
            if delta < 2.2:
                return item["title"]
        return None

    def _prepare_node(self, job_id, node_key, kind, title, model_config_id=None):
        if not job_id:
            return None
        execution = ManualExecutionNodeService(self._database)
        prepared = execution.prepare(
            job_id, node_key, "screenshots", kind, title,
            dependencies=["project_profile"], model_config_id=model_config_id,
            max_attempts=2,
        )
        execution.running(job_id, node_key, max(1, int(prepared.get("attempt") or 0) + 1))
        return node_key

    def _mark_ui_outdated(self, task_id):
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_execution_nodes SET status='outdated',updated_at=?
                WHERE node_key IN ('section:ui_operations','ui_section_update') AND job_id IN
                (SELECT id FROM manual_generation_jobs WHERE task_id=?)""",
                (utc_now(), task_id),
            )

    @staticmethod
    def _mark_analysis_outdated(connection, task_id):
        connection.execute(
            """UPDATE manual_project_screenshot_assets SET analysis_status='outdated',
            review_status='pending',updated_at=? WHERE task_id=? AND analysis_status='completed'""",
            (utc_now(), task_id),
        )

    def _task(self, task_id):
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT t.id,ps.display_name FROM tasks t JOIN project_sources ps
                ON ps.id=t.source_id WHERE t.id=?""", (task_id,),
            ).fetchone()
        if row is None:
            raise ScreenshotEvidenceError("项目任务不存在")
        return row

    def _assert_job(self, task_id, job_id):
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM manual_generation_jobs WHERE id=? AND task_id=?",
                (job_id, task_id),
            ).fetchone()
        if row is None:
            raise ScreenshotEvidenceError("说明书任务不属于当前项目")

    def _asset_row(self, task_id, asset_id):
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM manual_project_screenshot_assets WHERE id=? AND task_id=?",
                (asset_id, task_id),
            ).fetchone()
        if row is None:
            raise ScreenshotEvidenceError("截图不存在")
        return row

    def _image_path(self, task_id, relative_path):
        task_root = (self._data_root / "tasks" / task_id).resolve()
        path = (task_root / Path(relative_path)).resolve()
        if task_root not in path.parents:
            raise ScreenshotEvidenceError("截图路径越界")
        return path

    def _analysis_context(self, task_id: str, asset: dict) -> dict:
        group_key = asset.get("group_key") or ""
        related = []
        if group_key and group_key not in {"未分组页面", "未分组页面".lower()}:
            related = [item for item in self.list_assets(task_id)
                       if item["id"] != asset["id"] and item["group_key"] == group_key and
                       item["review_status"] == "reviewed" and item["interpretation"]][:3]
        with self._database.connect() as connection:
            revisions = connection.execute(
                """SELECT version,interpretation_json,created_at
                FROM manual_screenshot_interpretation_revisions
                WHERE asset_id=? AND reviewed=1 AND status='completed'
                ORDER BY version DESC LIMIT 3""", (asset["id"],),
            ).fetchall()
        confirmed = [{"version": row["version"],
                      "interpretation": json.loads(row["interpretation_json"]),
                      "created_at": row["created_at"]} for row in revisions]
        related_summary = [{"asset_id": item["id"], "revision_id": item["revision_id"],
                            "interpretation_version": item["interpretation_version"],
                            "title": item["title"], "group": item["group_title"],
                            "order": item["sort_order"],
                            "confirmed_interpretation": item["interpretation"]}
                           for item in related]
        basis = {"related": related_summary, "confirmed_user_revisions": confirmed}
        return {**basis,
                "related_image_paths": [item["image_relative_path"] for item in related],
                "fingerprint": hashlib.sha256(encode_json(basis).encode()).hexdigest()}

    @staticmethod
    def _analysis_prompt(profile, asset, analysis_context=None):
        schema = {key: ([] if expected is list else 0 if key == "suggested_order" else
                        0.0 if key == "confidence" else "")
                  for key, expected in INTERPRETATION_FIELDS.items()}
        return """你是软件著作权材料的界面证据分析器。只依据图片和项目概要返回单个 JSON 对象。
不得编造不可见的后台动作、成功结果、权限或业务数据；推断必须降低 confidence，并写入 warnings
或 related_evidence_refs。列表元素使用简短中文字符串。不要输出章节散文、Markdown 或解释。

项目概要：{0}
文件名/现有标题：{1}
同组已审核参考截图与当前图的已确认修订：{2}
严格字段：{3}""".format(encode_json(profile)[:18000], asset["title"],
                         encode_json(analysis_context or {})[:12000], encode_json(schema))

    @staticmethod
    def _parse_json(raw):
        text = (raw or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            first, last = text.find("{"), text.rfind("}")
            if first >= 0 and last > first:
                text = text[first:last + 1]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ScreenshotEvidenceError("模型返回的结构化 JSON 无法解析") from error
        if not isinstance(value, dict):
            raise ScreenshotEvidenceError("模型必须返回 JSON 对象")
        return value

    @staticmethod
    def _normalize_interpretation(value):
        result = {}
        for key, expected in INTERPRETATION_FIELDS.items():
            item = value.get(key)
            if key == "confidence":
                try:
                    result[key] = max(0.0, min(1.0, float(item)))
                except (TypeError, ValueError):
                    raise ScreenshotEvidenceError("confidence 必须是 0 到 1 的数字")
            elif key == "suggested_order":
                try:
                    result[key] = max(0, int(item or 0))
                except (TypeError, ValueError):
                    raise ScreenshotEvidenceError("suggested_order 必须是整数")
            elif expected is list:
                if not isinstance(item, list):
                    raise ScreenshotEvidenceError("{0} 必须是数组".format(key))
                result[key] = [str(entry).strip()[:500] for entry in item if str(entry).strip()][:30]
            else:
                result[key] = str(item or "").strip()[:2000]
        if not result["page_title"] or not result["purpose"] or not result["suggested_caption"]:
            raise ScreenshotEvidenceError("结构化解读缺少页面标题、用途或图注")
        return result

    @staticmethod
    def _normalize_profile(profile):
        if not isinstance(profile, dict):
            raise ScreenshotEvidenceError("项目概要格式无效")
        allowed = {"software_name", "purpose", "target_users", "user_roles", "core_modules",
                   "technology_stack", "page_route_clues", "main_operations", "source_evidence",
                   "related_api_component_evidence", "unconfirmed"}
        result = {key: profile.get(key, [] if key not in {"software_name", "purpose"} else "")
                  for key in allowed}
        result["software_name"] = str(result["software_name"]).strip()[:200]
        result["purpose"] = str(result["purpose"]).strip()[:1000]
        for key in allowed - {"software_name", "purpose"}:
            if not isinstance(result[key], list):
                raise ScreenshotEvidenceError("项目概要字段 {0} 必须是数组".format(key))
            result[key] = result[key][:40]
        if not result["software_name"]:
            raise ScreenshotEvidenceError("软件名称不能为空")
        return result

    @staticmethod
    def _profile_dict(row):
        return {"id": row["id"], "task_id": row["task_id"], "version": row["version"],
                "origin": row["origin"], "profile": json.loads(row["profile_json"]),
                "fingerprint": row["fingerprint"], "created_at": row["created_at"]}

    @staticmethod
    def _asset_dict(row):
        interpretation = (json.loads(row["interpretation_json"])
                          if row["interpretation_json"] and
                          row["analysis_status"] == "completed" else None)
        return {"id": row["id"], "task_id": row["task_id"], "asset_key": row["asset_key"],
                "source": row["source"], "title": row["title"],
                "image_relative_path": row["image_relative_path"], "width": row["width"],
                "height": row["height"], "image_format": row["image_format"],
                "sha256": row["sha256"], "version": row["revision_version"],
                "revision_id": row["revision_id"], "analysis_status": row["analysis_status"],
                "review_status": row["review_status"], "adoption_status": row["adoption_status"],
                "group_key": row["group_key"], "group_title": row["group_title"],
                "sort_order": row["sort_order"], "sensitive_status": row["sensitive_status"],
                "failure_reason": row["failure_reason"], "interpretation": interpretation,
                "interpretation_id": row["interpretation_id"],
                "interpretation_version": row["interpretation_version"],
                "interpretation_reviewed": bool(row["interpretation_reviewed"] or 0),
                "interpretation_model": row["interpretation_model"],
                "interpretation_elapsed_ms": row["interpretation_elapsed_ms"],
                "interpretation_attempts": row["interpretation_attempts"],
                "archived": row["archived_at"] is not None,
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    @staticmethod
    def _generic_group(value: object) -> bool:
        normalized = re.sub(r"[\s_-]+", "", str(value or "")).lower()
        return normalized in {
            "", "截屏", "截图", "界面截图", "页面截图", "screenshot", "screenshots",
            "未分组", "未分组页面",
        }

    @staticmethod
    def _pick(mapping, *keys):
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, list):
                return value[:30]
            if value:
                return [value]
        return []

    @staticmethod
    def _fact_values(facts, markers, limit):
        values = []
        for item in facts:
            if any(marker in item["key"].lower() for marker in markers):
                values.append({"fact_key": item["key"], "value": item["value"],
                               "confidence": item["confidence"]})
        return values[:limit]

    @staticmethod
    def _slug(value):
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", str(value).lower()).strip("-")
        return slug[:80] or "screens"

    @staticmethod
    def _natural_key(value):
        return [int(part) if part.isdigit() else part.lower()
                for part in re.split(r"(\d+)", str(value))]
