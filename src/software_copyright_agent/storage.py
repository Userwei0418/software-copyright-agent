import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 31

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

MIGRATION_016 = """
CREATE TABLE manual_research_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'completed_with_warnings')),
    project_profile_json TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    artifact_relative_path TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    model_name TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, version)
);
CREATE INDEX idx_manual_research_artifacts_latest
ON manual_research_artifacts(job_id, version DESC);
"""

MIGRATION_017 = """
CREATE TABLE manual_section_revisions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('ai', 'user')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('generated', 'confirmed', 'needs_review')),
    content_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    inference_notes_json TEXT NOT NULL,
    figure_requests_json TEXT NOT NULL,
    model_name TEXT,
    prompt_fingerprint TEXT,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, section_key, version)
);
CREATE INDEX idx_manual_section_revisions_latest
ON manual_section_revisions(job_id, section_key, version DESC);
"""

MIGRATION_018 = """
CREATE TABLE manual_figure_revisions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    figure_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    figure_type TEXT NOT NULL,
    semantic_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    drawio_relative_path TEXT NOT NULL,
    svg_relative_path TEXT NOT NULL,
    png_relative_path TEXT NOT NULL,
    qa_json TEXT NOT NULL,
    model_name TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, figure_key, version)
);
CREATE INDEX idx_manual_figure_revisions_latest
ON manual_figure_revisions(job_id, figure_key, version DESC);
"""

MIGRATION_019 = """
CREATE TABLE manual_capture_assessments (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('auto_available', 'manual_import', 'not_applicable')),
    assessment_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, version)
);
CREATE INDEX idx_manual_capture_assessments_latest
ON manual_capture_assessments(job_id, version DESC);

CREATE TABLE manual_screenshot_revisions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    screenshot_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('automated', 'user')),
    image_relative_path TEXT NOT NULL,
    description_json TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, screenshot_key, version)
);
CREATE INDEX idx_manual_screenshot_revisions_latest
ON manual_screenshot_revisions(job_id, screenshot_key, version DESC);
"""

MIGRATION_020 = """
CREATE TABLE manual_document_qa_runs (
    id TEXT PRIMARY KEY,
    document_artifact_id TEXT NOT NULL REFERENCES manual_document_artifacts(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    qa_version INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    renderer_kind TEXT NOT NULL CHECK (renderer_kind IN ('deterministic_companion')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    checks_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    report_relative_path TEXT NOT NULL,
    render_relative_path TEXT NOT NULL,
    preview_pdf_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_artifact_id, qa_version)
);
CREATE INDEX idx_manual_document_qa_latest
ON manual_document_qa_runs(document_artifact_id, qa_version DESC);
"""

MIGRATION_021 = """
ALTER TABLE manual_figure_revisions
ADD COLUMN edit_source TEXT NOT NULL DEFAULT 'ai_generation'
CHECK (edit_source IN ('ai_generation', 'manual', 'ai'));
ALTER TABLE manual_figure_revisions
ADD COLUMN parent_revision_id TEXT;
ALTER TABLE manual_figure_revisions
ADD COLUMN operations_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE manual_figure_revisions
ADD COLUMN semantic_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE manual_figure_revisions
ADD COLUMN revision_status TEXT NOT NULL DEFAULT 'clean'
CHECK (revision_status IN ('clean', 'conflicted'));
"""

MIGRATION_022 = """
ALTER TABLE manual_screenshot_artifacts
ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
ALTER TABLE manual_screenshot_artifacts
ADD COLUMN archived_at TEXT;
UPDATE manual_screenshot_artifacts SET updated_at=created_at WHERE updated_at='';
ALTER TABLE manual_screenshot_revisions
ADD COLUMN edit_source TEXT NOT NULL DEFAULT 'import'
CHECK (edit_source IN ('import', 'manual', 'replacement', 'rollback', 'archive', 'restore'));
ALTER TABLE manual_screenshot_revisions
ADD COLUMN parent_revision_id TEXT;
ALTER TABLE manual_screenshot_revisions
ADD COLUMN change_summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE manual_screenshot_revisions
ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1));
"""

