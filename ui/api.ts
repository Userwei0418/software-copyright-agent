import { invoke } from "@tauri-apps/api/core";

export type SidecarConnection = {
  baseUrl: string;
  sessionToken: string;
  pid: number;
  version: string;
};

export type FactItem = { key: string; value: unknown; status: string; confidence: number };
export type ConfirmationItem = {
  field_key: string; question: string; candidates: unknown[]; required: boolean; status: string;
};
export type ProjectScanResult = {
  task_id: string;
  snapshot_id: string;
  summary: {
    file_count: number; ignored_count: number; total_bytes: number;
    secret_finding_count: number; languages: string[];
  };
  inspection: {
    task: { id: string; status: string; current_stage_key: string };
    facts: FactItem[];
    confirmations: ConfirmationItem[];
  };
};
export type RecentTask = {
  task_id: string; snapshot_id: string | null; display_name: string;
  source_kind: "directory" | "zip"; status: string; current_stage_key: string;
  summary: ProjectScanResult["summary"] | null; updated_at: string;
};
export type ModelConfig = { id: string; name: string;
  protocol_id: "openai_compatible" | "anthropic" | "ollama"; base_url: string;
  model_name: string; provider_id: string; has_credential: boolean; enabled: boolean;
  endpoint_mode: "messages" | "chat_completions" | "responses" | "ollama_chat" | null;
  supports_vision: boolean | null; vision_verified: boolean;
  max_concurrency: number;
  verified_at: string | null; created_at: string; updated_at: string };
export type FormalFigureAiPatchResult = { figure_key: string; edit_source: "ai"; xml: string;
  operations: Array<{ action: string; target: string; payload: Record<string, unknown> }>;
  elapsed_ms: number; model_name: string; model_config_id: string; prompt_version: string;
  context_cache_hit: boolean };
export type FormalFigureAiStreamEvent =
  | { type: "phase"; phase: string; message: string }
  | { type: "delta"; text: string }
  | { type: "heartbeat" }
  | { type: "result"; result: FormalFigureAiPatchResult }
  | { type: "error"; message: string };
export type AppSettings = { manual_model_id: string | null; diagram_model_id: string | null;
  vision_model_id: string | null;
  temperature: number; max_output_tokens: number;
  source_strategy: "standard" | "relaxed" | "maximum"; auto_preview: boolean;
  generation_concurrency: number; document_style_prompt: string; diagram_style_prompt: string };
export type Inspection = ProjectScanResult["inspection"];
export type SourceMaterialsSnapshot = {
  task: { id: string; status: string; current_stage_key: string; failure_category?: string | null;
    safe_error_message?: string | null };
  project: { name: string; version: string };
  source_plan: null | { version: number; created_at: string; summary: {
    total_source_files: number; selected_files: number; selected_code_lines: number;
    excluded_files: number; grades: Record<"A" | "B" | "C", number>;
    strategy: "standard" | "relaxed" | "maximum";
  }; candidates: Array<{ relative_path: string; grade: string; score: number;
    code_lines: number; language: string | null }> };
  code_preview: null | { version: number; created_at: string; summary: {
    available_visual_lines: number; used_visual_lines: number; required_visual_lines: number;
    generated_pages: number; target_pages: number; sufficient: boolean; selected_files: number;
    included_files: number; available_buckets?: string[]; included_buckets?: string[];
    included_languages?: string[]; truncated: boolean;
  } };
  source_document: null | { version: number; created_at: string; artifact_relative_path: string;
    sha256: string; integrity: { status: "verified" | "missing" | "mismatch" | "invalid_path";
      size_bytes: number | null };
    quality: { status: "passed" | "failed" | "not_checked" | "outdated"; passed: boolean | null;
      checked_at: string | null; summary: null | { rendered_pages: number;
        minimum_body_fill_ratio: number; underfilled_pages: number[]; [key: string]: unknown };
      qa_version: number | null; policy_version: string | null; current_policy: boolean;
      generator_version: string | null; current_generator: boolean };
    summary: { total_pages_expected: number; code_pages: number; code_lines: number } };
  actions: { source_plan: boolean; code_preview: boolean; source_docx: boolean };
  blockers: string[];
};
export type CodePagePreview = { version: number; total_pages: number; pages: Array<{
  page_number: number; line_count: number; entries: Array<{ kind: string; path: string | null;
    source_line: number | null; continuation: boolean; text: string }>;
}> };
export type SourceDocumentPreview = { version: number; qa_version: number;
  total_pages: number; quality_status: "passed" | "failed" | "outdated"; pages: number[] };
export type SourceDocumentQaCapability = { available: boolean; renderer: "libreoffice";
  missing: string[]; message: string };
export type ManualWorkspaceSnapshot = {
  task: { id: string; status: string; current_stage_key: string };
  manual_plan: null | { version: number; summary: { section_count: number; ready_sections: number;
    needs_evidence_sections: number; missing_information_count: number; diagram_count: number };
    sections: Array<{ key: string; title: string; purpose: string; status: string;
      missing_information: string[]; subsections: string[]; diagram_keys: string[] }>;
    missing_information: string[]; diagram_requirements: Array<{ key: string; title: string }> };
  diagram_plan: null | { version: number; summary: { diagram_count: number; ready_diagrams: number;
    needs_evidence_diagrams: number; node_count: number; edge_count: number };
    diagrams: Array<{ key: string; title: string; status: string; node_count: number;
      edge_count: number; missing_information: string[] }> };
  diagram_artifacts: null | { version: number; summary: Record<string, unknown> };
  manual_draft: null | { version: number; summary: { model_name: string; endpoint_mode: string;
    plan_version: number; character_count: number }; content: string; elapsed_ms: number; created_at: string };
  actions: { manual_plan: boolean; diagram_plan: boolean; diagram_artifacts: boolean;
    manual_generate: boolean };
};

