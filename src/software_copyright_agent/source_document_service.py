import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .domain import TaskStatus
from .font_assets import FontAsset
from .service import new_id, utc_now
from .source_document import (
    GENERATOR_VERSION,
    SourceDocumentBuilder,
    SourceDocumentError,
    SourceDocumentTemplate,
)
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


@dataclass(frozen=True)
class PersistedSourceDocument:
    task_id: str
    run_id: str
    version: int
    artifact_path: Path
    sha256: str
    summary: dict


class SourceDocumentService:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        template: SourceDocumentTemplate = None,
    ) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._builder = SourceDocumentBuilder(template)
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedSourceDocument:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            preview = connection.execute(
                """SELECT id, artifact_relative_path, summary_json
                FROM code_preview_runs WHERE task_id = ?
                ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            if task is None or preview is None:
                raise SourceDocumentError("Code pagination preview not found for task")
            if task["status"] not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.COMPLETED_WITH_WARNINGS.value,
            }:
                raise SourceDocumentError(
                    "Task must be completed before source document generation"
                )
            preview_summary = json.loads(preview["summary_json"])
            if not preview_summary.get("sufficient"):
                raise SourceDocumentError(
                    "Source code is insufficient for a 60-page document"
                )
            facts = self._load_facts(connection, task_id)

        software_name = facts.get("project.name")
        version_name = facts.get("project.version")
        if not software_name or not version_name:
            raise SourceDocumentError("Confirmed software name and version are required")

        task_root = self._data_root / "tasks" / task_id
        preview_path = task_root / PurePosixPath(preview["artifact_relative_path"])
        preview_payload = json.loads(preview_path.read_text(encoding="utf-8"))
        required_text = "".join(
            entry.get("text", "")
            for page in preview_payload["pages"] for entry in page["entries"]
        ) + str(software_name) + str(version_name) + "源程序代码文档构成封面正文第页共"
        font_summary = FontAsset.bundled_cjk().validate(required_text)

        stage_id = new_id()
        now = utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            running_task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                running_task,
                TaskStatus.RUNNING,
                now,
                current_stage_key="06_prepare_source_doc",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "06_prepare_source_doc")
            unit_of_work.stages.start(
                stage_id, task_id, "06_prepare_source_doc", 6, attempt, now
            )
            document_version = unit_of_work.source_documents.next_version(task_id)

        artifact_relative = Path("artifacts") / "source-code" / (
            "source-code.v{0}.docx".format(document_version)
        )
        artifact_path = task_root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="source-docx-", suffix=".docx", dir=str(artifact_path.parent)
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            summary = self._builder.build(
                temporary_path,
                str(software_name),
                str(version_name),
                preview_payload["pages"],
            )
            summary["cjk_font_preflight"] = font_summary
            digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
            os.replace(str(temporary_path), str(artifact_path))
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            self._record_failure(task_id, stage_id, error)
            raise

        finished_at = utc_now()
        run_id = new_id()
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.source_documents.add_run(
                run_id,
                task_id,
                preview["id"],
                stage_id,
                document_version,
                GENERATOR_VERSION,
                asdict(self._builder.template),
                summary,
                artifact_relative.as_posix(),
                digest,
                finished_at,
            )
            unit_of_work.stages.succeed(
                stage_id,
                digest,
                {"run_id": run_id, "version": document_version, "sha256": digest},
                finished_at,
            )
            unit_of_work.events.add(
                task_id,
                "artifact.created",
                "info",
                "Source code DOCX created",
                summary,
                finished_at,
                stage_run_id=stage_id,
            )
            task_record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                task_record,
                TaskStatus.COMPLETED,
                finished_at,
                current_stage_key="06_prepare_source_doc",
            )
        return PersistedSourceDocument(
            task_id, run_id, document_version, artifact_path, digest, summary
        )

    @staticmethod
    def _load_facts(connection, task_id: str) -> dict:
        rows = connection.execute(
            """SELECT fact_key, value_json, status, created_at FROM facts
            WHERE task_id = ? AND status IN ('candidate', 'confirmed')
            ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END,
                     created_at DESC""",
            (task_id,),
        ).fetchall()
        facts = {}
        for row in rows:
            if row["fact_key"] not in facts:
                facts[row["fact_key"]] = json.loads(row["value_json"])
        return facts

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe_message = "Source document generation failed: {0}".format(
            type(error).__name__
        )
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "source_document_error", safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.FAILED,
                now,
                current_stage_key="06_prepare_source_doc",
                failure_category="source_document_error",
                safe_error_message=safe_message,
            )
