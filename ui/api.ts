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
};

export type DiagramAsset = {
  diagram_key: "system_architecture" | "core_business_flow";
  title: string;
  revision_count: number;
  latest_revision: AssetRevision | null;
  editable: boolean;
};

export type WorkspaceSnapshot = { task_id: string; assets: DiagramAsset[] };

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

export async function loadPreview(
  connection: SidecarConnection,
  revisionId: string,
): Promise<Blob> {
  const response = await fetch(
    `${connection.baseUrl}/api/v1/diagram-revisions/${revisionId}/preview.svg`,
    { headers: { "X-Session-Token": connection.sessionToken } },
  );
  if (!response.ok) throw new Error(`预览加载失败 (${response.status})`);
  return response.blob();
}
