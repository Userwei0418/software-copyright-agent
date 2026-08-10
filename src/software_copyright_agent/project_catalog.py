import json
import shutil
from pathlib import Path

from .storage import Database


class ProjectCatalogService:
    def __init__(self, database: Database, data_root: Path = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve() if data_root else None

    def list_recent(self, limit: int = 20) -> list:
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("Recent task limit must be between 1 and 100")
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT t.id, t.snapshot_id, t.status, t.current_stage_key,
                t.created_at, t.updated_at, s.display_name, s.kind,
                p.summary_json
                FROM tasks t
                JOIN project_sources s ON s.id = t.source_id
                LEFT JOIN project_snapshots p ON p.id = t.snapshot_id
                ORDER BY t.updated_at DESC, t.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            stored = json.loads(row["summary_json"]) if row["summary_json"] else None
            summary = None if stored is None else {
                "file_count": stored.get("file_count", 0),
                "ignored_count": stored.get("ignored_count", 0),
                "total_bytes": stored.get("total_bytes", 0),
                "secret_finding_count": stored.get("secret_finding_count", 0),
                "languages": sorted(stored.get("languages", {}).keys()),
            }
            items.append({
                "task_id": row["id"], "snapshot_id": row["snapshot_id"],
                "display_name": row["display_name"], "source_kind": row["kind"],
                "status": row["status"],
                "current_stage_key": row["current_stage_key"],
                "summary": summary,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            })
        return items

    def delete_task(self, task_id: str) -> None:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT source_id, snapshot_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise ValueError("Task not found: {0}".format(task_id))
            for table in (
                "diagram_asset_revisions", "diagram_artifact_runs", "diagram_plan_runs",
                "manual_plan_runs", "source_document_qa_runs", "source_document_runs",
                "code_preview_runs",
            ):
                connection.execute("DELETE FROM {0} WHERE task_id = ?".format(table), (task_id,))
            plan_ids = [row[0] for row in connection.execute(
                "SELECT id FROM source_plan_runs WHERE task_id = ?", (task_id,)
            ).fetchall()]
            for plan_id in plan_ids:
                connection.execute("DELETE FROM source_candidates WHERE plan_run_id = ?", (plan_id,))
            for table in ("source_plan_runs", "confirmation_requests", "facts", "task_events",
                          "task_stages"):
                connection.execute("DELETE FROM {0} WHERE task_id = ?".format(table), (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            snapshot_id = task["snapshot_id"]
            if snapshot_id and connection.execute(
                "SELECT 1 FROM tasks WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone() is None:
                connection.execute("DELETE FROM evidence WHERE snapshot_id = ?", (snapshot_id,))
                connection.execute("DELETE FROM project_snapshots WHERE id = ?", (snapshot_id,))
            source_id = task["source_id"]
            if connection.execute(
                "SELECT 1 FROM tasks WHERE source_id = ?", (source_id,)
            ).fetchone() is None and connection.execute(
                "SELECT 1 FROM project_snapshots WHERE source_id = ?", (source_id,)
            ).fetchone() is None:
                connection.execute("DELETE FROM project_sources WHERE id = ?", (source_id,))
        if self._data_root is not None:
            task_path = (self._data_root / "tasks" / task_id).resolve()
            tasks_root = (self._data_root / "tasks").resolve()
            if tasks_root in task_path.parents and task_path.is_dir():
                shutil.rmtree(task_path)