export type FormalManualDocument = {
  id: string; job_id: string; task_id: string; version: number;
  status: "assembled" | "qa_passed" | "qa_failed";
  document_kind: "review_checkpoint" | "formal_candidate" | "final_document";
  project_name: string; project_version: string; filename: string;
  docx_relative_path: string; sha256: string; created_at: string;
  integrity: { status: "verified" | "missing" | "mismatch"; size_bytes: number | null };
  freshness: { status: "current" | "outdated"; latest_asset_update: string | null };
  quality: { status: "passed" | "failed" | "not_checked" | "outdated";
    passed: boolean | null; qa_version: number | null; policy_version: string | null;
    current_policy: boolean; generator_version: string | null; current_generator: boolean;
    checked_at: string | null };
  qa: { section_count: number; figure_count: number; screenshot_count: number;
    warning_count: number; warnings: string[]; design_preset: string; named_override: string;
    quality?: FormalManualQa["summary"] };
};

export type FormalManualQa = {
  id: string; job_id: string; document_artifact_id: string; document_version: number;
  qa_version: number; policy_version: string;
  renderer_kind: "deterministic_companion" | "libreoffice_word";
  passed: boolean; page_count: number; created_at: string;
  checks: Array<{ key: string; passed: boolean; severity: "blocker" | "warning";
    expected: unknown; actual: unknown; message: string }>;
  decisions: Array<{ check_key: string; action: "deferred"; reason: string; created_at: string }>;
  summary: { passed: boolean; rendered_pages: number; warning_count: number;
    blocker_count: number; underfilled_pages: number[]; renderer_kind: string;
    renderer_disclosure: string; [key: string]: unknown };
};

export type FormalManualJob = {
  id: string; task_id: string; model_config_id: string; version: number;
  status: string; current_step: string; progress: { completed: number; total: number; percent: number;
    stage_completed?: number; stage_total?: number; current_title?: string;
    running_nodes?: number; queued_nodes?: number; node_status_counts?: Record<string, number> };
  created_at: string; started_at: string | null; finished_at: string | null;
  updated_at: string; safe_error_message: string | null;
  steps: Array<{ key: string; status: string; attempt: number; summary: Record<string, unknown>;
    started_at: string | null; finished_at: string | null; safe_error_message: string | null }>;
  nodes: Array<{ id: string; key: string; stage_key: string; kind: string; title: string;
    status: string; dependencies: string[]; attempt: number; max_attempts: number;
    model_config_id: string | null; input: Record<string, unknown>; output: Record<string, unknown>;
    next_action: string | null; duration_ms: number | null;
    error_category: string | null; safe_error_message: string | null; started_at: string | null;
    heartbeat_at: string | null; finished_at: string | null; created_at: string; updated_at: string }>;
};

export type QuickStartStage = {
  key: string; title: string; description: string;
  status: "pending" | "running" | "completed" | "failed";
  attempt: number; message: string; started_at: string | null; finished_at: string | null;
  output?: Record<string, unknown>;
  events?: Array<{ at: string; status: string; message: string; attempt: number }>;
};

export type QuickStartRun = {
  id: string; task_id: string | null; manual_job_id: string | null;
  status: "queued" | "running" | "waiting_for_user" | "failed" | "completed";
  current_stage: string; safe_error_message: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; updated_at: string;
  config: { software_name: string; version: string; project_path: string;
    screenshot_folder: string; concurrency: number; retry_limit: number };
  stages: QuickStartStage[];
  outputs: Record<string, unknown>;
  manual_job: FormalManualJob | null;
};

export type QuickStartConfig = {
  project_path: string; software_name: string; version: string; screenshot_folder: string;
  manual_model_id: string; diagram_model_id: string; vision_model_id: string;
  source_strategy: "standard" | "relaxed" | "maximum"; concurrency: number;
  retry_limit: number; recursive_screenshots: boolean; finalize_with_warnings: boolean;
  sensitive_confirmed: boolean; auto_adopt_confirmed: boolean;
};

export type RunDiagnosticsStage = Pick<QuickStartStage, "key" | "title" | "description" |
  "status" | "attempt" | "message" | "started_at" | "finished_at"> & {
    events?: QuickStartStage["events"];
    output?: Record<string, unknown>;
  };
export type RunDiagnosticsItem = {
  id: string; status: string; current_stage: string; safe_error_message: string | null;
  task_id: string | null; manual_job_id: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; updated_at: string;
  config: Record<string, unknown>; stages: RunDiagnosticsStage[];
  outputs: Record<string, unknown>; task_events: Array<Record<string, unknown>>;
  manual_job: null | Record<string, unknown>; manual_nodes: Array<Record<string, unknown>>;
  document_qa: Array<Record<string, unknown>>; source_qa: Array<Record<string, unknown>>;
};
export type RunDiagnosticsBundle = { schema_version: number; generated_from: string;
  run_count: number; runs: RunDiagnosticsItem[] };
export type DiagnosticsExportResult = { destinationPath: string; sizeBytes: number };

export type ManualExportResult = { destinationPath: string; sizeBytes: number; sha256: string;
  verified: boolean; receiptRecorded: boolean };
export type ManualExportRecord = { id: string; job_id: string; document_version: number;
  export_kind: "review" | "formal"; destination_path: string; size_bytes: number;
  sha256: string; verified: boolean; created_at: string };

type ManualBlockTrace = { evidence_refs?: string[]; inference?: boolean };
export type ManualSectionBlock = ({ type: "paragraph"; text: string } |
  { type: "subheading"; title: string } |
  { type: "list"; lead: string; items: string[] } |
  { type: "table"; title: string; headers: string[]; rows: string[][] } |
  { type: "figure_request"; figure_key: string; figure_type?: string;
    title: string; purpose: string }) & ManualBlockTrace;

export type FormalManualPreview = {
  document: FormalManualDocument;
  sections: Array<{ section_key: string; title: string; ordinal: number; status: string;
    blocks: ManualSectionBlock[] }>;
  figures: Array<{ figure_key: string; section_key: string; title: string; status: string }>;
  screenshots: Array<{ screenshot_key: string; section_key: string; title: string;
    description: Record<string, string> }>;
};

