import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .domain import TaskStatus
from .service import new_id, utc_now
from .source_selection import SOURCE_SELECTION_RULES_VERSION, SourcePlan, SourceSelector
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


class SourcePlanError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedSourcePlan:
    task_id: str
    run_id: str
    version: int
    artifact_path: Path
    plan: SourcePlan


class SourcePlanService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._selector = SourceSelector()
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedSourcePlan:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT t.status, ps.manifest_relative_path, ps.scan_root_mode,
                ps.scan_root_path FROM tasks t
                JOIN project_snapshots ps ON ps.id = t.snapshot_id
                WHERE t.id = ?""",
                (task_id,),
            ).fetchone()
            if row is None:
                raise SourcePlanError("Task or snapshot not found: {0}".format(task_id))
            if row["status"] != TaskStatus.COMPLETED.value:
                raise SourcePlanError(
                    "Task metadata must be completed before source planning: {0}".format(
                        row["status"]
                    )
                )
            task_root = self._data_root / "tasks" / task_id
            manifest_path = task_root / PurePosixPath(row["manifest_relative_path"])
            if row["scan_root_mode"] == "task":
                scan_root = task_root / PurePosixPath(row["scan_root_path"])
            else:
                scan_root = Path(row["scan_root_path"])
            report_path = task_root / "qa" / "scan-report.json"

        if not manifest_path.is_file() or not scan_root.is_dir():
            raise SourcePlanError("Source planning inputs are missing")
        secret_paths = []
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            secret_paths = [item["path"] for item in report.get("secret_findings", [])]

        stage_id = new_id()
        now = utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            task = unit_of_work.tasks.get(task_id)
            running = self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.RUNNING,
                now,
                current_stage_key="05_select_source",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "05_select_source")
            unit_of_work.stages.start(
                stage_id, task_id, "05_select_source", 5, attempt, now
            )
            version = unit_of_work.source_plans.next_version(task_id)

        try:
            plan = self._selector.build(scan_root, manifest_path, secret_paths)
            artifact_relative = Path("intermediate") / "source-selection" / (
                "plan.v{0}.json".format(version)
            )
            artifact_path = task_root / artifact_relative
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._payload(task_id, version, plan)
            self._write_json_atomic(artifact_path, payload)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished_at = utc_now()
        run_id = new_id()
        summary = {
            "total_source_files": plan.total_source_files,
            "selected_files": plan.selected_files,
            "selected_code_lines": plan.selected_code_lines,
            "excluded_files": plan.excluded_files,
            "grades": {
                grade: sum(1 for item in plan.candidates if item.grade == grade)
                for grade in ("A", "B", "C")
            },
        }
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.source_plans.add_run(
                run_id, task_id, stage_id, version, SOURCE_SELECTION_RULES_VERSION,
                summary, artifact_relative.as_posix(), finished_at,
            )
            for candidate in plan.candidates:
                unit_of_work.source_plans.add_candidate(
                    new_id(), run_id, candidate, finished_at
                )
            fingerprint = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            unit_of_work.stages.succeed(
                stage_id, fingerprint, {"run_id": run_id, "version": version}, finished_at
            )
            unit_of_work.events.add(
                task_id, "stage.succeeded", "info", "Source planning completed",
                summary, finished_at, stage_run_id=stage_id,
            )
            running_task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                running_task,
                TaskStatus.COMPLETED,
                finished_at,
                current_stage_key="05_select_source",
            )

        return PersistedSourcePlan(task_id, run_id, version, artifact_path, plan)

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe_message = "Source planning failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "source_plan_error", safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, task, TaskStatus.FAILED, now,
                current_stage_key="05_select_source",
                failure_category="source_plan_error",
                safe_error_message=safe_message,
            )

    @staticmethod
    def _payload(task_id: str, version: int, plan: SourcePlan) -> dict:
        return {
            "schema_version": 1,
            "task_id": task_id,
            "version": version,
            "rules_version": SOURCE_SELECTION_RULES_VERSION,
            "summary": {
                "total_source_files": plan.total_source_files,
                "selected_files": plan.selected_files,
                "selected_code_lines": plan.selected_code_lines,
                "excluded_files": plan.excluded_files,
            },
            "candidates": [
                {
                    "path": item.relative_path,
                    "grade": item.grade,
                    "selected": item.selected,
                    "score": item.score,
                    "code_lines": item.code_lines,
                    "byte_size": item.byte_size,
                    "language": item.language,
                    "reasons": item.reasons,
                    "exclusion_code": item.exclusion_code,
                }
                for item in plan.candidates
            ],
        }

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="source-plan-", suffix=".tmp", dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
