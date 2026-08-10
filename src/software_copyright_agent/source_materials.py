import hashlib
import json
from pathlib import Path

from .code_preview_service import CodePreviewService
from .source_document_service import SourceDocumentService
from .source_plan_service import SourcePlanService
from .storage import Database


class SourceMaterialsError(ValueError):
    pass


class SourceMaterialsService:
    """Read and manually advance the source-material generation pipeline."""

    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._source_plan = SourcePlanService(database, data_root)
        self._code_preview = CodePreviewService(database, data_root)
        self._source_document = SourceDocumentService(database, data_root)

    def snapshot(self, task_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                """SELECT id, status, current_stage_key, failure_category,
                safe_error_message FROM tasks WHERE id = ?""",
                (task_id,),
            ).fetchone()
            if task is None:
                raise SourceMaterialsError("Task not found: {0}".format(task_id))

            plan = connection.execute(
                """SELECT id, version, summary_json, created_at FROM source_plan_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            preview = connection.execute(
                """SELECT version, summary_json, created_at FROM code_preview_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            document = connection.execute(
                """SELECT version, summary_json, artifact_relative_path, sha256,
                created_at FROM source_document_runs WHERE task_id = ?
                ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            candidates = []
            if plan is not None:
                rows = connection.execute(
                    """SELECT relative_path, grade, score, code_lines, language
                    FROM source_candidates WHERE plan_run_id = ? AND selected = 1
                    ORDER BY CASE grade WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
                    score DESC, relative_path LIMIT 30""",
                    (plan["id"],),
                ).fetchall()
                candidates = [dict(row) for row in rows]

        plan_payload = self._run_payload(plan, include_id=False)
        if plan_payload is not None:
            plan_payload["candidates"] = candidates
        preview_payload = self._run_payload(preview)
        document_payload = self._run_payload(document)
        if document_payload is not None:
            document_payload["integrity"] = self._document_integrity(
                task_id, document_payload
            )
        status = task["status"]
        preview_sufficient = bool(
            preview_payload and preview_payload["summary"].get("sufficient")
        )
        retryable_document = (
            status == "failed" and task["failure_category"] == "source_document_error"
        )
        actions = {
            "source_plan": status in {"completed", "completed_with_warnings"},
            "code_preview": plan_payload is not None
            and status in {"completed", "completed_with_warnings"},
            "source_docx": preview_sufficient
            and (status in {"completed", "completed_with_warnings"}
                 or retryable_document),
        }
        blockers = self._blockers(status, plan_payload, preview_payload)
        return {
            "task": dict(task),
            "source_plan": plan_payload,
            "code_preview": preview_payload,
            "source_document": document_payload,
            "actions": actions,
            "blockers": blockers,
        }

    def build_source_plan(self, task_id: str, strategy: str = "standard") -> dict:
        self._source_plan.execute(task_id, strategy)
        return self.snapshot(task_id)

    def build_code_preview(self, task_id: str) -> dict:
        self._code_preview.execute(task_id)
        return self.snapshot(task_id)

    def build_source_document(self, task_id: str) -> dict:
        self._source_document.execute(task_id)
        return self.snapshot(task_id)

    def preview_pages(self, task_id: str, all_pages: bool = False) -> dict:
        """Return representative pages, or every page for the in-app document viewer."""
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise SourceMaterialsError("Task not found: {0}".format(task_id))
            preview = connection.execute(
                """SELECT version, artifact_relative_path FROM code_preview_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
        if preview is None:
            raise SourceMaterialsError("Code pagination preview not found for task")

        task_root = (self._data_root / "tasks" / task_id).resolve()
        artifact = (task_root / preview["artifact_relative_path"]).resolve()
        if task_root not in artifact.parents or not artifact.is_file():
            raise SourceMaterialsError("Code pagination artifact is unavailable")
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        pages = payload.get("pages", [])
        if not pages:
            return {"version": preview["version"], "total_pages": 0, "pages": []}
        indexes = list(range(len(pages))) if all_pages else sorted(
            {0, len(pages) // 2, len(pages) - 1}
        )
        sampled = []
        for index in indexes:
            page = pages[index]
            sampled.append({
                "page_number": page["page_number"],
                "line_count": page["line_count"],
                "entries": [
                    {
                        "kind": entry.get("kind", "code"),
                        "path": entry.get("path"),
                        "source_line": entry.get("source_line"),
                        "continuation": bool(entry.get("continuation")),
                        "text": entry.get("text", ""),
                    }
                    for entry in page.get("entries", [])
                ],
            })
        return {
            "version": preview["version"],
            "total_pages": len(pages),
            "pages": sampled,
        }

    @staticmethod
    def _run_payload(row, include_id: bool = True):
        if row is None:
            return None
        payload = {
            "version": row["version"],
            "summary": json.loads(row["summary_json"]),
            "created_at": row["created_at"],
        }
        if "artifact_relative_path" in row.keys():
            payload["artifact_relative_path"] = row["artifact_relative_path"]
            payload["sha256"] = row["sha256"]
        if include_id and "id" in row.keys():
            payload["id"] = row["id"]
        return payload

    @staticmethod
    def _blockers(status: str, plan, preview) -> list:
        blockers = []
        if status == "waiting_for_user":
            blockers.append("请先在项目概览完成必填信息确认。")
        elif status not in {"completed", "completed_with_warnings"}:
            blockers.append("当前任务状态为 {0}，暂不能生成源码材料。".format(status))
        if plan is None:
            blockers.append("尚未生成源码筛选计划。")
        elif preview is None:
            blockers.append("尚未进行代码分页预检。")
        elif not preview["summary"].get("sufficient"):
            blockers.append("可用代码不足 59 页，请补充项目源码后重新扫描。")
        return blockers

    def _document_integrity(self, task_id: str, document: dict) -> dict:
        task_root = (self._data_root / "tasks" / task_id).resolve()
        artifact = (task_root / document["artifact_relative_path"]).resolve()
        if task_root not in artifact.parents:
            return {"status": "invalid_path", "size_bytes": None}
        if not artifact.is_file():
            return {"status": "missing", "size_bytes": None}
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return {
            "status": "verified" if digest == document["sha256"] else "mismatch",
            "size_bytes": artifact.stat().st_size,
        }