export type FormalManualGenerationResult = {
  job: FormalManualJob; document: FormalManualDocument;
  quality: FormalManualQa;
  draft: { status: string; section_count: number; errors: unknown[] };
  figures: { status: string; count: number; errors: unknown[] };
  screenshots: { status: string; count: number; assessment: { status: string; reason: string } };
};

export type FormalManualFigure = {
  id: string; figure_key: string; section_key: string; figure_type: string;
  title: string; status: string; available: boolean; editor_managed?: boolean;
  error?: string; version: number; updated_at: string;
  semantic: { figure_key: string; title: string; figure_type: string; layout: string;
    nodes: Array<{ key: string; label: string; display_label?: string; kind: string;
      layer: number; visual_override?: Record<string, Record<string, unknown>> }>;
    edges: Array<{ key: string; source: string; target: string; label: string;
      display_label?: string; visual_override?: Record<string, Record<string, unknown>> }> };
  drawio_relative_path: string; svg_relative_path: string; png_relative_path: string;
  qa: Record<string, unknown>;
};

export type ScreenshotDescription = {
  page_purpose: string; entry_conditions: string; visible_regions: string;
  typical_workflow: string; backend_interactions: string;
  result_validation_recovery: string;
};

export type FormalManualScreenshot = {
  screenshot_key: string; section_key: string; title: string; source: "user" | "automated";
  image_relative_path: string; description: ScreenshotDescription; width: number; height: number;
  sha256: string; version: number; archived: boolean; created_at: string; updated_at: string;
};

export type FormalScreenshotRevision = FormalManualScreenshot & {
  revision_id: string; edit_source: "import" | "manual" | "replacement" | "rollback" |
    "archive" | "restore"; parent_revision_id: string | null;
  change_summary: Record<string, unknown>;
};

export type ScreenshotInterpretation = {
  page_title: string; page_type: string; purpose: string; target_roles: string[];
  entry_conditions: string[]; visible_regions: string[]; key_controls: string[];
  workflow_steps: string[]; success_state: string; failure_and_recovery: string;
  related_backend_actions: string[]; route_guess: string; related_evidence_refs: string[];
  suggested_group: string; suggested_order: number; suggested_caption: string;
  confidence: number; warnings: string[];
};

export type ProjectScreenshotAsset = {
  id: string; task_id: string; asset_key: string; source: "user" | "clipboard" | "folder" | "automated";
  title: string; image_relative_path: string; width: number; height: number; image_format: string;
  sha256: string; version: number; revision_id: string;
  analysis_status: "pending" | "queued" | "running" | "completed" | "failed" | "outdated";
  review_status: "pending" | "reviewed" | "rejected";
  adoption_status: "pending" | "adopted" | "excluded";
  group_key: string; group_title: string; sort_order: number;
  sensitive_status: "unreviewed" | "confirmed_safe" | "contains_sensitive";
  failure_reason: string | null; interpretation: ScreenshotInterpretation | null;
  interpretation_id: string | null; interpretation_version: number | null;
  interpretation_reviewed: boolean; interpretation_model: string | null;
  interpretation_elapsed_ms: number | null; interpretation_attempts: number | null;
  archived: boolean; created_at: string; updated_at: string;
};

export type ScreenshotProjectProfile = { id: string; task_id: string; version: number;
  origin: "research" | "user"; profile: Record<string, unknown>; fingerprint: string; created_at: string };

export type ScreenshotEvidenceWorkspace = {
  profile: ScreenshotProjectProfile; assets: ProjectScreenshotAsset[];
  vision_models: Array<{ id: string; name: string; model_name: string; status: "supported";
    confirmed: boolean; message: string }>;
  batches: Array<{ id: string; source: string; status: string; input_count: number;
    imported_count: number; warning_count: number; failure_count: number; summary: Record<string, unknown> }>;
  ui_evidence_decision: { id?: string; task_id: string; version: number;
    decision: "waiting_for_screenshots" | "source_inferred" | "not_applicable";
    reason: string; created_at: string | null };
  privacy_notice: string;
};

export type ScreenshotEvidenceHistory = {
  asset_id: string;
  image_revisions: Array<{ id: string; version: number; title: string;
    width: number; height: number; image_format: string; sha256: string;
    edit_source: "import" | "manual" | "replacement" | "rollback" | "legacy_migration";
    created_at: string }>;
  interpretation_revisions: Array<{ id: string; version: number; asset_revision_id: string;
    model_name: string; status: "completed" | "failed"; origin: "ai" | "user" | "legacy_migration";
    reviewed: number; attempt_count: number; elapsed_ms: number; failure_reason: string | null;
    interpretation: ScreenshotInterpretation; created_at: string }>;
};

export type CaptureLaunchCandidate = {
  id: string; kind: "node_script" | "maven_spring_boot" | "service_bundle";
  title: string; program: string; args: string[];
  working_directory: string; command_preview: string; script_preview: string; default_url: string;
  services: Array<{ program: string; args: string[]; working_directory: string;
    command_preview: string }>;
};

export type CaptureLaunchPlan = {
  job_id: string; project_root: string; candidates: CaptureLaunchCandidate[];
  routes: Array<{ path: string; title: string; source: string; name: string; component: string;
    requires_auth: boolean; section_key: string;
    relevance: "document_evidence" | "document_keyword" | "route_only"; reason: string;
    matched_evidence_refs: string[]; score: number }>;
  policy: { requires_explicit_authorization: boolean; allows_arbitrary_commands: boolean;
    allows_external_urls: boolean; allowed_hosts: string[]; credentials_are_injected: boolean;
    process_tree_is_stopped_by_application: boolean; multi_service_launch_supported: boolean };
};

