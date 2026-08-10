import json
from typing import Optional

from .storage import Database


class InspectionError(ValueError):
    pass


class InspectionService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def inspect(self, task_id: Optional[str] = None) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            if task_id is None:
                row = connection.execute(
                    "SELECT id FROM tasks ORDER BY created_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    raise InspectionError("No tasks found")
                task_id = row["id"]
            task = connection.execute(
                """SELECT id, status, current_stage_key, row_version, snapshot_id,
                created_at, updated_at FROM tasks WHERE id = ?""",
                (task_id,),
            ).fetchone()
            if task is None:
                raise InspectionError("Task not found: {0}".format(task_id))

            fact_rows = connection.execute(
                """SELECT id, fact_key, value_json, status, source, confidence,
                evidence_ids_json FROM facts WHERE task_id = ?
                ORDER BY fact_key, created_at, id""",
                (task_id,),
            ).fetchall()
            confirmation_rows = connection.execute(
                """SELECT id, field_key, question, candidates_json, evidence_ids_json,
                required, status, answer_json FROM confirmation_requests
                WHERE task_id = ? ORDER BY created_at, id""",
                (task_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """SELECT id, kind, relative_path, locator_json, excerpt, content_hash,
                extractor, confidence FROM evidence WHERE snapshot_id = ?
                ORDER BY relative_path, id""",
                (task["snapshot_id"],),
            ).fetchall()

        return {
            "task": {
                "id": task["id"],
                "status": task["status"],
                "current_stage_key": task["current_stage_key"],
                "row_version": task["row_version"],
                "snapshot_id": task["snapshot_id"],
            },
            "facts": [
                {
                    "id": row["id"],
                    "key": row["fact_key"],
                    "value": json.loads(row["value_json"]),
                    "status": row["status"],
                    "source": row["source"],
                    "confidence": row["confidence"],
                    "evidence_ids": json.loads(row["evidence_ids_json"]),
                }
                for row in fact_rows
            ],
            "confirmations": [
                {
                    "id": row["id"],
                    "field_key": row["field_key"],
                    "question": row["question"],
                    "candidates": json.loads(row["candidates_json"]),
                    "evidence_ids": json.loads(row["evidence_ids_json"]),
                    "required": bool(row["required"]),
                    "status": row["status"],
                    "answer": json.loads(row["answer_json"])
                    if row["answer_json"] is not None
                    else None,
                }
                for row in confirmation_rows
            ],
            "evidence": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "relative_path": row["relative_path"],
                    "locator": json.loads(row["locator_json"]),
                    "excerpt": row["excerpt"],
                    "content_hash": row["content_hash"],
                    "extractor": row["extractor"],
                    "confidence": row["confidence"],
                }
                for row in evidence_rows
            ],
        }
