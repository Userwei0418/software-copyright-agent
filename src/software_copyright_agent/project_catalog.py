import json

from .storage import Database


class ProjectCatalogService:
    def __init__(self, database: Database) -> None:
        self._database = database

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