export type ProjectCaptureStatus = {
  jobId: string; status: "starting" | "running" | "partial_failure" | "exited" | "stopped"; pid: number;
  targetUrl: string; commandPreview: string; elapsedSeconds: number; logTail: string;
  exitCode: number | null;
};

export type ProjectPageCaptureResult = {
  path: string; url: string; width: number; height: number; browser: string;
};

export type FormalFigureRevision = {
  revision_id: string; version: number; edit_source: "ai_generation" | "manual" | "ai";
  parent_revision_id: string | null;
  operations: Array<{ action: string; target: string; payload: Record<string, unknown> }>;
  operation_count: number;
  semantic_fingerprint: string; status: "clean" | "conflicted";
  model_name: string; elapsed_ms: number; created_at: string;
};

export async function connectSidecar(): Promise<SidecarConnection> {
  const devMode = (import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV;
  if (devMode && typeof window !== "undefined") {
    const query = new URLSearchParams(window.location.search);
    const testBaseUrl = query.get("sidecarBaseUrl");
    const testToken = query.get("sidecarToken");
    if (testBaseUrl || testToken) {
      if (!testBaseUrl || !testToken || testToken.length < 32) {
        throw new Error("开发测试连接参数不完整");
      }
      const parsed = new URL(testBaseUrl);
      if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" ||
          parsed.username || parsed.password || parsed.pathname !== "/") {
        throw new Error("开发测试连接仅允许 127.0.0.1 HTTP 根地址");
      }
      return { baseUrl: parsed.origin, sessionToken: testToken, pid: 0, version: "dev-test" };
    }
  }
  return invoke<SidecarConnection>("start_sidecar");
}

async function localFetch(connection: SidecarConnection, path: string, init?: RequestInit) {
  const execute = () => {
    const headers = new Headers(init?.headers);
    if (headers.has("X-Session-Token")) {
      headers.set("X-Session-Token", connection.sessionToken);
    }
    return fetch(`${connection.baseUrl}${path}`, { ...init, headers });
  };
  try { return await execute(); }
  catch (error) {
    if (!(error instanceof TypeError)) throw error;
    try {
      const refreshed = await connectSidecar();
      Object.assign(connection, refreshed);
      return await execute();
    } catch (retryError) {
      const detail = retryError instanceof Error ? retryError.message : String(retryError);
      throw new Error(`本地服务连接中断，自动重连失败：${detail}`);
    }
  }
}

async function requireJson<T>(response: Response, fallback: string): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const raw = await response.text().catch(() => "");
  type ErrorPayload = { error?: { message?: string }; detail?: string };
  let payload: ErrorPayload | null = null;
  try { payload = raw ? JSON.parse(raw) as ErrorPayload : null; } catch { /* plain-text response */ }
  const detail = payload?.error?.message ?? payload?.detail ?? raw.trim();
  throw new Error(detail && detail !== "Not Found"
    ? `${fallback}：${detail}` : `${fallback} (${response.status})`);
}

export async function scanProject(
  connection: SidecarConnection,
  path: string,
): Promise<ProjectScanResult> {
  const response = await fetch(`${connection.baseUrl}/api/v1/projects/scan`, {
    method: "POST",
    headers: {
      "X-Session-Token": connection.sessionToken,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ path }),
  });
  return requireJson<ProjectScanResult>(response, "项目扫描失败");
}

export async function rescanProject(
  connection: SidecarConnection, taskId: string,
): Promise<ProjectScanResult> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/rescan`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson<ProjectScanResult>(response, "项目重新扫描失败");
}

export async function listRecentTasks(
  connection: SidecarConnection,
): Promise<RecentTask[]> {
  const response = await localFetch(connection, "/api/v1/tasks?limit=20", {
    headers: { "X-Session-Token": connection.sessionToken },
  });
  return (await requireJson<{ items: RecentTask[] }>(response, "最近任务读取失败")).items;
}

export async function deleteTask(connection: SidecarConnection, taskId: string): Promise<void> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    { method: "DELETE", headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new Error(payload?.error?.message || `任务删除失败 (${response.status})`);
  }
}

export async function loadInspection(
  connection: SidecarConnection,
  taskId: string,
): Promise<Inspection> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/inspection`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson<Inspection>(response, "项目详情读取失败");
}

export async function answerConfirmation(
  connection: SidecarConnection,
  taskId: string,
  fieldKey: string,
  value: string,
): Promise<{ remaining_required: number; task_status: string; inspection: Inspection }> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/confirmations/${encodeURIComponent(fieldKey)}`,
    {
      method: "POST",
      headers: {
        "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ value }),
    },
  );
  return requireJson(response, "确认提交失败");
}

export async function loadSourceMaterials(
  connection: SidecarConnection, taskId: string,
): Promise<SourceMaterialsSnapshot> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "源码材料读取失败");
}

export async function runSourceMaterialAction(
  connection: SidecarConnection, taskId: string,
  action: "source-plan" | "code-preview" | "source-docx",
  strategy?: "standard" | "relaxed" | "maximum",
): Promise<SourceMaterialsSnapshot> {
  const query = action === "source-plan" && strategy ? `?strategy=${strategy}` : "";
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/${action}${query}`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "源码材料生成失败");
}

export async function loadCodePagePreview(
  connection: SidecarConnection, taskId: string, allPages = false,
): Promise<CodePagePreview> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/code-preview/pages${allPages ? "?all_pages=true" : ""}`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "分页内容读取失败");
}

export async function loadSourceDocumentPreview(
  connection: SidecarConnection, taskId: string,
): Promise<SourceDocumentPreview> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/source-docx/preview`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "真实 DOCX 预览读取失败");
}

export async function loadSourceDocumentPreviewPage(
  connection: SidecarConnection, taskId: string, pageNumber: number,
): Promise<string> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/source-docx/preview/pages/${pageNumber}.png`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) throw new Error(`真实 DOCX 预览页读取失败 (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

export async function loadSourceDocumentQaCapability(
  connection: SidecarConnection, taskId: string,
): Promise<SourceDocumentQaCapability> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/source-docx/qa-capability`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "逐页质检能力读取失败");
}

export async function runSourceDocumentQa(
  connection: SidecarConnection, taskId: string,
): Promise<SourceMaterialsSnapshot> {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/source-docx/qa`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "源代码文档逐页质检失败");
}

