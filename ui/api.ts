import { invoke } from "@tauri-apps/api/core";

export type SidecarConnection = {
  baseUrl: string;
  sessionToken: string;
  pid: number;
  version: string;
};

export type AssetRevision = {
  revision_id: string;
  version: number;
  status: "clean" | "conflicted";
  edit_source: "manual" | "ai";
  conflict_count: number;
  created_at: string;
  operation_count?: number;
};

export type DiagramAsset = {
  diagram_key: "system_architecture" | "core_business_flow";
  title: string;
  revision_count: number;
  latest_revision: AssetRevision | null;
  editable: boolean;
};

export type WorkspaceSnapshot = { task_id: string; assets: DiagramAsset[] };

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
  verified_at: string | null; created_at: string; updated_at: string };
export type AppSettings = { manual_model_id: string | null; diagram_model_id: string | null;
  temperature: number; max_output_tokens: number;
  source_strategy: "standard" | "relaxed" | "maximum"; auto_preview: boolean };
export type Inspection = ProjectScanResult["inspection"];
export type SourceMaterialsSnapshot = {
  task: { id: string; status: string; current_stage_key: string; safe_error_message?: string | null };
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
    included_files: number; truncated: boolean;
  } };
  source_document: null | { version: number; created_at: string; artifact_relative_path: string;
    sha256: string; integrity: { status: "verified" | "missing" | "mismatch" | "invalid_path";
      size_bytes: number | null };
    summary: { total_pages_expected: number; code_pages: number; code_lines: number } };
  actions: { source_plan: boolean; code_preview: boolean; source_docx: boolean };
  blockers: string[];
};
export type CodePagePreview = { version: number; total_pages: number; pages: Array<{
  page_number: number; line_count: number; entries: Array<{ kind: string; path: string | null;
    source_line: number | null; continuation: boolean; text: string }>;
}> };
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
  project_name: string; project_version: string; filename: string;
  docx_relative_path: string; sha256: string; created_at: string;
  integrity: { status: "verified" | "missing" | "mismatch"; size_bytes: number | null };
  qa: { section_count: number; figure_count: number; screenshot_count: number;
    warning_count: number; warnings: string[]; design_preset: string; named_override: string;
    quality?: FormalManualQa["summary"] };
};

export type FormalManualQa = {
  id: string; job_id: string; document_artifact_id: string; document_version: number;
  qa_version: number; policy_version: string; renderer_kind: "deterministic_companion";
  passed: boolean; page_count: number; created_at: string;
  checks: Array<{ key: string; passed: boolean; severity: "blocker" | "warning";
    expected: unknown; actual: unknown; message: string }>;
  summary: { passed: boolean; rendered_pages: number; warning_count: number;
    blocker_count: number; underfilled_pages: number[]; renderer_kind: string;
    renderer_disclosure: string; [key: string]: unknown };
};

export type FormalManualJob = {
  id: string; task_id: string; model_config_id: string; version: number;
  status: string; current_step: string; progress: { completed: number; total: number; percent: number };
  steps: Array<{ key: string; status: string; attempt: number; summary: Record<string, unknown> }>;
};

export type ManualSectionBlock = { type: "paragraph"; text: string } |
  { type: "list"; lead: string; items: string[] } |
  { type: "table"; title: string; headers: string[]; rows: string[][] } |
  { type: "figure_request"; figure_key: string; title: string; purpose: string };

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

export type OverlayOperation = {
  action: "node.move" | "node.resize" | "node.style" | "node.label" | "node.hide" |
    "edge.route" | "edge.style" | "edge.label";
  target: string;
  payload: Record<string, unknown>;
  expected_target_fingerprint?: string;
};

export type RevisionDetail = {
  revision_id: string;
  operations: OverlayOperation[];
};

export async function connectSidecar(): Promise<SidecarConnection> {
  return invoke<SidecarConnection>("start_sidecar");
}

async function localFetch(connection: SidecarConnection, path: string, init?: RequestInit) {
  const execute = () => fetch(`${connection.baseUrl}${path}`, init);
  try { return await execute(); }
  catch (error) {
    if (!(error instanceof TypeError)) throw error;
    const refreshed = await connectSidecar();
    Object.assign(connection, refreshed);
    return execute();
  }
}

export async function loadWorkspace(
  connection: SidecarConnection,
  taskId: string,
): Promise<WorkspaceSnapshot> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/diagram-assets`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) throw new Error(`工作台加载失败 (${response.status})`);
  return response.json() as Promise<WorkspaceSnapshot>;
}

async function requireJson<T>(response: Response, fallback: string): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const payload = await response.json().catch(() => null) as
    { error?: { message?: string } } | null;
  throw new Error(payload?.error?.message ?? `${fallback} (${response.status})`);
}

export async function loadPreview(
  connection: SidecarConnection,
  revisionId: string,
): Promise<string> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/diagram-revisions/${revisionId}/preview.svg`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) throw new Error(`预览加载失败 (${response.status})`);
  return response.text();
}

export async function loadRevision(
  connection: SidecarConnection,
  revisionId: string,
): Promise<RevisionDetail> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/diagram-revisions/${revisionId}`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson<RevisionDetail>(response, "修订读取失败");
}

export async function saveRevision(
  connection: SidecarConnection,
  taskId: string,
  diagramKey: DiagramAsset["diagram_key"],
  operations: OverlayOperation[],
): Promise<AssetRevision> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/diagram-assets/${diagramKey}/revisions`,
    {
      method: "POST",
      headers: {
        "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ edit_source: "manual", operations }),
    },
  );
  return requireJson<AssetRevision>(response, "修订保存失败");
}

export async function listRevisions(
  connection: SidecarConnection,
  taskId: string,
  diagramKey: DiagramAsset["diagram_key"],
): Promise<AssetRevision[]> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/diagram-assets/${diagramKey}/revisions`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  const payload = await requireJson<{ items: AssetRevision[] }>(response, "历史版本读取失败");
  return payload.items;
}

export async function rollbackRevision(
  connection: SidecarConnection,
  taskId: string,
  diagramKey: DiagramAsset["diagram_key"],
  version: number,
): Promise<AssetRevision> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/diagram-assets/${diagramKey}/rollback`,
    {
      method: "POST",
      headers: {
        "X-Session-Token": connection.sessionToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ version }),
    },
  );
  return requireJson<AssetRevision>(response, "版本恢复失败");
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
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}`,
    { method: "DELETE", headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) throw new Error(`任务删除失败 (${response.status})`);
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
  base_url: string; model_name: string; credential_ref: string | null;
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
  return requireJson<FormalManualGenerationResult>(response, "正式说明书生成失败");
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
  destination: string): Promise<void> {
  await invoke("export_manual_document", { jobId, version, destination });
}
