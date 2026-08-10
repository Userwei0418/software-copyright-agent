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
        status = task["status"]
        preview_sufficient = bool(
            preview_payload and preview_payload["summary"].get("sufficient")
        )
        actions = {
            "source_plan": status == "completed",
            "code_preview": plan_payload is not None
            and status in {"completed", "completed_with_warnings"},
            "source_docx": preview_sufficient
            and status in {"completed", "completed_with_warnings"},
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

    def build_source_plan(self, task_id: str) -> dict:
        self._source_plan.execute(task_id)
        return self.snapshot(task_id)

    def build_code_preview(self, task_id: str) -> dict:
        self._code_preview.execute(task_id)
        return self.snapshot(task_id)

    def build_source_document(self, task_id: str) -> dict:
        self._source_document.execute(task_id)
        return self.snapshot(task_id)

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