export async function revealSourceDocument(taskId: string): Promise<void> {
  await invoke("reveal_source_document", { taskId });
}

export async function exportSourceDocument(taskId: string, destination: string): Promise<void> {
  await invoke("export_source_document", { taskId, destination });
}

export async function revealExportedDocument(path: string): Promise<void> {
  await invoke("reveal_exported_document", { path });
}

export async function listModelConfigs(connection: SidecarConnection): Promise<ModelConfig[]> {
  const response = await localFetch(connection, "/api/v1/model-configs", {
    headers: { "X-Session-Token": connection.sessionToken },
  });
  return (await requireJson<{ items: ModelConfig[] }>(response, "模型配置读取失败")).items;
}

export async function saveModelConfig(connection: SidecarConnection, config: {
  id: string; name: string; protocol_id: ModelConfig["protocol_id"];
  base_url: string; model_name: string; credential_ref: string | null; max_concurrency: number;
}): Promise<ModelConfig> {
  const response = await localFetch(connection, "/api/v1/model-configs", { method: "POST",
    headers: { "X-Session-Token": connection.sessionToken, "Content-Type": "application/json" },
    body: JSON.stringify(config) });
  return requireJson(response, "模型配置保存失败");
}

export async function markModelVerified(connection: SidecarConnection, id: string) {
  const response = await localFetch(connection, `/api/v1/model-configs/${encodeURIComponent(id)}/verified`, {
    method: "POST", headers: { "X-Session-Token": connection.sessionToken },
  });
  return requireJson<ModelConfig>(response, "模型验证状态保存失败");
}

export async function saveModelVisionCapability(connection: SidecarConnection, id: string,
  supportsVision: boolean) {
  const response = await localFetch(connection,
    `/api/v1/model-configs/${encodeURIComponent(id)}/vision-capability`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" },
      body: JSON.stringify({ supports_vision: supportsVision }),
    });
  return requireJson<ModelConfig>(response, "模型图片能力保存失败");
}

export async function deleteModelConfig(connection: SidecarConnection, id: string) {
  const response = await localFetch(connection, `/api/v1/model-configs/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: { "X-Session-Token": connection.sessionToken },
  });
  if (!response.ok) throw new Error(`模型配置删除失败 (${response.status})`);
  await invoke("delete_model_credential", { configId: id });
}

export async function storeModelCredential(id: string, apiKey: string) {
  await invoke("store_model_credential", { configId: id, apiKey });
}

export async function deleteModelCredential(id: string) {
  await invoke("delete_model_credential", { configId: id });
}

export async function hasModelCredential(id: string) {
  return invoke<boolean>("has_model_credential", { configId: id });
}

export async function probeModelConfig(config: { configId: string; protocolId: string;
  baseUrl: string; modelName: string }) {
  return invoke<{ available: boolean; modelFound: boolean; discoveredModels: string[];
    normalizedBaseUrl: string; discoverySource: string; warning: string | null }>(
    "probe_model_config", { request: config });
}

export async function testModelConnection(config: { configId: string; protocolId: string;
  baseUrl: string; modelName: string }) {
  return invoke<{ ok: boolean; elapsedMs: number; endpointMode: NonNullable<ModelConfig["endpoint_mode"]> }>(
    "test_model_connection", { request: config });
}

export async function saveModelEndpointMode(connection: SidecarConnection, id: string,
  endpointMode: NonNullable<ModelConfig["endpoint_mode"]>) {
  const response = await localFetch(connection,
    `/api/v1/model-configs/${encodeURIComponent(id)}/endpoint-mode`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ endpoint_mode: endpointMode }),
    });
  return requireJson<ModelConfig>(response, "模型接口模式保存失败");
}

export async function loadAppSettings(connection: SidecarConnection): Promise<AppSettings> {
  const response = await localFetch(connection, "/api/v1/settings", {
    headers: { "X-Session-Token": connection.sessionToken },
  });
  return requireJson(response, "应用设置读取失败");
}

export async function saveAppSettings(connection: SidecarConnection, settings: AppSettings) {
  const response = await localFetch(connection, "/api/v1/settings", { method: "POST",
    headers: { "X-Session-Token": connection.sessionToken, "Content-Type": "application/json" },
    body: JSON.stringify(settings) });
  return requireJson<AppSettings>(response, "应用设置保存失败");
}

export async function listQuickStartRuns(connection: SidecarConnection): Promise<QuickStartRun[]> {
  const response = await localFetch(connection, "/api/v1/quick-start?limit=20", {
    headers: { "X-Session-Token": connection.sessionToken },
  });
  return (await requireJson<{ items: QuickStartRun[] }>(response,
    "快速任务读取失败")).items;
}

export async function loadRunDiagnostics(connection: SidecarConnection, limit = 5) {
  const response = await localFetch(connection,
    `/api/v1/run-diagnostics?limit=${Math.max(1, Math.min(20, limit))}`, {
      headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<RunDiagnosticsBundle>(response, "运行日志读取失败");
}

export async function exportRunDiagnostics(limit: number, destination: string) {
  return invoke<DiagnosticsExportResult>("export_run_diagnostics", { limit, destination });
}

export async function loadQuickStartRun(connection: SidecarConnection, runId: string) {
  const response = await localFetch(connection,
    `/api/v1/quick-start/${encodeURIComponent(runId)}`, {
      headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<QuickStartRun>(response, "快速任务进度读取失败");
}

export async function createQuickStartRun(connection: SidecarConnection,
  config: QuickStartConfig) {
  const response = await localFetch(connection, "/api/v1/quick-start", {
    method: "POST", headers: { "X-Session-Token": connection.sessionToken,
      "Content-Type": "application/json" }, body: JSON.stringify(config),
  });
  return requireJson<QuickStartRun>(response, "快速任务启动失败");
}

export async function retryQuickStartRun(connection: SidecarConnection, runId: string) {
  const response = await localFetch(connection,
    `/api/v1/quick-start/${encodeURIComponent(runId)}/retry`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<QuickStartRun>(response, "快速任务重试失败");
}

export async function discardQuickStartRun(connection: SidecarConnection, runId: string) {
  const response = await localFetch(connection,
    `/api/v1/quick-start/${encodeURIComponent(runId)}`, {
      method: "DELETE", headers: { "X-Session-Token": connection.sessionToken },
    });
  if (!response.ok) await requireJson(response, "快速任务清空失败");
}

export async function loadManualWorkspace(connection: SidecarConnection, taskId: string) {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/manual-workspace`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson<ManualWorkspaceSnapshot>(response, "说明书工作台读取失败");
}

export async function runManualAction(connection: SidecarConnection, taskId: string,
  action: "manual-plan" | "diagram-plan" | "diagram-artifacts") {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/manual-workspace/${action}`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson<ManualWorkspaceSnapshot>(response, "说明书阶段执行失败");
}

export async function generateManualDraft(connection: SidecarConnection, taskId: string,
  modelConfigId: string) {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/manual-workspace/generate`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken,
      "Content-Type": "application/json" }, body: JSON.stringify({ model_config_id: modelConfigId }) },
  );
  return requireJson<ManualWorkspaceSnapshot>(response, "说明书 AI 草稿生成失败");
}

