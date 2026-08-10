import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .domain import TaskStatus
from .manual_plan import MANUAL_PLAN_RULES_VERSION, ManualPlan, ManualPlanBuilder, PlanningFact
from .service import new_id, utc_now
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


class ManualPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedManualPlan:
    task_id: str
    run_id: str
    version: int
    artifact_path: Path
    plan: ManualPlan


class ManualPlanService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._builder = ManualPlanBuilder()
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedManualPlan:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ManualPlanError("Task not found: {0}".format(task_id))
            if task["status"] not in {TaskStatus.COMPLETED.value,
                                      TaskStatus.COMPLETED_WITH_WARNINGS.value,
                                      TaskStatus.FAILED.value}:
                raise ManualPlanError("Task must be completed before manual planning")
            rows = connection.execute(
                """SELECT id, fact_key, value_json, confidence, evidence_ids_json
                FROM facts WHERE task_id = ? AND status IN ('candidate', 'confirmed')
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, created_at DESC""",
                (task_id,),
            ).fetchall()
        facts_by_key = {}
        for row in rows:
            facts_by_key.setdefault(row["fact_key"], PlanningFact(
                row["id"], row["fact_key"], json.loads(row["value_json"]),
                row["confidence"], tuple(json.loads(row["evidence_ids_json"])),
            ))

        now = utc_now()
        stage_id = new_id()
        with UnitOfWork(self._database) as unit_of_work:
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.RUNNING, now,
                current_stage_key="07_plan_manual",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "07_plan_manual")
            unit_of_work.stages.start(stage_id, task_id, "07_plan_manual", 7, attempt, now)
            version = unit_of_work.manual_plans.next_version(task_id)

        try:
            plan = self._builder.build(facts_by_key.values())
            relative = Path("intermediate") / "manual-planning" / "plan.v{0}.json".format(version)
            artifact_path = self._data_root / "tasks" / task_id / relative
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._payload(task_id, version, plan)
            self._write_json_atomic(artifact_path, payload)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished = utc_now()
        run_id = new_id()
        summary = {
            "section_count": len(plan.sections),
            "ready_sections": plan.ready_sections,
            "needs_evidence_sections": plan.needs_evidence_sections,
            "missing_information_count": len(plan.missing_information),
            "diagram_count": len(plan.diagram_requirements),
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.manual_plans.add_run(
                run_id, task_id, stage_id, version, MANUAL_PLAN_RULES_VERSION,
                summary, relative.as_posix(), fingerprint, finished,
            )
            unit_of_work.stages.succeed(
                stage_id, fingerprint, {"run_id": run_id, "version": version}, finished
            )
            unit_of_work.events.add(
                task_id, "stage.succeeded", "info", "Manual chapter planning completed",
                summary, finished, stage_run_id=stage_id,
            )
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.COMPLETED, finished,
                current_stage_key="07_plan_manual",
            )
        return PersistedManualPlan(task_id, run_id, version, artifact_path, plan)

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe = "Manual planning failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "manual_plan_error", safe, now)
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.FAILED, now,
                current_stage_key="07_plan_manual",
                failure_category="manual_plan_error", safe_error_message=safe,
            )

    @staticmethod
    def _payload(task_id: str, version: int, plan: ManualPlan) -> dict:
        return {
            "schema_version": 1, "rules_version": MANUAL_PLAN_RULES_VERSION,
            "task_id": task_id, "version": version,
            "summary": {
                "section_count": len(plan.sections),
                "ready_sections": plan.ready_sections,
                "needs_evidence_sections": plan.needs_evidence_sections,
                "missing_information_count": len(plan.missing_information),
            },
            "missing_information": plan.missing_information,
            "diagram_requirements": plan.diagram_requirements,
            "sections": [section.__dict__ for section in plan.sections],
        }

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="manual-plan-", suffix=".tmp", dir=str(path.parent)
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
