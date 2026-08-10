import sqlite3
from dataclasses import dataclass
from typing import Optional

from .domain import SourceKind, StageStatus, TaskStatus
from .storage import encode_json


class RepositoryError(RuntimeError):
    pass


class TaskNotFoundError(RepositoryError):
    pass


class ConcurrentUpdateError(RepositoryError):
    pass


@dataclass(frozen=True)
class TaskRecord:
    id: str
    source_id: str
    snapshot_id: Optional[str]
    status: TaskStatus
    current_stage_key: Optional[str]
    row_version: int


class SourceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        source_id: str,
        kind: SourceKind,
        path: str,
        display_name: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO project_sources
            (id, kind, original_path, display_name, created_at, last_opened_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, kind.value, path, display_name, now, now),
        )

    def add_directory(self, source_id: str, path: str, display_name: str, now: str) -> None:
        self.add(source_id, SourceKind.DIRECTORY, path, display_name, now)


class SnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        snapshot_id: str,
        source_id: str,
        root_fingerprint: str,
        scanner_version: str,
        rules_version: str,
        summary: object,
        manifest_relative_path: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO project_snapshots
            (id, source_id, root_fingerprint, scanner_version, rules_version,
             summary_json, manifest_relative_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                source_id,
                root_fingerprint,
                scanner_version,
                rules_version,
                encode_json(summary),
                manifest_relative_path,
                now,
            ),
        )


class TaskRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        task_id: str,
        source_id: str,
        workflow_version: str,
        quality_policy_version: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO tasks
            (id, source_id, status, workflow_version, quality_policy_version,
             row_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                task_id,
                source_id,
                TaskStatus.CREATED.value,
                workflow_version,
                quality_policy_version,
                now,
                now,
            ),
        )

    def get(self, task_id: str) -> TaskRecord:
        row = self._connection.execute(
            """SELECT id, source_id, snapshot_id, status, current_stage_key, row_version
            FROM tasks WHERE id = ?""",
            (task_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError("Task not found: {0}".format(task_id))
        return TaskRecord(
            id=row["id"],
            source_id=row["source_id"],
            snapshot_id=row["snapshot_id"],
            status=TaskStatus(row["status"]),
            current_stage_key=row["current_stage_key"],
            row_version=row["row_version"],
        )

    def transition(
        self,
        task_id: str,
        expected_version: int,
        from_status: TaskStatus,
        to_status: TaskStatus,
        now: str,
        current_stage_key: Optional[str] = None,
        failure_category: Optional[str] = None,
        safe_error_message: Optional[str] = None,
    ) -> TaskRecord:
        started_at = now if to_status == TaskStatus.RUNNING else None
        finished_at = now if to_status in {
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
            TaskStatus.COMPLETED,
            TaskStatus.COMPLETED_WITH_WARNINGS,
        } else None
        cursor = self._connection.execute(
            """UPDATE tasks
            SET status = ?, current_stage_key = ?, row_version = row_version + 1,
                updated_at = ?, started_at = COALESCE(started_at, ?),
                finished_at = ?, failure_category = ?, safe_error_message = ?
            WHERE id = ? AND row_version = ? AND status = ?""",
            (
                to_status.value,
                current_stage_key,
                now,
                started_at,
                finished_at,
                failure_category,
                safe_error_message,
                task_id,
                expected_version,
                from_status.value,
            ),
        )
        if cursor.rowcount != 1:
            current = self._connection.execute(
                "SELECT id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise TaskNotFoundError("Task not found: {0}".format(task_id))
            raise ConcurrentUpdateError(
                "Task {0} changed since version {1}".format(task_id, expected_version)
            )
        return self.get(task_id)

    def attach_snapshot(self, task_id: str, snapshot_id: str) -> None:
        self._connection.execute(
            "UPDATE tasks SET snapshot_id = ? WHERE id = ?",
            (snapshot_id, task_id),
        )


class StageRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def start(
        self,
        stage_id: str,
        task_id: str,
        stage_key: str,
        sequence: int,
        attempt: int,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO task_stages
            (id, task_id, stage_key, sequence, status, attempt, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                stage_id,
                task_id,
                stage_key,
                sequence,
                StageStatus.RUNNING.value,
                attempt,
                now,
            ),
        )

    def succeed(
        self,
        stage_id: str,
        input_fingerprint: str,
        checkpoint: object,
        now: str,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE task_stages
            SET status = ?, input_fingerprint = ?, checkpoint_json = ?, finished_at = ?
            WHERE id = ? AND status = ?""",
            (
                StageStatus.SUCCEEDED.value,
                input_fingerprint,
                encode_json(checkpoint),
                now,
                stage_id,
                StageStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrentUpdateError("Stage is not running: {0}".format(stage_id))

    def fail(self, stage_id: str, category: str, safe_message: str, now: str) -> None:
        self._connection.execute(
            """UPDATE task_stages
            SET status = ?, failure_category = ?, safe_error_message = ?, finished_at = ?
            WHERE id = ? AND status = ?""",
            (
                StageStatus.FAILED.value,
                category,
                safe_message,
                now,
                stage_id,
                StageStatus.RUNNING.value,
            ),
        )


class EventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        task_id: str,
        event_type: str,
        level: str,
        message: str,
        payload: object,
        now: str,
        stage_run_id: Optional[str] = None,
    ) -> None:
        self._connection.execute(
            """INSERT INTO task_events
            (task_id, stage_run_id, event_type, level, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                stage_run_id,
                event_type,
                level,
                message,
                encode_json(payload),
                now,
            ),
        )