export async function generateFormalManual(connection: SidecarConnection, taskId: string,
  modelConfigId: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/manual-jobs/generate`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ model_config_id: modelConfigId }),
    });
  return requireJson<FormalManualJob>(response, "正式说明书生成失败");
}

export async function listFormalManualJobs(connection: SidecarConnection, taskId: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/manual-jobs`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalManualJob[] }>(response, "说明书版本读取失败")).items;
}

export async function listFormalManualDocuments(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalManualDocument[] }>(response, "说明书文档读取失败")).items;
}

export async function loadFormalManualPreview(connection: SidecarConnection, jobId: string,
  version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/preview`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<FormalManualPreview>(response, "说明书预览读取失败");
}

export async function editFormalManualSection(connection: SidecarConnection, jobId: string,
  sectionKey: string, title: string, blocks: ManualSectionBlock[]) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/sections/${encodeURIComponent(sectionKey)}`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ title, blocks }),
    });
  return requireJson<{ section_key: string; title: string; version: number; origin: "user";
    status: string; blocks: ManualSectionBlock[] }>(response, "章节修改保存失败");
}

export async function regenerateFormalManualSection(connection: SidecarConnection, jobId: string,
  sectionKey: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/sections/${encodeURIComponent(sectionKey)}/regenerate`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<{ section_key: string; title: string; version: number; origin: "ai";
    status: string; blocks: ManualSectionBlock[] }>(response, "AI 章节重新生成失败");
}

export async function listFormalManualSections(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/sections`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: Array<{ section_key: string; title: string; ordinal: number;
    status: string; blocks: ManualSectionBlock[] }> }>(response, "说明书章节读取失败")).items;
}

export async function assembleFormalManualDocument(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<FormalManualDocument>(response, "修订版 Word 装配失败");
}

export async function finalizeFormalManualDocument(connection: SidecarConnection, jobId: string,
  version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/finalize`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  if (response.status === 404) {
    throw new Error("终稿接口未加载：当前本地服务仍是旧版本。请完全退出并重新启动开发版后再试；审阅稿不会丢失。");
  }
  return requireJson<{ document: FormalManualDocument; qa_run: FormalManualQa }>(
    response, "终稿生成失败");
}

export async function generateFormalManualFigures(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<{ status: string; figures: unknown[]; errors: unknown[] }>(
    response, "章节图表同步失败");
}

export async function listFormalManualFigures(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalManualFigure[] }>(response,
    "正式图表读取失败")).items;
}

export async function regenerateFormalManualFigure(connection: SidecarConnection, jobId: string,
  figureKey: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/regenerate`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<FormalManualFigure>(response, "图表重新生成失败");
}

export async function loadFormalFigureAsset(connection: SidecarConnection, jobId: string,
  figureKey: string, format: "drawio" | "svg" | "png"): Promise<string> {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}.${format}`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  if (!response.ok) throw new Error(`图表 ${format.toUpperCase()} 读取失败 (${response.status})`);
  return format === "png" ? URL.createObjectURL(await response.blob()) : response.text();
}

export async function exportFormalFigureAsset(jobId: string, figureKey: string,
  format: "drawio" | "svg" | "png", destination: string): Promise<void> {
  await invoke("export_manual_figure_asset", {
    jobId, figureKey, assetFormat: format, destination,
  });
}

export async function revealExportedAsset(path: string): Promise<void> {
  await invoke("reveal_exported_asset", { path });
}

export async function listFormalFigureRevisions(connection: SidecarConnection, jobId: string,
  figureKey: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/revisions`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalFigureRevision[] }>(response,
    "图表修订历史读取失败")).items;
}

export async function saveFormalFigureEditorRevision(connection: SidecarConnection, jobId: string,
  figureKey: string, payload: { xml: string; svg: string; png: string }) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/editor-revision`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  return requireJson<{ revision_id: string; version: number; editor_managed: true }>(
    response, "Draw.io 完整编辑结果保存失败");
}

export async function patchFormalFigureWithAi(connection: SidecarConnection, jobId: string,
  figureKey: string, payload: { instruction: string; xml: string; model_config_id?: string }) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/ai-patch`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  return requireJson<FormalFigureAiPatchResult>(response, "AI 图表修改失败");
}

