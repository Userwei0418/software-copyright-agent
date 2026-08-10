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
export type Inspection = ProjectScanResult["inspection"];
export type SourceMaterialsSnapshot = {
  task: { id: string; status: string; current_stage_key: string; safe_error_message?: string | null };
  source_plan: null | { version: number; created_at: string; summary: {
    total_source_files: number; selected_files: number; selected_code_lines: number;
    excluded_files: number; grades: Record<"A" | "B" | "C", number>;
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

export async function listRecentTasks(
  connection: SidecarConnection,
): Promise<RecentTask[]> {
  const response = await fetch(`${connection.baseUrl}/api/v1/tasks?limit=20`, {
    headers: { "X-Session-Token": connection.sessionToken },
  });
  return (await requireJson<{ items: RecentTask[] }>(response, "最近任务读取失败")).items;
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
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "源码材料读取失败");
}

export async function runSourceMaterialAction(
  connection: SidecarConnection, taskId: string,
  action: "source-plan" | "code-preview" | "source-docx",
): Promise<SourceMaterialsSnapshot> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/${action}`,
    { method: "POST", headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "源码材料生成失败");
}

export async function loadCodePagePreview(
  connection: SidecarConnection, taskId: string,
): Promise<CodePagePreview> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/source-materials/code-preview/pages`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  return requireJson(response, "分页内容读取失败");
}

export async function revealSourceDocument(taskId: string): Promise<void> {
  await invoke("reveal_source_document", { taskId });
}
