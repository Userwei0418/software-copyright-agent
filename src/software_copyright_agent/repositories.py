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


class ConfirmationNotFoundError(RepositoryError):
    pass


class ConfirmationAlreadyAnsweredError(RepositoryError):
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
        scan_root_mode: str,
        scan_root_path: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO project_snapshots
            (id, source_id, root_fingerprint, scanner_version, rules_version,
             summary_json, manifest_relative_path, scan_root_mode, scan_root_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                source_id,
                root_fingerprint,
                scanner_version,
                rules_version,
                encode_json(summary),
                manifest_relative_path,
                scan_root_mode,
                scan_root_path,
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

    def next_attempt(self, task_id: str, stage_key: str) -> int:
        row = self._connection.execute(
            """SELECT COALESCE(MAX(attempt), 0) + 1 FROM task_stages
            WHERE task_id = ? AND stage_key = ?""",
            (task_id, stage_key),
        ).fetchone()
        return int(row[0])

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

    def wait_for_user(
        self,
        stage_id: str,
        task_id: str,
        stage_key: str,
        sequence: int,
        attempt: int,
        checkpoint: object,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO task_stages
            (id, task_id, stage_key, sequence, status, attempt, checkpoint_json, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stage_id,
                task_id,
                stage_key,
                sequence,
                StageStatus.WAITING_FOR_USER.value,
                attempt,
                encode_json(checkpoint),
                now,
            ),
        )

    def complete_waiting(self, stage_id: str, checkpoint: object, now: str) -> None:
        cursor = self._connection.execute(
            """UPDATE task_stages
            SET status = ?, checkpoint_json = ?, finished_at = ?
            WHERE id = ? AND status = ?""",
            (
                StageStatus.SUCCEEDED.value,
                encode_json(checkpoint),
                now,
                stage_id,
                StageStatus.WAITING_FOR_USER.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrentUpdateError(
                "Confirmation stage is not waiting: {0}".format(stage_id)
            )

    def find_waiting(self, task_id: str, stage_key: str) -> Optional[str]:
        row = self._connection.execute(
            """SELECT id FROM task_stages
            WHERE task_id = ? AND stage_key = ? AND status = ?
            ORDER BY attempt DESC LIMIT 1""",
            (task_id, stage_key, StageStatus.WAITING_FOR_USER.value),
        ).fetchone()
        return row["id"] if row is not None else None


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


class EvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        evidence_id: str,
        snapshot_id: str,
        candidate: object,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO evidence
            (id, snapshot_id, kind, relative_path, locator_json, excerpt,
             content_hash, extractor, confidence, sensitivity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                snapshot_id,
                candidate.kind,
                candidate.relative_path,
                encode_json(candidate.locator),
                candidate.excerpt,
                candidate.content_hash,
                "deterministic-v1",
                candidate.confidence,
                "normal",
                now,
            ),
        )

    def add_user_confirmation(
        self,
        evidence_id: str,
        snapshot_id: str,
        field_key: str,
        confirmation_id: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO evidence
            (id, snapshot_id, kind, relative_path, locator_json, excerpt,
             content_hash, extractor, confidence, sensitivity, created_at)
            VALUES (?, ?, 'user_confirmation', NULL, ?, NULL, NULL,
                    'user-input-v1', 1.0, 'normal', ?)""",
            (
                evidence_id,
                snapshot_id,
                encode_json(
                    {"field_key": field_key, "confirmation_id": confirmation_id}
                ),
                now,
            ),
        )


class FactRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        fact_id: str,
        task_id: str,
        candidate: object,
        evidence_ids: object,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO facts
            (id, task_id, fact_key, value_json, status, source, confidence,
             evidence_ids_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fact_id,
                task_id,
                candidate.key,
                encode_json(candidate.value),
                candidate.status,
                "deterministic",
                candidate.confidence,
                encode_json(evidence_ids),
                now,
            ),
        )

    def supersede_active(self, task_id: str, fact_key: str) -> int:
        cursor = self._connection.execute(
            """UPDATE facts SET status = 'superseded'
            WHERE task_id = ? AND fact_key = ? AND status IN ('candidate', 'confirmed')""",
            (task_id, fact_key),
        )
        return cursor.rowcount

    def add_user_confirmed(
        self,
        fact_id: str,
        task_id: str,
        fact_key: str,
        value: object,
        evidence_id: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO facts
            (id, task_id, fact_key, value_json, status, source, confidence,
             evidence_ids_json, created_at, confirmed_at)
            VALUES (?, ?, ?, ?, 'confirmed', 'user', 1.0, ?, ?, ?)""",
            (
                fact_id,
                task_id,
                fact_key,
                encode_json(value),
                encode_json([evidence_id]),
                now,
                now,
            ),
        )


class ConfirmationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        confirmation_id: str,
        task_id: str,
        candidate: object,
        evidence_ids: object,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO confirmation_requests
            (id, task_id, field_key, question, candidates_json, evidence_ids_json,
             required, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                confirmation_id,
                task_id,
                candidate.field_key,
                candidate.question,
                encode_json(candidate.candidates),
                encode_json(evidence_ids),
                1 if candidate.required else 0,
                now,
            ),
        )

    def get_pending(self, task_id: str, field_key: str) -> sqlite3.Row:
        rows = self._connection.execute(
            """SELECT id, task_id, field_key, required, status
            FROM confirmation_requests
            WHERE task_id = ? AND field_key = ? AND status = 'pending'
            ORDER BY created_at, id""",
            (task_id, field_key),
        ).fetchall()
        if not rows:
            existing = self._connection.execute(
                """SELECT status FROM confirmation_requests
                WHERE task_id = ? AND field_key = ? ORDER BY created_at DESC LIMIT 1""",
                (task_id, field_key),
            ).fetchone()
            if existing is not None:
                raise ConfirmationAlreadyAnsweredError(
                    "Confirmation is not pending: {0}".format(field_key)
                )
            raise ConfirmationNotFoundError(
                "Pending confirmation not found: {0}".format(field_key)
            )
        if len(rows) > 1:
            raise RepositoryError(
                "Multiple pending confirmations found: {0}".format(field_key)
            )
        return rows[0]

    def answer(self, confirmation_id: str, answer: object, now: str) -> None:
        cursor = self._connection.execute(
            """UPDATE confirmation_requests
            SET status = 'answered', answer_json = ?, answered_at = ?
            WHERE id = ? AND status = 'pending'""",
            (encode_json(answer), now, confirmation_id),
        )
        if cursor.rowcount != 1:
            raise ConfirmationAlreadyAnsweredError(
                "Confirmation changed before it could be answered"
            )

    def pending_required_count(self, task_id: str) -> int:
        return self._connection.execute(
            """SELECT COUNT(*) FROM confirmation_requests
            WHERE task_id = ? AND required = 1 AND status = 'pending'""",
            (task_id,),
        ).fetchone()[0]


class SourcePlanRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM source_plan_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def add_run(
        self,
        run_id: str,
        task_id: str,
        stage_run_id: str,
        version: int,
        rules_version: str,
        summary: object,
        artifact_relative_path: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO source_plan_runs
            (id, task_id, stage_run_id, version, rules_version, summary_json,
             artifact_relative_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, task_id, stage_run_id, version, rules_version,
                encode_json(summary), artifact_relative_path, now,
            ),
        )

    def add_candidate(self, candidate_id: str, run_id: str, candidate: object, now: str) -> None:
        self._connection.execute(
            """INSERT INTO source_candidates
            (id, plan_run_id, relative_path, grade, selected, score, code_lines,
             byte_size, language, reasons_json, exclusion_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id,
                run_id,
                candidate.relative_path,
                candidate.grade,
                1 if candidate.selected else 0,
                candidate.score,
                candidate.code_lines,
                candidate.byte_size,
                candidate.language,
                encode_json(candidate.reasons),
                candidate.exclusion_code,
                now,
            ),
        )


class CodePreviewRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM code_preview_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def add_run(
        self,
        run_id: str,
        task_id: str,
        source_plan_run_id: str,
        stage_run_id: str,
        version: int,
        formatter_version: str,
        config: object,
        summary: object,
        artifact_relative_path: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO code_preview_runs
            (id, task_id, source_plan_run_id, stage_run_id, version,
             formatter_version, config_json, summary_json, artifact_relative_path,
             created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                task_id,
                source_plan_run_id,
                stage_run_id,
                version,
                formatter_version,
                encode_json(config),
                encode_json(summary),
                artifact_relative_path,
                now,
            ),
        )


class SourceDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM source_document_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def add_run(
        self,
        run_id: str,
        task_id: str,
        code_preview_run_id: str,
        stage_run_id: str,
        version: int,
        generator_version: str,
        template: object,
        summary: object,
        artifact_relative_path: str,
        sha256: str,
        now: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO source_document_runs
            (id, task_id, code_preview_run_id, stage_run_id, version,
             generator_version, template_json, summary_json,
             artifact_relative_path, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, task_id, code_preview_run_id, stage_run_id, version,
                generator_version, encode_json(template), encode_json(summary),
                artifact_relative_path, sha256, now,
            ),
        )


class SourceDocumentQaRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM source_document_qa_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def add_run(self, run_id: str, task_id: str, document_run_id: str,
                stage_run_id: str, version: int, policy_version: str,
                passed: bool, checks: object, summary: object,
                report_relative_path: str, render_relative_path: str, now: str) -> None:
        self._connection.execute(
            """INSERT INTO source_document_qa_runs
            (id, task_id, source_document_run_id, stage_run_id, version,
             policy_version, passed, checks_json, summary_json,
             report_relative_path, render_relative_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, document_run_id, stage_run_id, version,
             policy_version, 1 if passed else 0, encode_json(checks),
             encode_json(summary), report_relative_path, render_relative_path, now),
        )


class ManualPlanRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM manual_plan_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def add_run(self, run_id: str, task_id: str, stage_run_id: str, version: int,
                rules_version: str, summary: object, artifact_relative_path: str,
                fingerprint: str, now: str) -> None:
        self._connection.execute(
            """INSERT INTO manual_plan_runs
            (id, task_id, stage_run_id, version, rules_version, summary_json,
             artifact_relative_path, fingerprint, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, stage_run_id, version, rules_version,
             encode_json(summary), artifact_relative_path, fingerprint, now),
        )
