import hashlib
import json
from pathlib import Path

from .code_preview import FORMATTER_VERSION
from .code_preview_service import CodePreviewService
from .source_document import GENERATOR_VERSION as SOURCE_GENERATOR_VERSION
from .source_document_service import SourceDocumentService
from .source_document_qa import LibreOfficeRenderer, QA_POLICY_VERSION as SOURCE_QA_POLICY_VERSION
from .source_document_qa_service import SourceDocumentQaService
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
        self._source_document_qa = SourceDocumentQaService(database, data_root)

    def snapshot(self, task_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                """SELECT t.id, t.status, t.current_stage_key, t.failure_category,
                t.safe_error_message, s.display_name FROM tasks t
                JOIN project_sources s ON s.id = t.source_id WHERE t.id = ?""",
                (task_id,),
            ).fetchone()
            if task is None:
                raise SourceMaterialsError("Task not found: {0}".format(task_id))
            metadata_rows = connection.execute(
                """SELECT fact_key, value_json, status, created_at FROM facts
                WHERE task_id = ? AND fact_key IN ('project.name', 'project.version')
                AND status IN ('candidate', 'confirmed')
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, created_at DESC""",
                (task_id,),
            ).fetchall()
            metadata = {}
            for row in metadata_rows:
                if row["fact_key"] not in metadata:
                    metadata[row["fact_key"]] = json.loads(row["value_json"])

            plan = connection.execute(
                """SELECT id, version, summary_json, created_at FROM source_plan_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            preview = connection.execute(
                """SELECT version, formatter_version, summary_json, created_at FROM code_preview_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            document = connection.execute(
                """SELECT d.version, d.generator_version, d.summary_json,
                d.artifact_relative_path, d.sha256,
                d.created_at, q.passed qa_passed, q.summary_json qa_summary_json,
                q.version qa_version, q.policy_version qa_policy_version,
                q.created_at qa_created_at
                FROM source_document_runs d
                LEFT JOIN source_document_qa_runs q ON q.id = (
                    SELECT latest.id FROM source_document_qa_runs latest
                    WHERE latest.source_document_run_id = d.id
                    ORDER BY latest.version DESC LIMIT 1
                )
                WHERE d.task_id = ? ORDER BY d.version DESC LIMIT 1""",
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
        preview_current = bool(
            preview is not None and preview["formatter_version"] == FORMATTER_VERSION
        )
        if preview_payload is not None:
            preview_payload["formatter_version"] = preview["formatter_version"]
            preview_payload["current_formatter"] = preview_current
        document_payload = self._run_payload(document)
        if document_payload is not None:
            document_payload["integrity"] = self._document_integrity(
                task_id, document_payload
            )
            current_generator = document["generator_version"] == SOURCE_GENERATOR_VERSION
            current_policy = bool(
                document["qa_policy_version"] == SOURCE_QA_POLICY_VERSION
            )
            if document["qa_passed"] is None:
                document_payload["quality"] = {
                    "status": "not_checked", "passed": None, "summary": None,
                    "checked_at": None, "qa_version": None,
                    "policy_version": None, "current_policy": False,
                    "generator_version": document["generator_version"],
                    "current_generator": current_generator,
                }
            else:
                quality_status = (
                    "failed" if not document["qa_passed"] else
                    "passed" if current_generator and current_policy else "outdated"
                )
                document_payload["quality"] = {
                    "status": quality_status,
                    "passed": bool(document["qa_passed"]),
                    "summary": json.loads(document["qa_summary_json"]),
                    "checked_at": document["qa_created_at"],
                    "qa_version": document["qa_version"],
                    "policy_version": document["qa_policy_version"],
                    "current_policy": current_policy,
                    "generator_version": document["generator_version"],
                    "current_generator": current_generator,
                }
        status = task["status"]
        preview_sufficient = bool(
            preview_payload and preview_payload["summary"].get("sufficient")
        )
        preview_representative = bool(
            preview_payload and self._representative_sample(
                preview_payload["summary"]
            )
        )
        retryable_document = (
            status == "failed" and task["failure_category"] == "source_document_error"
        )
        actions = {
            "source_plan": status in {"completed", "completed_with_warnings"},
            "code_preview": plan_payload is not None
            and status in {"completed", "completed_with_warnings"},
            "source_docx": preview_sufficient and preview_representative and preview_current
            and (status in {"completed", "completed_with_warnings"}
                 or retryable_document),
        }
        blockers = self._blockers(status, plan_payload, preview_payload)
        return {
            "task": {key: task[key] for key in (
                "id", "status", "current_stage_key", "failure_category", "safe_error_message"
            )},
            "project": {
                "name": metadata.get("project.name") or task["display_name"],
                "version": metadata.get("project.version") or "",
            },
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

    def source_document_qa_capability(self, _task_id: str = "") -> dict:
        return LibreOfficeRenderer.capability()

    def run_source_document_qa(self, task_id: str) -> dict:
        self._source_document_qa.execute(task_id)
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

    def source_document_preview(self, task_id: str) -> dict:
        """Describe the real pages rendered by the latest DOCX quality run."""
        document, qa = self._latest_document_qa(task_id)
        if qa is None:
            raise SourceMaterialsError(
                "该源代码文档尚无真实 Word 渲染结果，请先执行逐页质量检查"
            )
        render_path = self._safe_task_path(task_id, qa["render_relative_path"])
        if not render_path.is_dir():
            raise SourceMaterialsError("源代码文档的真实渲染目录不可用")
        page_numbers = sorted(
            int(path.stem.split("-")[-1])
            for path in render_path.glob("page-*.png")
            if path.stem.split("-")[-1].isdigit()
        )
        if not page_numbers:
            raise SourceMaterialsError("源代码文档没有可显示的真实渲染页")
        return {
            "version": document["version"],
            "qa_version": qa["version"],
            "total_pages": len(page_numbers),
            "quality_status": self._quality_status(document, qa),
            "pages": page_numbers,
        }

    def read_source_document_preview_page(
        self, task_id: str, page_number: int
    ) -> bytes:
        preview = self.source_document_preview(task_id)
        if page_number not in preview["pages"]:
            raise SourceMaterialsError("源代码文档预览页码超出范围")
        _, qa = self._latest_document_qa(task_id)
        render_path = self._safe_task_path(task_id, qa["render_relative_path"])
        path = next((candidate for candidate in render_path.glob("page-*.png")
                     if candidate.stem.split("-")[-1].isdigit() and
                     int(candidate.stem.split("-")[-1]) == page_number), None)
        if path is None or not path.is_file():
            raise SourceMaterialsError("源代码文档预览页不存在")
        return path.read_bytes()

    def _latest_document_qa(self, task_id: str):
        self._database.initialize()
        with self._database.connect() as connection:
            document = connection.execute(
                """SELECT id, version, generator_version FROM source_document_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            if document is None:
                raise SourceMaterialsError("源代码文档不存在")
            qa = connection.execute(
                """SELECT version, policy_version, passed, render_relative_path
                FROM source_document_qa_runs WHERE source_document_run_id = ?
                ORDER BY version DESC LIMIT 1""", (document["id"],)
            ).fetchone()
        return document, qa

    @staticmethod
    def _quality_status(document, qa) -> str:
        if not qa["passed"]:
            return "failed"
        if (document["generator_version"] != SOURCE_GENERATOR_VERSION or
                qa["policy_version"] != SOURCE_QA_POLICY_VERSION):
            return "outdated"
        return "passed"

    def _safe_task_path(self, task_id: str, relative_path) -> Path:
        task_root = (self._data_root / "tasks" / task_id).resolve()
        path = (task_root / Path(relative_path)).resolve()
        if task_root != path and task_root not in path.parents:
            raise SourceMaterialsError("源代码文档预览路径无效")
        return path

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
        elif not SourceMaterialsService._representative_sample(preview["summary"]):
            blockers.append("当前 59 页取样过度集中，请重新执行分页预检，按前端、后端和数据层平衡取样。")
        elif not preview.get("current_formatter", False):
            blockers.append("分页规则已升级；旧预检可能在 Word 中产生额外页或稀疏页，请重新执行分页预检。")
        return blockers

    @staticmethod
    def _representative_sample(summary: dict) -> bool:
        selected = int(summary.get("selected_files", 0))
        included = int(summary.get("included_files", 0))
        if included < min(selected, 12):
            return False
        available_buckets = summary.get("available_buckets") or []
        included_buckets = summary.get("included_buckets") or []
        return len(included_buckets) >= min(len(available_buckets), 3)

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
