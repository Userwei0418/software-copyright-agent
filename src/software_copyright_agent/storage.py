import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 15

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

MIGRATION_005 = """
CREATE TABLE source_document_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    code_preview_run_id TEXT NOT NULL REFERENCES code_preview_runs(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    generator_version TEXT NOT NULL,
    template_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_source_document_runs_task_version
ON source_document_runs(task_id, version DESC);
"""

MIGRATION_006 = """
CREATE TABLE source_document_qa_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_document_run_id TEXT NOT NULL REFERENCES source_document_runs(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    checks_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    report_relative_path TEXT NOT NULL,
    render_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_source_document_qa_task_version
ON source_document_qa_runs(task_id, version DESC);
"""

MIGRATION_007 = """
CREATE TABLE manual_plan_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    rules_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_manual_plan_runs_task_version
ON manual_plan_runs(task_id, version DESC);
"""

MIGRATION_008 = """
CREATE TABLE diagram_plan_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    manual_plan_run_id TEXT NOT NULL REFERENCES manual_plan_runs(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    rules_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_diagram_plan_runs_task_version
ON diagram_plan_runs(task_id, version DESC);
"""

MIGRATION_009 = """
CREATE TABLE diagram_artifact_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    diagram_plan_run_id TEXT NOT NULL REFERENCES diagram_plan_runs(id),
    stage_run_id TEXT NOT NULL REFERENCES task_stages(id),
    version INTEGER NOT NULL,
    generator_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    architecture_drawio_relative_path TEXT NOT NULL,
    architecture_svg_relative_path TEXT NOT NULL,
    workflow_drawio_relative_path TEXT NOT NULL,
    workflow_svg_relative_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_diagram_artifact_runs_task_version
ON diagram_artifact_runs(task_id, version DESC);
"""

MIGRATION_010 = """
CREATE TABLE diagram_asset_revisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    diagram_key TEXT NOT NULL,
    base_artifact_run_id TEXT NOT NULL REFERENCES diagram_artifact_runs(id),
    parent_revision_id TEXT REFERENCES diagram_asset_revisions(id),
    version INTEGER NOT NULL,
    edit_source TEXT NOT NULL CHECK (edit_source IN ('manual', 'ai')),
    semantic_fingerprint TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    conflicts_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('clean', 'conflicted')),
    artifact_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, diagram_key, version)
);

CREATE INDEX idx_diagram_asset_revisions_latest
ON diagram_asset_revisions(task_id, diagram_key, version DESC);
"""

MIGRATION_011 = """
CREATE TABLE model_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    protocol_id TEXT NOT NULL CHECK (protocol_id IN ('openai_compatible', 'anthropic', 'ollama')),
    base_url TEXT NOT NULL,
    model_name TEXT NOT NULL,
    credential_ref TEXT,
    settings_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_model_configs_enabled ON model_configs(enabled, updated_at DESC);
"""

MIGRATION_012 = """
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATION_013 = """
CREATE TABLE model_credentials (
    provider_id TEXT PRIMARY KEY,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATION_014 = """
CREATE TABLE manual_draft_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    model_config_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    summary_json TEXT NOT NULL,
    artifact_relative_path TEXT,
    elapsed_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE INDEX idx_manual_draft_runs_latest ON manual_draft_runs(task_id, version DESC);
"""

MIGRATION_015 = """
CREATE TABLE manual_generation_jobs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    model_config_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'canceled'
    )),
    current_step TEXT,
    progress_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    safe_error_message TEXT,
    UNIQUE(task_id, version)
);
CREATE INDEX idx_manual_generation_jobs_latest
ON manual_generation_jobs(task_id, version DESC);

CREATE TABLE manual_generation_steps (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL CHECK (step_key IN (
        'research', 'draft', 'diagrams', 'screenshots', 'assemble_docx', 'render_qa'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'completed', 'completed_with_warnings', 'failed', 'skipped'
    )),
    attempt INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    safe_error_message TEXT,
    UNIQUE(job_id, step_key, attempt)
);
CREATE INDEX idx_manual_generation_steps_job
ON manual_generation_steps(job_id, step_key, attempt DESC);

CREATE TABLE manual_section_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'generated', 'confirmed', 'needs_review')),
    content_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    inference_notes_json TEXT NOT NULL,
    figure_requests_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, section_key)
);

CREATE TABLE manual_figure_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    figure_key TEXT NOT NULL,
    section_key TEXT NOT NULL,
    figure_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned', 'rendered', 'verified', 'failed')),
    semantic_json TEXT NOT NULL,
    drawio_relative_path TEXT,
    svg_relative_path TEXT,
    png_relative_path TEXT,
    qa_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, figure_key)
);

CREATE TABLE manual_screenshot_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    screenshot_key TEXT NOT NULL,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('automated', 'user')),
    image_relative_path TEXT NOT NULL,
    description_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, screenshot_key)
);

CREATE TABLE manual_document_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('assembled', 'qa_passed', 'qa_failed')),
    docx_relative_path TEXT NOT NULL,
    preview_pdf_relative_path TEXT,
    qa_json TEXT NOT NULL,
    sha256 TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, version)
);
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
            applied_v5 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 5"
            ).fetchone()
            if applied_v5 is None:
                connection.executescript(MIGRATION_005)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                    """,
                    ("migration-005",),
                )
            applied_v6 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 6"
            ).fetchone()
            if applied_v6 is None:
                connection.executescript(MIGRATION_006)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-006",),
                )
            applied_v7 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 7"
            ).fetchone()
            if applied_v7 is None:
                connection.executescript(MIGRATION_007)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-007",),
                )
            applied_v8 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 8"
            ).fetchone()
            if applied_v8 is None:
                connection.executescript(MIGRATION_008)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-008",),
                )
            applied_v9 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 9"
            ).fetchone()
            if applied_v9 is None:
                connection.executescript(MIGRATION_009)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (9, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-009",),
                )
            applied_v10 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 10"
            ).fetchone()
            if applied_v10 is None:
                connection.executescript(MIGRATION_010)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (10, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-010",),
                )
            applied_v11 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 11"
            ).fetchone()
            if applied_v11 is None:
                connection.executescript(MIGRATION_011)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (11, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-011",),
                )
            applied_v12 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 12"
            ).fetchone()
            if applied_v12 is None:
                connection.executescript(MIGRATION_012)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (12, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-012",),
                )
            applied_v13 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 13"
            ).fetchone()
            if applied_v13 is None:
                connection.executescript(MIGRATION_013)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (13, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-013",),
                )
            applied_v14 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 14"
            ).fetchone()
            if applied_v14 is None:
                connection.executescript(MIGRATION_014)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (14, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-014",),
                )
            applied_v15 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 15"
            ).fetchone()
            if applied_v15 is None:
                connection.executescript(MIGRATION_015)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (15, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-015",),
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
