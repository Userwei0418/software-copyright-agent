import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .domain import TaskStatus
from .diagram_asset import DiagramOverlayEngine
from .drawio_document import (
    DRAWIO_GENERATOR_VERSION, DrawioDocumentBuilder, DrawioDocumentInspector,
    InternalSvgRenderer,
)
from .service import new_id, utc_now
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


class DrawioGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedDrawioArtifacts:
    task_id: str
    run_id: str
    version: int
    paths: Dict[str, Path]
    summary: dict


class DrawioGenerationService:
    def __init__(self, database: Database, data_root: Path,
                 builder: Optional[DrawioDocumentBuilder] = None,
                 renderer: Optional[object] = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._builder = builder or DrawioDocumentBuilder()
        self._renderer = renderer or InternalSvgRenderer()
        self._inspector = DrawioDocumentInspector()
        self._overlay_engine = DiagramOverlayEngine()
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedDrawioArtifacts:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            plan_run = connection.execute(
                """SELECT id, artifact_relative_path FROM diagram_plan_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            revision_rows = connection.execute(
                """SELECT r.diagram_key, r.operations_json, r.version
                FROM diagram_asset_revisions r
                JOIN (SELECT diagram_key, MAX(version) AS version
                      FROM diagram_asset_revisions WHERE task_id = ? GROUP BY diagram_key) latest
                  ON latest.diagram_key = r.diagram_key AND latest.version = r.version
                WHERE r.task_id = ?""", (task_id, task_id)
            ).fetchall()
            if task is None or plan_run is None:
                raise DrawioGenerationError("Diagram plan not found for task")
            if task["status"] not in {TaskStatus.COMPLETED.value,
                                      TaskStatus.COMPLETED_WITH_WARNINGS.value,
                                      TaskStatus.FAILED.value}:
                raise DrawioGenerationError("Task must be completed before diagram generation")
        plan_path = self._data_root / "tasks" / task_id / plan_run["artifact_relative_path"]
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        diagrams = {item["key"]: item for item in payload["diagrams"]}
        overlay_summary = {}
        for row in revision_rows:
            if row["diagram_key"] not in diagrams:
                continue
            applied = self._overlay_engine.prepare(
                diagrams[row["diagram_key"]], json.loads(row["operations_json"])
            )
            diagrams[row["diagram_key"]] = applied.diagram
            overlay_summary[row["diagram_key"]] = {
                "revision_version": row["version"],
                "operation_count": len(applied.operations),
                "conflict_count": len(applied.conflicts),
                "conflicts": list(applied.conflicts),
            }
        required = ("system_architecture", "core_business_flow")
        if any(key not in diagrams for key in required):
            raise DrawioGenerationError("Both required diagram plans must exist")

        now = utc_now()
        stage_id = new_id()
        with UnitOfWork(self._database) as unit_of_work:
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(unit_of_work, record, TaskStatus.RUNNING, now,
                                           current_stage_key="09_generate_diagrams")
            attempt = unit_of_work.stages.next_attempt(task_id, "09_generate_diagrams")
            unit_of_work.stages.start(stage_id, task_id, "09_generate_diagrams", 9, attempt, now)
            version = unit_of_work.diagram_artifacts.next_version(task_id)

        relative_root = Path("artifacts") / "diagrams" / "v{0}".format(version)
        relative_paths = {
            "architecture_drawio": relative_root / "system-architecture.drawio",
            "architecture_svg": relative_root / "system-architecture.svg",
            "workflow_drawio": relative_root / "core-business-flow.drawio",
            "workflow_svg": relative_root / "core-business-flow.svg",
        }
        paths = {key: self._data_root / "tasks" / task_id / value
                 for key, value in relative_paths.items()}
        try:
            architecture = self._builder.build(diagrams["system_architecture"], paths["architecture_drawio"])
            workflow = self._builder.build(diagrams["core_business_flow"], paths["workflow_drawio"])
            architecture["validation"] = self._inspector.require_valid(paths["architecture_drawio"])
            workflow["validation"] = self._inspector.require_valid(paths["workflow_drawio"])
            self._renderer.render(paths["architecture_drawio"], paths["architecture_svg"])
            self._renderer.render(paths["workflow_drawio"], paths["workflow_svg"])
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished = utc_now()
        summary = {"architecture": architecture, "workflow": workflow,
                   "overlays": overlay_summary, "svg_renderer": "internal-v1",
                   "evidence_warnings": {key: diagrams[key].get("missing_information", [])
                                         for key in required
                                         if diagrams[key].get("status") != "ready"}}
        fingerprint = hashlib.sha256("".join(
            hashlib.sha256(paths[key].read_bytes()).hexdigest() for key in sorted(paths)
        ).encode("ascii")).hexdigest()
        run_id = new_id()
        stored_paths = {key: value.as_posix() for key, value in relative_paths.items()}
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.diagram_artifacts.add_run(
                run_id, task_id, plan_run["id"], stage_id, version,
                DRAWIO_GENERATOR_VERSION, summary, stored_paths, fingerprint, finished,
            )
            unit_of_work.stages.succeed(stage_id, fingerprint,
                                        {"run_id": run_id, "version": version}, finished)
            unit_of_work.events.add(task_id, "stage.succeeded", "info",
                                    "Editable Draw.io diagrams generated", summary, finished,
                                    stage_run_id=stage_id)
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(unit_of_work, record, TaskStatus.COMPLETED, finished,
                                           current_stage_key="09_generate_diagrams")
        return PersistedDrawioArtifacts(task_id, run_id, version, paths, summary)

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe = "Diagram generation failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "diagram_generation_error", safe, now)
            record = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, record, TaskStatus.FAILED, now,
                current_stage_key="09_generate_diagrams",
                failure_category="diagram_generation_error", safe_error_message=safe,
            )
