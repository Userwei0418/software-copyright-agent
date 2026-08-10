import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .domain import PersistedScan, SourceKind, TaskStatus
from .scanner import ProjectScanner
from .storage import Database, encode_json


WORKFLOW_VERSION = "mvp-1"
QUALITY_POLICY_VERSION = "mvp-1"
SCANNER_VERSION = "0.1.0"
RULES_VERSION = "0.1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


class ScanProjectService:
    def __init__(self, database: Database, data_root: Path, scanner: ProjectScanner = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._scanner = scanner or ProjectScanner()

    def execute(self, project_root: Path) -> PersistedScan:
        self._database.initialize()
        result = self._scanner.scan(project_root)
        source_id = new_id()
        snapshot_id = new_id()
        task_id = new_id()
        stage_id = new_id()
        now = utc_now()

        task_root = self._data_root / "tasks" / task_id
        manifest_relative = Path("input") / "manifest.jsonl"
        manifest_path = task_root / manifest_relative
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest_atomic(manifest_path, result)

        summary = {
            "file_count": len(result.files),
            "ignored_count": result.ignored_count,
            "skipped_symlink_count": result.skipped_symlink_count,
            "total_bytes": result.total_bytes,
        }

        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO project_sources
                (id, kind, original_path, display_name, created_at, last_opened_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    source_id,
                    SourceKind.DIRECTORY.value,
                    str(result.root),
                    result.root.name,
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO project_snapshots
                (id, source_id, root_fingerprint, scanner_version, rules_version,
                 summary_json, manifest_relative_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    source_id,
                    result.root_fingerprint,
                    SCANNER_VERSION,
                    RULES_VERSION,
                    encode_json(summary),
                    manifest_relative.as_posix(),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO tasks
                (id, source_id, snapshot_id, status, current_stage_key,
                 workflow_version, quality_policy_version, created_at, started_at,
                 finished_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    source_id,
                    snapshot_id,
                    TaskStatus.COMPLETED.value,
                    "02_scan",
                    WORKFLOW_VERSION,
                    QUALITY_POLICY_VERSION,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO task_stages
                (id, task_id, stage_key, sequence, status, attempt,
                 input_fingerprint, checkpoint_json, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    stage_id,
                    task_id,
                    "02_scan",
                    2,
                    "succeeded",
                    1,
                    result.root_fingerprint,
                    encode_json({"snapshot_id": snapshot_id}),
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO task_events
                (task_id, stage_run_id, event_type, level, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    stage_id,
                    "stage.succeeded",
                    "info",
                    "Project scan completed",
                    encode_json(summary),
                    now,
                ),
            )

        return PersistedScan(
            task_id=task_id,
            source_id=source_id,
            snapshot_id=snapshot_id,
            manifest_path=manifest_path,
            result=result,
        )

    @staticmethod
    def _write_manifest_atomic(path: Path, result: object) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="manifest-", suffix=".tmp", dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                for item in result.files:
                    stream.write(
                        json.dumps(
                            {
                                "path": item.relative_path,
                                "size": item.size,
                                "modified_ns": item.modified_ns,
                                "sha256": item.sha256,
                                "category": item.category.value,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
