import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .diagram_asset import DiagramAssetError, DiagramOverlayEngine, OverlayResult
from .service import new_id, utc_now
from .storage import Database
from .unit_of_work import UnitOfWork


@dataclass(frozen=True)
class PersistedDiagramRevision:
    revision_id: str
    task_id: str
    diagram_key: str
    version: int
    status: str
    artifact_path: Path
    result: OverlayResult


class DiagramAssetService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._engine = DiagramOverlayEngine()

    def create_revision(self, task_id: str, diagram_key: str,
                        operations: Iterable[dict], edit_source: str) -> PersistedDiagramRevision:
        if edit_source not in {"manual", "ai"}:
            raise DiagramAssetError("Edit source must be manual or ai")
        self._database.initialize()
        with self._database.connect() as connection:
            artifact = connection.execute(
                """SELECT a.id, p.artifact_relative_path
                FROM diagram_artifact_runs a
                JOIN diagram_plan_runs p ON p.id = a.diagram_plan_run_id
                WHERE a.task_id = ? ORDER BY a.version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            if artifact is None:
                raise DiagramAssetError("Generated diagram artifact not found for task")
        plan_path = self._data_root / "tasks" / task_id / artifact["artifact_relative_path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        diagram = next((item for item in plan.get("diagrams", [])
                        if item.get("key") == diagram_key), None)
        if diagram is None:
            raise DiagramAssetError("Diagram not found: {0}".format(diagram_key))
        result = self._engine.prepare(diagram, operations)
        status = "conflicted" if result.conflicts else "clean"
        revision_id, now = new_id(), utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            version = unit_of_work.diagram_asset_revisions.next_version(task_id, diagram_key)
            parent_id = unit_of_work.diagram_asset_revisions.latest_id(task_id, diagram_key)
            relative = (Path("intermediate") / "diagram-assets" / diagram_key /
                        "revision.v{0}.json".format(version))
            path = self._data_root / "tasks" / task_id / relative
            payload = {
                "schema_version": 1, "revision_id": revision_id, "task_id": task_id,
                "diagram_key": diagram_key, "version": version,
                "base_artifact_run_id": artifact["id"], "parent_revision_id": parent_id,
                "edit_source": edit_source, "status": status,
                "semantic_fingerprint": result.semantic_fingerprint,
                "operations": list(result.operations), "conflicts": list(result.conflicts),
                "diagram": result.diagram,
            }
            self._write_json_atomic(path, payload)
            unit_of_work.diagram_asset_revisions.add(
                revision_id, task_id, diagram_key, artifact["id"], parent_id,
                version, edit_source, result.semantic_fingerprint,
                result.operations, result.conflicts, status, relative.as_posix(), now,
            )
            unit_of_work.events.add(
                task_id, "diagram.revision.created",
                "warning" if result.conflicts else "info",
                "Diagram asset revision created",
                {"diagram_key": diagram_key, "version": version, "status": status,
                 "operation_count": len(result.operations),
                 "conflict_count": len(result.conflicts)}, now,
            )
        return PersistedDiagramRevision(
            revision_id, task_id, diagram_key, version, status, path, result
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="diagram-revision-", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