MIGRATION_023 = """
UPDATE model_configs
SET verified_at = COALESCE(verified_at, updated_at)
WHERE verified_at IS NULL
  AND json_extract(settings_json, '$.endpoint_mode') IN
      ('messages', 'chat_completions', 'responses', 'ollama_chat');
"""

MIGRATION_024 = """
CREATE TABLE manual_document_qa_runs_v24 (
    id TEXT PRIMARY KEY,
    document_artifact_id TEXT NOT NULL REFERENCES manual_document_artifacts(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    qa_version INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    renderer_kind TEXT NOT NULL CHECK (renderer_kind IN ('deterministic_companion', 'libreoffice_word')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    checks_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    report_relative_path TEXT NOT NULL,
    render_relative_path TEXT NOT NULL,
    preview_pdf_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_artifact_id, qa_version)
);
INSERT INTO manual_document_qa_runs_v24(
    id,document_artifact_id,job_id,qa_version,policy_version,renderer_kind,passed,
    checks_json,summary_json,report_relative_path,render_relative_path,
    preview_pdf_relative_path,created_at
)
SELECT
    id,document_artifact_id,job_id,qa_version,policy_version,
    CASE
        WHEN json_extract(summary_json, '$.renderer_kind') = 'libreoffice_word'
            THEN 'libreoffice_word'
        ELSE renderer_kind
    END,
    passed,checks_json,summary_json,report_relative_path,render_relative_path,
    preview_pdf_relative_path,created_at
FROM manual_document_qa_runs;
DROP TABLE manual_document_qa_runs;
ALTER TABLE manual_document_qa_runs_v24 RENAME TO manual_document_qa_runs;
CREATE INDEX idx_manual_document_qa_latest
ON manual_document_qa_runs(document_artifact_id, qa_version DESC);
"""

MIGRATION_025 = """
CREATE TABLE manual_export_records (
    id TEXT PRIMARY KEY,
    document_artifact_id TEXT NOT NULL REFERENCES manual_document_artifacts(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    document_version INTEGER NOT NULL,
    export_kind TEXT NOT NULL CHECK (export_kind IN ('review', 'formal')),
    destination_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_manual_export_records_document
ON manual_export_records(document_artifact_id, created_at DESC);
CREATE INDEX idx_manual_export_records_job
ON manual_export_records(job_id, created_at DESC);
"""

MIGRATION_026 = """
CREATE TABLE manual_execution_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    stage_key TEXT NOT NULL,
    node_kind TEXT NOT NULL CHECK (node_kind IN
        ('research', 'section', 'figure', 'screenshot', 'assemble', 'qa')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'skipped')),
    dependency_keys_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    model_config_id TEXT REFERENCES model_configs(id) ON DELETE SET NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_category TEXT,
    safe_error_message TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, node_key)
);
CREATE INDEX idx_manual_execution_nodes_job_stage
ON manual_execution_nodes(job_id, stage_key, status);
"""

MIGRATION_027 = """
ALTER TABLE manual_execution_nodes RENAME TO manual_execution_nodes_v26;
CREATE TABLE manual_execution_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    stage_key TEXT NOT NULL,
    node_kind TEXT NOT NULL CHECK (node_kind IN
        ('research', 'section', 'figure', 'screenshot', 'assemble', 'qa')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'skipped',
         'waiting_for_authorization')),
    dependency_keys_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    model_config_id TEXT REFERENCES model_configs(id) ON DELETE SET NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_category TEXT,
    safe_error_message TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, node_key)
);
INSERT INTO manual_execution_nodes(
    id,job_id,node_key,stage_key,node_kind,title,status,dependency_keys_json,
    attempt,max_attempts,model_config_id,input_json,output_json,error_category,
    safe_error_message,started_at,heartbeat_at,finished_at,created_at,updated_at
)
SELECT id,job_id,node_key,stage_key,node_kind,title,status,dependency_keys_json,
    attempt,max_attempts,model_config_id,input_json,output_json,error_category,
    safe_error_message,started_at,heartbeat_at,finished_at,created_at,updated_at
FROM manual_execution_nodes_v26;
DROP TABLE manual_execution_nodes_v26;
CREATE INDEX idx_manual_execution_nodes_job_stage
ON manual_execution_nodes(job_id, stage_key, status);
"""

