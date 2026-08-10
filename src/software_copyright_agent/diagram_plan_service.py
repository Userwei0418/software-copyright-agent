import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .diagram_plan import DIAGRAM_PLAN_RULES_VERSION, DiagramPlan, DiagramPlanBuilder
from .domain import TaskStatus
from .manual_plan import PlanningFact
from .service import new_id, utc_now
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


class DiagramPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedDiagramPlan:
    task_id: str
    run_id: str
    version: int
    artifact_path: Path
    plan: DiagramPlan


class DiagramPlanService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._builder = DiagramPlanBuilder()
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedDiagramPlan:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            manual_run = connection.execute(
                "SELECT id FROM manual_plan_runs WHERE task_id = ? ORDER BY version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if task is None or manual_run is None:
                raise DiagramPlanError("Manual plan not found for task")
            if task["status"] not in {TaskStatus.COMPLETED.value,
                                      TaskStatus.COMPLETED_WITH_WARNINGS.value,
                                      TaskStatus.FAILED.value}:
                raise DiagramPlanError("Task must be completed before diagram planning")
            rows = connection.execute(
                """SELECT id, fact_key, value_json, confidence, evidence_ids_json
                FROM facts WHERE task_id = ? AND status IN ('candidate', 'confirmed')
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, created_at DESC""",
                (task_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """SELECT e.id, e.relative_path, e.locator_json FROM evidence e
                JOIN tasks t ON t.snapshot_id = e.snapshot_id
                WHERE t.id = ? AND e.relative_path IS NOT NULL
                ORDER BY e.relative_path, e.id""",
                (task_id,),
            ).fetchall()
        by_key = {}
        for row in rows:
            by_key.setdefault(row["fact_key"], PlanningFact(
                row["id"], row["fact_key"], json.loads(row["value_json"]),
                row["confidence"], tuple(json.loads(row["evidence_ids_json"])),
            ))

        now = utc_now()
        stage_id = new_id()
        with UnitOfWork(self._database) as unit_of_work:
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.RUNNING, now,
                current_stage_key="08_plan_diagrams",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "08_plan_diagrams")
            unit_of_work.stages.start(stage_id, task_id, "08_plan_diagrams", 8, attempt, now)
            version = unit_of_work.diagram_plans.next_version(task_id)

        try:
            evidence_by_path = {}
            for row in evidence_rows:
                locator = json.loads(row["locator_json"])
                category = "dependency" if "internal_imports" in locator else (
                    "transition" if "transitions" in locator else None
                )
                if category is not None:
                    key = category + ":" + row["relative_path"]
                    evidence_by_path.setdefault(key, []).append(row["id"])
            plan = self._builder.build(
                by_key.values(),
                {path: tuple(ids) for path, ids in evidence_by_path.items()},
            )
            if not plan.validation["passed"]:
                raise DiagramPlanError("Diagram plan validation failed")
            relative = Path("intermediate") / "diagram-planning" / "plan.v{0}.json".format(version)
            artifact_path = self._data_root / "tasks" / task_id / relative
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._payload(task_id, version, plan)
            self._write_json_atomic(artifact_path, payload)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished = utc_now()
        run_id = new_id()
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        summary = {
            "diagram_count": len(plan.diagrams), "ready_diagrams": plan.ready_diagrams,
            "needs_evidence_diagrams": plan.needs_evidence_diagrams,
            "node_count": sum(len(item.nodes) for item in plan.diagrams),
            "edge_count": sum(len(item.edges) for item in plan.diagrams),
        }
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.diagram_plans.add_run(
                run_id, task_id, manual_run["id"], stage_id, version,
                DIAGRAM_PLAN_RULES_VERSION, summary, relative.as_posix(), fingerprint, finished,
            )
            unit_of_work.stages.succeed(
                stage_id, fingerprint, {"run_id": run_id, "version": version}, finished
            )
            unit_of_work.events.add(
                task_id, "stage.succeeded", "info", "Diagram semantic planning completed",
                summary, finished, stage_run_id=stage_id,
            )
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.COMPLETED, finished,
                current_stage_key="08_plan_diagrams",
            )
        return PersistedDiagramPlan(task_id, run_id, version, artifact_path, plan)

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe = "Diagram planning failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "diagram_plan_error", safe, now)
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.FAILED, now,
                current_stage_key="08_plan_diagrams",
                failure_category="diagram_plan_error", safe_error_message=safe,
            )

    @staticmethod
    def _payload(task_id: str, version: int, plan: DiagramPlan) -> dict:
        return {
            "schema_version": 1, "rules_version": DIAGRAM_PLAN_RULES_VERSION,
            "task_id": task_id, "version": version,
            "summary": {
                "diagram_count": len(plan.diagrams),
                "ready_diagrams": plan.ready_diagrams,
                "needs_evidence_diagrams": plan.needs_evidence_diagrams,
            },
            "validation": plan.validation,
            "diagrams": [{
                **{key: value for key, value in diagram.__dict__.items()
                   if key not in {"nodes", "edges"}},
                "nodes": [node.__dict__ for node in diagram.nodes],
                "edges": [edge.__dict__ for edge in diagram.edges],
            } for diagram in plan.diagrams],
        }

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="diagram-plan-", suffix=".tmp", dir=str(path.parent)
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