export async function streamFormalFigureAiPatch(connection: SidecarConnection, jobId: string,
  figureKey: string, payload: { instruction: string; xml: string; model_config_id: string },
  onEvent: (event: FormalFigureAiStreamEvent) => void): Promise<FormalFigureAiPatchResult> {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/ai-patch-stream`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  if (!response.ok) {
    let message = `AI 图表修改失败 (${response.status})`;
    try { message = (await response.json()).error?.message || message; } catch { /* stable fallback */ }
    throw new Error(message);
  }
  if (!response.body) throw new Error("当前运行环境不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: FormalFigureAiPatchResult | null = null;
  const consume = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as FormalFigureAiStreamEvent;
    onEvent(event);
    if (event.type === "error") throw new Error(event.message);
    if (event.type === "result") result = event.result;
  };
  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) consume(line);
    if (chunk.done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (!result) throw new Error("模型流已结束，但没有返回可应用的 XML 修改");
  return result;
}

export async function rollbackFormalFigure(connection: SidecarConnection, jobId: string,
  figureKey: string, version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/figures/${encodeURIComponent(figureKey)}/rollback`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ version }),
    });
  return requireJson<{ revision_id: string; version: number }>(response, "图表版本恢复失败");
}

export async function listFormalScreenshots(connection: SidecarConnection, jobId: string,
  includeArchived = false) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots?include_archived=${includeArchived ? "true" : "false"}`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalManualScreenshot[] }>(response,
    "截图资产读取失败")).items;
}

export async function loadScreenshotEvidenceWorkspace(connection: SidecarConnection,
  taskId: string, includeArchived = false) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/workspace?include_archived=${includeArchived}`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<ScreenshotEvidenceWorkspace>(response, "截图证据工作台读取失败");
}

export async function saveScreenshotProjectProfile(connection: SidecarConnection,
  taskId: string, profile: Record<string, unknown>) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/profile`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ profile }),
    });
  return requireJson<ScreenshotProjectProfile>(response, "项目概要保存失败");
}

export async function saveUiEvidenceDecision(connection: SidecarConnection, taskId: string,
  decision: ScreenshotEvidenceWorkspace["ui_evidence_decision"]["decision"], reason: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/ui-evidence-decision`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ decision, reason }),
    });
  return requireJson<ScreenshotEvidenceWorkspace["ui_evidence_decision"]>(
    response, "用户界面证据决策保存失败");
}

export async function importScreenshotEvidenceBatch(connection: SidecarConnection,
  taskId: string, paths: string[], source: "user" | "folder" | "automated",
  jobId?: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/import-batch`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ paths, source, job_id: jobId || null }),
    });
  return requireJson<{ id: string; status: string; input_count: number; imported_count: number;
    warning_count: number; failure_count: number; results: unknown[] }>(response, "截图批量导入失败");
}

export async function importScreenshotEvidenceFolder(connection: SidecarConnection,
  taskId: string, path: string, recursive: boolean, jobId?: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/import-folder`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ path, recursive, job_id: jobId || null }),
    });
  return requireJson<{ status: string; input_count: number; imported_count: number;
    warning_count: number; failure_count: number }>(response, "截图文件夹导入失败");
}

export async function importScreenshotClipboard(connection: SidecarConnection,
  taskId: string, dataBase64: string, filename: string, jobId?: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/clipboard`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ data_base64: dataBase64,
          filename, job_id: jobId || null }),
    });
  return requireJson<{ status: string; imported_count: number; warning_count: number;
    failure_count: number }>(response, "剪贴板截图导入失败");
}

export async function analyzeScreenshotEvidence(connection: SidecarConnection,
  taskId: string, assetIds: string[], modelConfigId: string, jobId?: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/analyze`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ asset_ids: assetIds,
          model_config_id: modelConfigId, job_id: jobId || null }),
    });
  return requireJson<{ status: "queued"; asset_ids: string[]; privacy_notice_shown: boolean }>(
    response, "截图分析排队失败");
}

export async function retryScreenshotAnalysisNode(connection: SidecarConnection,
  jobId: string, nodeKey: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/retry-analysis`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ node_key: nodeKey }),
    });
  return requireJson<{ status: "queued"; asset_id: string; model_config_id: string }>(
    response, "截图分析重试排队失败");
}

export async function reviewScreenshotEvidence(connection: SidecarConnection,
  taskId: string, assetId: string, interpretation: ScreenshotInterpretation, adopted: boolean,
  groupTitle: string, sortOrder: number, sensitiveStatus: ProjectScreenshotAsset["sensitive_status"]) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/${encodeURIComponent(assetId)}/review`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ interpretation, adopted,
          group_title: groupTitle, sort_order: sortOrder, sensitive_status: sensitiveStatus }),
    });
  return requireJson<ProjectScreenshotAsset>(response, "截图解读审核保存失败");
}

export async function replaceScreenshotEvidenceImage(connection: SidecarConnection,
  taskId: string, assetId: string, path: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/${encodeURIComponent(assetId)}/image`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    });
  return requireJson<ProjectScreenshotAsset>(response, "截图图片替换失败");
}

export async function loadScreenshotEvidenceHistory(connection: SidecarConnection,
  taskId: string, assetId: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/${encodeURIComponent(assetId)}/history`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<ScreenshotEvidenceHistory>(response, "截图版本历史读取失败");
}

export async function rollbackScreenshotEvidence(connection: SidecarConnection,
  taskId: string, assetId: string, imageVersion?: number, interpretationVersion?: number) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/${encodeURIComponent(assetId)}/rollback`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({
          image_version: imageVersion ?? null, interpretation_version: interpretationVersion ?? null,
        }),
    });
  return requireJson<ProjectScreenshotAsset>(response, "截图历史版本恢复失败");
}

export async function setScreenshotEvidenceAdoption(connection: SidecarConnection,
  taskId: string, assetIds: string[], adopted: boolean) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/adoption`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ asset_ids: assetIds, adopted }),
    });
  return (await requireJson<{ items: ProjectScreenshotAsset[] }>(response,
    "截图采用状态保存失败")).items;
}