MIGRATION_028 = """
CREATE TABLE manual_qa_decisions (
    id TEXT PRIMARY KEY,
    qa_run_id TEXT NOT NULL REFERENCES manual_document_qa_runs(id) ON DELETE CASCADE,
    document_artifact_id TEXT NOT NULL REFERENCES manual_document_artifacts(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    check_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('deferred')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(qa_run_id, check_key)
);
CREATE INDEX idx_manual_qa_decisions_run
ON manual_qa_decisions(qa_run_id, created_at);
"""

MIGRATION_029 = """
CREATE TABLE manual_project_profile_revisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    research_artifact_id TEXT REFERENCES manual_research_artifacts(id) ON DELETE SET NULL,
    origin TEXT NOT NULL CHECK (origin IN ('research', 'user')),
    profile_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE INDEX idx_manual_project_profile_latest
ON manual_project_profile_revisions(task_id, version DESC);

CREATE TABLE manual_screenshot_import_batches (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    job_id TEXT REFERENCES manual_generation_jobs(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK (source IN ('user', 'clipboard', 'folder', 'automated')),
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'completed', 'completed_with_warnings', 'failed')),
    input_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX idx_manual_screenshot_batches_task
ON manual_screenshot_import_batches(task_id, created_at DESC);

CREATE TABLE manual_project_screenshot_assets (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    asset_key TEXT NOT NULL,
    legacy_job_id TEXT REFERENCES manual_generation_jobs(id) ON DELETE SET NULL,
    legacy_screenshot_key TEXT,
    import_batch_id TEXT REFERENCES manual_screenshot_import_batches(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK (source IN ('user', 'clipboard', 'folder', 'automated')),
    title TEXT NOT NULL,
    image_relative_path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    image_format TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    analysis_status TEXT NOT NULL CHECK (analysis_status IN
        ('pending', 'queued', 'running', 'completed', 'failed', 'outdated')),
    review_status TEXT NOT NULL CHECK (review_status IN
        ('pending', 'reviewed', 'rejected')),
    adoption_status TEXT NOT NULL CHECK (adoption_status IN
        ('pending', 'adopted', 'excluded')),
    group_key TEXT NOT NULL DEFAULT '',
    group_title TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    sensitive_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (sensitive_status IN
        ('unreviewed', 'confirmed_safe', 'contains_sensitive')),
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(task_id, asset_key),
    UNIQUE(legacy_job_id, legacy_screenshot_key)
);
CREATE INDEX idx_manual_project_screenshots_task
ON manual_project_screenshot_assets(task_id, archived_at, group_key, sort_order);
CREATE INDEX idx_manual_project_screenshots_sha
ON manual_project_screenshot_assets(task_id, sha256);

CREATE TABLE manual_project_screenshot_revisions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES manual_project_screenshot_assets(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    image_relative_path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    image_format TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    edit_source TEXT NOT NULL CHECK (edit_source IN
        ('import', 'manual', 'replacement', 'rollback', 'legacy_migration')),
    parent_revision_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, version)
);
CREATE INDEX idx_manual_project_screenshot_revision_latest
ON manual_project_screenshot_revisions(asset_id, version DESC);

CREATE TABLE manual_screenshot_interpretation_revisions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES manual_project_screenshot_assets(id) ON DELETE CASCADE,
    asset_revision_id TEXT NOT NULL REFERENCES manual_project_screenshot_revisions(id),
    project_profile_revision_id TEXT NOT NULL REFERENCES manual_project_profile_revisions(id),
    version INTEGER NOT NULL,
    model_config_id TEXT REFERENCES model_configs(id) ON DELETE SET NULL,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    interpretation_json TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('ai', 'user', 'legacy_migration')),
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    attempt_count INTEGER NOT NULL DEFAULT 1,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, version)
);
CREATE INDEX idx_manual_screenshot_interpretation_cache
ON manual_screenshot_interpretation_revisions(cache_key, status, created_at DESC);

CREATE TABLE manual_job_screenshot_refs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES manual_project_screenshot_assets(id),
    asset_revision_id TEXT NOT NULL REFERENCES manual_project_screenshot_revisions(id),
    interpretation_revision_id TEXT NOT NULL REFERENCES manual_screenshot_interpretation_revisions(id),
    group_key TEXT NOT NULL,
    group_title TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    adopted_at TEXT NOT NULL,
    UNIQUE(job_id, asset_id)
);
CREATE INDEX idx_manual_job_screenshot_refs_order
ON manual_job_screenshot_refs(job_id, group_key, sort_order);

CREATE TABLE manual_ui_section_sources (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    section_revision_id TEXT NOT NULL REFERENCES manual_section_revisions(id),
    project_profile_revision_id TEXT NOT NULL REFERENCES manual_project_profile_revisions(id),
    adopted_set_hash TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_manual_ui_section_sources_latest
ON manual_ui_section_sources(job_id, created_at DESC);

INSERT OR IGNORE INTO manual_project_screenshot_assets(
    id,task_id,asset_key,legacy_job_id,legacy_screenshot_key,source,title,
    image_relative_path,width,height,image_format,sha256,analysis_status,
    review_status,adoption_status,group_key,group_title,sort_order,sensitive_status,
    created_at,updated_at,archived_at
)
SELECT
    lower(hex(randomblob(16))),j.task_id,
    'legacy-' || replace(msa.job_id, '-', '') || '-' || msa.screenshot_key,
    msa.job_id,msa.screenshot_key,msa.source,msa.title,msa.image_relative_path,
    COALESCE(msr.width,1),COALESCE(msr.height,1),'PNG',COALESCE(msr.sha256,''),
    'completed','reviewed',CASE WHEN msa.archived_at IS NULL THEN 'adopted' ELSE 'excluded' END,
    'legacy-ui','历史界面截图',0,'unreviewed',msa.created_at,
    COALESCE(NULLIF(msa.updated_at,''),msa.created_at),msa.archived_at
FROM manual_screenshot_artifacts msa
JOIN manual_generation_jobs j ON j.id=msa.job_id
LEFT JOIN manual_screenshot_revisions msr ON msr.job_id=msa.job_id
    AND msr.screenshot_key=msa.screenshot_key
    AND msr.version=(SELECT MAX(version) FROM manual_screenshot_revisions x
        WHERE x.job_id=msa.job_id AND x.screenshot_key=msa.screenshot_key);

INSERT OR IGNORE INTO manual_project_screenshot_revisions(
    id,asset_id,version,title,image_relative_path,width,height,image_format,sha256,
    edit_source,parent_revision_id,created_at
)
SELECT lower(hex(randomblob(16))),a.id,r.version,r.title,r.image_relative_path,
    r.width,r.height,'PNG',r.sha256,'legacy_migration',r.parent_revision_id,r.created_at
FROM manual_screenshot_revisions r
JOIN manual_project_screenshot_assets a ON a.legacy_job_id=r.job_id
    AND a.legacy_screenshot_key=r.screenshot_key;

ALTER TABLE manual_execution_nodes RENAME TO manual_execution_nodes_v28;
CREATE TABLE manual_execution_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES manual_generation_jobs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    stage_key TEXT NOT NULL,
    node_kind TEXT NOT NULL CHECK (node_kind IN
        ('research', 'profile', 'section', 'figure', 'screenshot', 'screenshot_import',
         'screenshot_analysis', 'screenshot_review', 'assemble', 'qa')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'skipped',
         'waiting_for_authorization', 'waiting_for_review', 'waiting_for_screenshots',
         'adopted', 'outdated')),
    dependency_keys_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    model_config_id TEXT REFERENCES model_configs(id) ON DELETE SET NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_category TEXT,
    safe_error_message TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, node_key)
);
INSERT INTO manual_execution_nodes SELECT * FROM manual_execution_nodes_v28;
DROP TABLE manual_execution_nodes_v28;
CREATE INDEX idx_manual_execution_nodes_job_stage
ON manual_execution_nodes(job_id, stage_key, status);
"""

