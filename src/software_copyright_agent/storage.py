import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 4

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('directory', 'zip')),
    original_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES project_sources(id),
    root_fingerprint TEXT NOT NULL,
    scanner_version TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    manifest_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES project_sources(id),
    snapshot_id TEXT REFERENCES project_snapshots(id),
    status TEXT NOT NULL,
    current_stage_key TEXT,
    workflow_version TEXT NOT NULL,
    quality_policy_version TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    failure_category TEXT,
    safe_error_message TEXT
);

CREATE TABLE IF NOT EXISTS task_stages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    stage_key TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    input_fingerprint TEXT,
    checkpoint_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    failure_category TEXT,
    safe_error_message TEXT,
    UNIQUE(task_id, stage_key, attempt)
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    stage_run_id TEXT REFERENCES task_stages(id),
    event_type TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id
ON task_events(task_id, id);
"""

MIGRATION_002 = """
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES project_snapshots(id),
    kind TEXT NOT NULL,
    relative_path TEXT,
    locator_json TEXT NOT NULL,
    excerpt TEXT,
    content_hash TEXT,
    extractor TEXT NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    fact_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_facts_task_key
ON facts(task_id, fact_key);

CREATE TABLE IF NOT EXISTS confirmation_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    field_key TEXT NOT NULL,
    question TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL,
    answer_json TEXT,
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_confirmations_task_status
ON confirmation_requests(task_id, status);
"""

MIGRATION_003 = """
ALTER TABLE project_snapshots ADD COLUMN scan_root_mode TEXT;
ALTER TABLE project_snapshots ADD COLUMN scan_root_path TEXT;

CREATE TABLE source_plan_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    rules_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE source_candidates (
    id TEXT PRIMARY KEY,
    plan_run_id TEXT NOT NULL REFERENCES source_plan_runs(id),
    relative_path TEXT NOT NULL,
    grade TEXT NOT NULL CHECK (grade IN ('A', 'B', 'C')),
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    score INTEGER NOT NULL,
    code_lines INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    language TEXT,
    reasons_json TEXT NOT NULL,
    exclusion_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(plan_run_id, relative_path)
);

CREATE INDEX idx_source_candidates_plan_grade
ON source_candidates(plan_run_id, grade, score DESC);
"""

MIGRATION_004 = """
CREATE TABLE code_preview_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_plan_run_id TEXT NOT NULL REFERENCES source_plan_runs(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    formatter_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_code_preview_runs_task_version
ON code_preview_runs(task_id, version DESC);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(MIGRATION_001)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, checksum)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                """,
                (1, "migration-001"),
            )
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 2"
            ).fetchone()
            if applied is None:
                connection.executescript(MIGRATION_002)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                    """,
                    ("migration-002",),
                )
            applied_v3 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 3"
            ).fetchone()
            if applied_v3 is None:
                connection.executescript(MIGRATION_003)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                    """,
                    ("migration-003",),
                )
            applied_v4 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 4"
            ).fetchone()
            if applied_v4 is None:
                connection.executescript(MIGRATION_004)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                    """,
                    ("migration-004",),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
