import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .diagram_asset import DiagramAssetError, DiagramOverlayEngine, OverlayResult
from .drawio_document import DrawioDocumentBuilder, DrawioDocumentInspector, InternalSvgRenderer
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
    preview_paths: dict


class DiagramAssetService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._engine = DiagramOverlayEngine()
        self._builder = DrawioDocumentBuilder()
        self._inspector = DrawioDocumentInspector()
        self._renderer = InternalSvgRenderer()

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
            drawio_relative = relative.with_suffix(".drawio")
            svg_relative = relative.with_suffix(".svg")
            drawio_path = self._data_root / "tasks" / task_id / drawio_relative
            svg_path = self._data_root / "tasks" / task_id / svg_relative
            render_summary = self._builder.build(result.diagram, drawio_path)
            render_summary["validation"] = self._inspector.require_valid(drawio_path)
            self._renderer.render(drawio_path, svg_path)
            payload = {
                "schema_version": 1, "revision_id": revision_id, "task_id": task_id,
                "diagram_key": diagram_key, "version": version,
                "base_artifact_run_id": artifact["id"], "parent_revision_id": parent_id,
                "edit_source": edit_source, "status": status,
                "semantic_fingerprint": result.semantic_fingerprint,
                "operations": list(result.operations), "conflicts": list(result.conflicts),
                "diagram": result.diagram,
                "preview": {
                    "drawio_relative_path": drawio_relative.as_posix(),
                    "svg_relative_path": svg_relative.as_posix(),
                    "summary": render_summary,
                },
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
            revision_id, task_id, diagram_key, version, status, path, result,
            {"drawio": drawio_path, "svg": svg_path},
        )

    def workspace_snapshot(self, task_id: str) -> dict:
        self._database.initialize()
        assets = []
        for diagram_key, title in (("system_architecture", "系统总体架构图"),
                                   ("core_business_flow", "核心业务流程图")):
            revisions = self.list_revisions(task_id, diagram_key)
            latest = revisions[0] if revisions else None
            assets.append({
                "diagram_key": diagram_key, "title": title,
                "latest_revision": latest,
                "revision_count": len(revisions),
                "editable": True,
                "supported_actions": [
                    "node.move", "node.resize", "node.style", "node.label", "node.hide",
                    "edge.route", "edge.style", "edge.label",
                ],
            })
        return {"task_id": task_id, "assets": assets}

    def list_revisions(self, task_id: str, diagram_key: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id, version, edit_source, status, base_artifact_run_id,
                parent_revision_id, semantic_fingerprint, artifact_relative_path,
                operations_json, conflicts_json, created_at
                FROM diagram_asset_revisions WHERE task_id = ? AND diagram_key = ?
                ORDER BY version DESC""", (task_id, diagram_key)
            ).fetchall()
        return [{
            "revision_id": row["id"], "version": row["version"],
            "edit_source": row["edit_source"], "status": row["status"],
            "base_artifact_run_id": row["base_artifact_run_id"],
            "parent_revision_id": row["parent_revision_id"],
            "semantic_fingerprint": row["semantic_fingerprint"],
            "artifact_path": str(self._data_root / "tasks" / task_id /
                                 row["artifact_relative_path"]),
            "operation_count": len(json.loads(row["operations_json"])),
            "conflict_count": len(json.loads(row["conflicts_json"])),
            "created_at": row["created_at"],
        } for row in rows]

    def get_revision(self, revision_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT task_id, artifact_relative_path FROM diagram_asset_revisions
                WHERE id = ?""", (revision_id,)
            ).fetchone()
        if row is None:
            raise DiagramAssetError("Diagram revision not found")
        path = self._data_root / "tasks" / row["task_id"] / row["artifact_relative_path"]
        return json.loads(path.read_text(encoding="utf-8"))

    def rebase_latest(self, task_id: str, diagram_key: str) -> PersistedDiagramRevision:
        revision = self._revision_row(task_id=task_id, diagram_key=diagram_key)
        if revision is None:
            raise DiagramAssetError("Diagram revision not found")
        return self.create_revision(
            task_id, diagram_key, json.loads(revision["operations_json"]),
            revision["edit_source"],
        )

    def resolve_conflicts(self, revision_id: str,
                          resolutions: Iterable[dict]) -> PersistedDiagramRevision:
        row = self._revision_row(revision_id=revision_id)
        if row is None:
            raise DiagramAssetError("Diagram revision not found")
        operations = json.loads(row["operations_json"])
        conflicts = json.loads(row["conflicts_json"])
        if not conflicts:
            raise DiagramAssetError("Diagram revision has no conflicts")
        by_index = {}
        for resolution in resolutions:
            if not isinstance(resolution, dict) or not isinstance(
                    resolution.get("operation_index"), int):
                raise DiagramAssetError("Conflict resolution operation_index is required")
            action = resolution.get("resolution")
            if action not in {"drop", "accept_current", "retarget"}:
                raise DiagramAssetError("Unsupported conflict resolution")
            if resolution["operation_index"] in by_index:
                raise DiagramAssetError("Conflict resolution is duplicated")
            by_index[resolution["operation_index"]] = resolution
        conflict_indexes = {item["operation_index"] for item in conflicts}
        if set(by_index) != conflict_indexes:
            raise DiagramAssetError("Every conflict must have exactly one resolution")
        resolved = []
        for index, operation in enumerate(operations):
            if index not in conflict_indexes:
                resolved.append(operation)
                continue
            resolution = by_index[index]
            if resolution["resolution"] == "drop":
                continue
            item = dict(operation)
            if resolution["resolution"] == "retarget":
                target = resolution.get("target")
                if not isinstance(target, str) or not target:
                    raise DiagramAssetError("Retarget resolution requires target")
                item["target"] = target
            item.pop("expected_target_fingerprint", None)
            resolved.append(item)
        return self.create_revision(
            row["task_id"], row["diagram_key"], resolved, "manual"
        )

    def rollback_to(self, task_id: str, diagram_key: str,
                    version: int) -> PersistedDiagramRevision:
        row = self._revision_row(task_id=task_id, diagram_key=diagram_key, version=version)
        if row is None:
            raise DiagramAssetError("Diagram revision version not found")
        return self.create_revision(
            task_id, diagram_key, json.loads(row["operations_json"]), "manual"
        )

    def _revision_row(self, revision_id: Optional[str] = None,
                      task_id: Optional[str] = None,
                      diagram_key: Optional[str] = None,
                      version: Optional[int] = None):
        self._database.initialize()
        with self._database.connect() as connection:
            if revision_id is not None:
                return connection.execute(
                    "SELECT * FROM diagram_asset_revisions WHERE id = ?", (revision_id,)
                ).fetchone()
            if task_id is None or diagram_key is None:
                raise DiagramAssetError("Task and diagram key are required")
            if version is None:
                return connection.execute(
                    """SELECT * FROM diagram_asset_revisions
                    WHERE task_id = ? AND diagram_key = ?
                    ORDER BY version DESC LIMIT 1""", (task_id, diagram_key)
                ).fetchone()
            return connection.execute(
                """SELECT * FROM diagram_asset_revisions
                WHERE task_id = ? AND diagram_key = ? AND version = ?""",
                (task_id, diagram_key, version),
            ).fetchone()

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