MIGRATION_030 = """
CREATE TABLE manual_ui_evidence_decisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN
        ('waiting_for_screenshots', 'source_inferred', 'not_applicable')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE INDEX idx_manual_ui_evidence_decisions_latest
ON manual_ui_evidence_decisions(task_id, version DESC);
"""

MIGRATION_031 = """
CREATE TABLE quick_start_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    manual_job_id TEXT REFERENCES manual_generation_jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued','running','waiting_for_user','failed','completed')),
    current_stage TEXT NOT NULL,
    config_json TEXT NOT NULL,
    stages_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL DEFAULT '{}',
    safe_error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_quick_start_runs_recent
ON quick_start_runs(created_at DESC);
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
            applied_v16 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 16"
            ).fetchone()
            if applied_v16 is None:
                connection.executescript(MIGRATION_016)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (16, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-016",),
                )
            applied_v17 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 17"
            ).fetchone()
            if applied_v17 is None:
                connection.executescript(MIGRATION_017)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (17, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-017",),
                )
            applied_v18 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 18"
            ).fetchone()
            if applied_v18 is None:
                connection.executescript(MIGRATION_018)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (18, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-018",),
                )
            applied_v19 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 19"
            ).fetchone()
            if applied_v19 is None:
                connection.executescript(MIGRATION_019)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (19, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-019",),
                )
            applied_v20 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 20"
            ).fetchone()
            if applied_v20 is None:
                connection.executescript(MIGRATION_020)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (20, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-020",),
                )
            applied_v21 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 21"
            ).fetchone()
            if applied_v21 is None:
                connection.executescript(MIGRATION_021)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (21, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-021",),
                )
            applied_v22 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 22"
            ).fetchone()
            if applied_v22 is None:
                connection.executescript(MIGRATION_022)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (22, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-022",),
                )
            applied_v23 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 23"
            ).fetchone()
            if applied_v23 is None:
                connection.executescript(MIGRATION_023)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (23, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-023",),
                )
            applied_v24 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 24"
            ).fetchone()
            if applied_v24 is None:
                connection.executescript(MIGRATION_024)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (24, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-024",),
                )
            applied_v25 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 25"
            ).fetchone()
            if applied_v25 is None:
                connection.executescript(MIGRATION_025)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (25, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-025",),
                )
            applied_v26 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 26"
            ).fetchone()
            if applied_v26 is None:
                connection.executescript(MIGRATION_026)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (26, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-026",),
                )
            applied_v27 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 27"
            ).fetchone()
            if applied_v27 is None:
                connection.executescript(MIGRATION_027)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (27, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-027",),
                )
            applied_v28 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 28"
            ).fetchone()
            if applied_v28 is None:
                connection.executescript(MIGRATION_028)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (28, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-028",),
                )
            applied_v29 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 29"
            ).fetchone()
            if applied_v29 is None:
                connection.executescript(MIGRATION_029)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (29, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-029",),
                )
            applied_v30 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 30"
            ).fetchone()
            if applied_v30 is None:
                connection.executescript(MIGRATION_030)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (30, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-030",),
                )
            applied_v31 = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 31"
            ).fetchone()
            if applied_v31 is None:
                connection.executescript(MIGRATION_031)
                connection.execute(
                    """INSERT INTO schema_migrations(version, applied_at, checksum)
                    VALUES (31, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)""",
                    ("migration-031",),
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
