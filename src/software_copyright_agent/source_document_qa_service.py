from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .domain import TaskStatus
from .service import new_id, utc_now
from .source_document_qa import (
    QA_POLICY_VERSION,
    LibreOfficeRenderer,
    SourceDocumentQaError,
    SourceDocumentQaInspector,
    write_qa_report,
)
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


@dataclass(frozen=True)
class PersistedSourceDocumentQa:
    task_id: str
    run_id: str
    version: int
    passed: bool
    report_path: Path
    render_path: Path
    summary: dict
    checks: tuple


class SourceDocumentQaService:
    def __init__(self, database: Database, data_root: Path,
                 renderer: LibreOfficeRenderer = None,
                 inspector: SourceDocumentQaInspector = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._renderer = renderer or LibreOfficeRenderer()
        self._inspector = inspector or SourceDocumentQaInspector()
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedSourceDocumentQa:
        self._database.initialize()
        with self._database.connect() as connection:
            document = connection.execute(
                """SELECT id, artifact_relative_path, sha256 FROM source_document_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if document is None or task is None:
                raise SourceDocumentQaError("Source document not found for task")
            if task["status"] not in {
                TaskStatus.COMPLETED.value, TaskStatus.COMPLETED_WITH_WARNINGS.value,
                TaskStatus.FAILED.value,
            }:
                raise SourceDocumentQaError("Task must be completed before document QA")

        now = utc_now()
        stage_id = new_id()
        with UnitOfWork(self._database) as unit_of_work:
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.RUNNING, now,
                current_stage_key="06_qa_source_doc",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "06_qa_source_doc")
            unit_of_work.stages.start(
                stage_id, task_id, "06_qa_source_doc", 6, attempt, now
            )
            qa_version = unit_of_work.source_document_qa.next_version(task_id)

        task_root = self._data_root / "tasks" / task_id
        document_path = task_root / PurePosixPath(document["artifact_relative_path"])
        render_relative = Path("qa") / "source-code" / "v{0}".format(qa_version) / "render"
        report_relative = Path("qa") / "source-code" / "v{0}".format(qa_version) / "report.json"
        render_path = task_root / render_relative
        report_path = task_root / report_relative
        try:
            render = self._renderer.render(document_path, render_path)
            result = self._inspector.inspect(document_path, document["sha256"], render)
            write_qa_report(report_path, task_id, document["id"], qa_version, result)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished = utc_now()
        run_id = new_id()
        checks = tuple(check.__dict__ for check in result.checks)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.source_document_qa.add_run(
                run_id, task_id, document["id"], stage_id, qa_version,
                QA_POLICY_VERSION, result.passed, checks, result.summary,
                report_relative.as_posix(), render_relative.as_posix(), finished,
            )
            unit_of_work.stages.succeed(
                stage_id, result.summary["document_sha256"],
                {"run_id": run_id, "version": qa_version, "passed": result.passed},
                finished,
            )
            unit_of_work.events.add(
                task_id, "quality.checked", "info" if result.passed else "warning",
                "Source document QA passed" if result.passed else "Source document QA blocked",
                result.summary, finished, stage_run_id=stage_id,
            )
            record = unit_of_work.tasks.get(task_id)
            target = TaskStatus.COMPLETED if result.passed else TaskStatus.COMPLETED_WITH_WARNINGS
            self._state_machine.transition(
                unit_of_work, record, target, finished,
                current_stage_key="06_qa_source_doc",
            )
        return PersistedSourceDocumentQa(
            task_id, run_id, qa_version, result.passed, report_path,
            render_path, result.summary, checks,
        )

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe_message = "Source document QA failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "source_document_qa_error", safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, task, TaskStatus.FAILED, now,
                current_stage_key="06_qa_source_doc",
                failure_category="source_document_qa_error",
                safe_error_message=safe_message,
            )
