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
