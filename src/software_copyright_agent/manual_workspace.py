import json
from pathlib import Path

from .diagram_plan_service import DiagramPlanService
from .drawio_service import DrawioGenerationService
from .manual_plan_service import ManualPlanService
from .manual_generation import ManualGenerationService
from .storage import Database


class ManualWorkspaceError(ValueError):
    pass


class ManualWorkspaceService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._manual_plan = ManualPlanService(database, data_root)
        self._diagram_plan = DiagramPlanService(database, data_root)
        self._drawio = DrawioGenerationService(database, data_root)
        self._generation = ManualGenerationService(database, data_root)

    def snapshot(self, task_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                """SELECT id, status, current_stage_key, safe_error_message
                FROM tasks WHERE id = ?""", (task_id,)
            ).fetchone()
            if task is None:
                raise ManualWorkspaceError("Task not found: {0}".format(task_id))
            manual = connection.execute(
                """SELECT version, summary_json, artifact_relative_path, created_at
                FROM manual_plan_runs WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            diagram = connection.execute(
                """SELECT version, summary_json, artifact_relative_path, created_at FROM diagram_plan_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            artifacts = connection.execute(
                """SELECT version, summary_json, created_at FROM diagram_artifact_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            draft = connection.execute(
                """SELECT version, summary_json, artifact_relative_path, elapsed_ms, created_at
                FROM manual_draft_runs WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
        manual_payload = self._run(manual)
        if manual_payload is not None:
            path = (self._data_root / "tasks" / task_id /
                    manual["artifact_relative_path"]).resolve()
            task_root = (self._data_root / "tasks" / task_id).resolve()
            if task_root not in path.parents or not path.is_file():
                raise ManualWorkspaceError("Manual plan artifact is unavailable")
            content = json.loads(path.read_text(encoding="utf-8"))
            manual_payload.update({
                "sections": content.get("sections", []),
                "missing_information": content.get("missing_information", []),
                "diagram_requirements": content.get("diagram_requirements", []),
            })
        diagram_payload = self._run(diagram)
        if diagram_payload is not None:
            path = (self._data_root / "tasks" / task_id /
                    diagram["artifact_relative_path"]).resolve()
            task_root = (self._data_root / "tasks" / task_id).resolve()
            if task_root not in path.parents or not path.is_file():
                raise ManualWorkspaceError("Diagram plan artifact is unavailable")
            content = json.loads(path.read_text(encoding="utf-8"))
            diagram_payload["diagrams"] = [{
                "key": item.get("key"), "title": item.get("title"),
                "status": item.get("status"),
                "node_count": len(item.get("nodes", [])),
                "edge_count": len(item.get("edges", [])),
                "missing_information": item.get("missing_information", []),
            } for item in content.get("diagrams", [])]
        allowed = task["status"] in {"completed", "completed_with_warnings"}
        draft_payload = self._run(draft)
        if draft_payload is not None:
            path = (self._data_root / "tasks" / task_id / draft["artifact_relative_path"]).resolve()
            task_root = (self._data_root / "tasks" / task_id).resolve()
            if task_root not in path.parents or not path.is_file():
                raise ManualWorkspaceError("Manual draft artifact is unavailable")
            draft_payload.update({"content": path.read_text(encoding="utf-8"),
                                  "elapsed_ms": draft["elapsed_ms"]})
        return {
            "task": dict(task),
            "manual_plan": manual_payload,
            "diagram_plan": diagram_payload,
            "diagram_artifacts": self._run(artifacts),
            "manual_draft": draft_payload,
            "actions": {
                "manual_plan": allowed,
                "diagram_plan": allowed and manual_payload is not None,
                "diagram_artifacts": allowed and diagram is not None
                and json.loads(diagram["summary_json"]).get("ready_diagrams") == 2,
                "manual_generate": allowed,
            },
        }

    def build_manual_plan(self, task_id: str) -> dict:
        self._manual_plan.execute(task_id)
        return self.snapshot(task_id)

    def build_diagram_plan(self, task_id: str) -> dict:
        self._diagram_plan.execute(task_id)
        return self.snapshot(task_id)

    def build_diagrams(self, task_id: str) -> dict:
        self._drawio.execute(task_id)
        return self.snapshot(task_id)

    def generate_manual(self, task_id: str, model_config_id: str) -> dict:
        self._generation.execute(task_id, model_config_id)
        return self.snapshot(task_id)

    @staticmethod
    def _run(row):
        if row is None:
            return None
        return {"version": row["version"], "summary": json.loads(row["summary_json"]),
                "created_at": row["created_at"]}