export async function setScreenshotEvidenceAdoptionStatus(connection: SidecarConnection,
  taskId: string, assetIds: string[], status: ProjectScreenshotAsset["adoption_status"]) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/adoption-status`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ asset_ids: assetIds, status }),
    });
  return (await requireJson<{ items: ProjectScreenshotAsset[] }>(response,
    "截图采用状态保存失败")).items;
}

export async function confirmScreenshotsAndUpdateManual(connection: SidecarConnection,
  jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/confirm-and-update`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken },
    });
  return requireJson<{ status: "queued"; job_id: string; screenshot_count: number;
    message: string }>(response, "确认采用并更新说明书失败");
}

export async function loadScreenshotEvidenceImage(connection: SidecarConnection,
  taskId: string, assetId: string) {
  const response = await localFetch(connection,
    `/api/v1/tasks/${encodeURIComponent(taskId)}/screenshots/${encodeURIComponent(assetId)}.png`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  if (!response.ok) throw new Error(`截图预览读取失败 (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

export async function loadCaptureLaunchPlan(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/launch-plan`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<CaptureLaunchPlan>(response, "项目启动方案读取失败");
}

export async function launchCaptureProject(jobId: string, candidateId: string, targetUrl: string) {
  return invoke<ProjectCaptureStatus>("launch_capture_project", { jobId, candidateId, targetUrl });
}

export async function readCaptureProjectStatus(jobId: string) {
  return invoke<ProjectCaptureStatus>("capture_project_status", { jobId });
}

export async function stopCaptureProject(jobId: string) {
  return invoke<ProjectCaptureStatus>("stop_capture_project", { jobId });
}

export async function captureProjectPage(jobId: string, targetUrl: string) {
  return invoke<ProjectPageCaptureResult>("capture_project_page", { jobId, targetUrl });
}

export async function loadFormalScreenshotImage(connection: SidecarConnection, jobId: string,
  screenshotKey: string): Promise<string> {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}.png`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  if (!response.ok) throw new Error(`截图预览读取失败 (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

export async function importFormalScreenshot(connection: SidecarConnection, jobId: string,
  path: string, sectionKey: string, title: string, description: ScreenshotDescription,
  source: "user" | "automated" = "user") {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/import`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" },
      body: JSON.stringify({ path, section_key: sectionKey, title, description, source }),
    });
  return requireJson<FormalManualScreenshot>(response, "截图导入失败");
}

export async function editFormalScreenshot(connection: SidecarConnection, jobId: string,
  screenshotKey: string, sectionKey: string, title: string, description: ScreenshotDescription) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}`, {
      method: "PUT", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" },
      body: JSON.stringify({ section_key: sectionKey, title, description }),
    });
  return requireJson<FormalManualScreenshot>(response, "截图说明保存失败");
}

export async function replaceFormalScreenshot(connection: SidecarConnection, jobId: string,
  screenshotKey: string, path: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}/replace`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    });
  return requireJson<FormalManualScreenshot>(response, "截图替换失败");
}

export async function listFormalScreenshotRevisions(connection: SidecarConnection, jobId: string,
  screenshotKey: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}/revisions`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: FormalScreenshotRevision[] }>(response,
    "截图历史读取失败")).items;
}

export async function rollbackFormalScreenshot(connection: SidecarConnection, jobId: string,
  screenshotKey: string, version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}/rollback`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ version }),
    });
  return requireJson<FormalManualScreenshot>(response, "截图版本恢复失败");
}

export async function archiveFormalScreenshot(connection: SidecarConnection, jobId: string,
  screenshotKey: string, archived: boolean) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/screenshots/${encodeURIComponent(screenshotKey)}/archive`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" }, body: JSON.stringify({ archived }),
    });
  return requireJson<FormalManualScreenshot>(response, archived ? "截图归档失败" : "截图恢复失败");
}

export async function loadFormalManualQa(connection: SidecarConnection, jobId: string,
  version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/qa`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<FormalManualQa>(response, "说明书逐页质检结果读取失败");
}

export async function runFormalManualQa(connection: SidecarConnection, jobId: string,
  version: number) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/qa`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } });
  return requireJson<{ document: FormalManualDocument; qa_run: FormalManualQa }>(
    response, "说明书逐页质量检查失败");
}

export async function deferFormalManualQaCheck(connection: SidecarConnection, jobId: string,
  version: number, checkKey: string, reason: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/qa/decisions`, {
      method: "POST", headers: { "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json" },
      body: JSON.stringify({ check_key: checkKey, reason }),
    });
  return requireJson<FormalManualQa>(response, "质量问题留痕失败");
}

export async function loadFormalManualQaPage(connection: SidecarConnection, jobId: string,
  version: number, pageNumber: number): Promise<string> {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/documents/${version}/qa/pages/${pageNumber}.png`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  if (!response.ok) throw new Error(`说明书预览页读取失败 (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

export async function loadFormalManualImage(connection: SidecarConnection, jobId: string,
  kind: "figure" | "screenshot", key: string): Promise<string> {
  const suffix = kind === "figure" ? `figures/${encodeURIComponent(key)}.png`
    : `screenshots/${encodeURIComponent(key)}.png`;
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/${suffix}`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  if (!response.ok) throw new Error(`说明书图片读取失败 (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

export async function exportManualDocument(jobId: string, version: number,
  destination: string, reviewDraft = false): Promise<ManualExportResult> {
  return invoke<ManualExportResult>("export_manual_document", {
    jobId, version, destination, reviewDraft,
  });
}

export async function listManualExports(connection: SidecarConnection, jobId: string) {
  const response = await localFetch(connection,
    `/api/v1/manual-jobs/${encodeURIComponent(jobId)}/exports`,
    { headers: { "X-Session-Token": connection.sessionToken } });
  return (await requireJson<{ items: ManualExportRecord[] }>(response,
    "说明书导出记录读取失败")).items;
}
