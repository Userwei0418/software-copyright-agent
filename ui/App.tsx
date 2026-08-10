import { useEffect, useRef, useState } from "react";
import {
  AssetRevision, connectSidecar, DiagramAsset, listRevisions, loadPreview, loadRevision,
  listRecentTasks, loadWorkspace, OverlayOperation, rollbackRevision, saveRevision, SidecarConnection,
  WorkspaceSnapshot,
} from "./api";
import { InteractiveDiagram } from "./InteractiveDiagram";
import { ProjectOverview } from "./ProjectOverview";
import { SourceMaterials } from "./SourceMaterials";
import { ManualWorkspace } from "./ManualWorkspace";
import { AssetLibrary } from "./AssetLibrary";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { Settings } from "./Settings";
import { FormalDiagramWorkspace } from "./FormalDiagramWorkspace";
import { ScreenshotAssetWorkspace } from "./ScreenshotAssetWorkspace";

const fallbackAssets: DiagramAsset[] = [
  { diagram_key: "system_architecture", title: "系统总体架构图", revision_count: 0,
    latest_revision: null, editable: true },
  { diagram_key: "core_business_flow", title: "核心业务流程图", revision_count: 0,
    latest_revision: null, editable: true },
];

export function App() {
  const [connection, setConnection] = useState<SidecarConnection | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [taskId, setTaskId] = useState("");
  const [selected, setSelected] = useState("system_architecture");
  const [message, setMessage] = useState("正在启动本地服务…");
  const [previewSvg, setPreviewSvg] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [revisions, setRevisions] = useState<AssetRevision[]>([]);
  const [previewRevision, setPreviewRevision] = useState<AssetRevision | null>(null);
  const [undoVersion, setUndoVersion] = useState<number | null>(null);
  const [redoVersion, setRedoVersion] = useState<number | null>(null);
  const [page, setPage] = useState<"overview" | "source" | "manual" | "screenshots" | "diagrams" | "assets" | "settings">("overview");
  const [previewRequested, setPreviewRequested] = useState(0);
  const connectionAttempt = useRef<Promise<SidecarConnection> | null>(null);

  async function ensureConnection(): Promise<SidecarConnection> {
    if (connection) return connection;
    if (connectionAttempt.current) return connectionAttempt.current;
    setMessage("正在连接本地服务…");
    connectionAttempt.current = (async () => { try {
      const value = await connectSidecar();
      setConnection(value);
      setMessage(`本地服务已连接 · v${value.version}`);
      listRecentTasks(value).then((recent) => {
        if (recent.length) setTaskId((current) => current || recent[0].task_id);
      }).catch(() => setMessage("本地服务已连接，但最近项目读取失败"));
      return value;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setMessage(`本地服务连接失败 · ${detail}`);
      throw new Error(`本地服务连接失败：${detail}`);
    } finally { connectionAttempt.current = null; } })();
    return connectionAttempt.current;
  }

  useEffect(() => {
    ensureConnection().catch(() => undefined);
  }, []);

  const assets = workspace?.assets ?? fallbackAssets;
  const active = assets.find((asset) => asset.diagram_key === selected) ?? assets[0];
  const currentRevisionId = active.latest_revision?.revision_id;
  const displayedRevisionId = previewRevision?.revision_id ?? currentRevisionId;

  useEffect(() => {
    const revisionId = displayedRevisionId;
    if (!connection || !revisionId) { setPreviewSvg(null); return; }
    loadPreview(connection, revisionId).then((source) => {
      setPreviewSvg(source);
    }).catch(() => setMessage("SVG 预览加载失败"));
  }, [connection, displayedRevisionId]);

  useEffect(() => {
    setHistoryOpen(false);
    setPreviewRevision(null);
    setSelectedNode(null);
  }, [selected]);

  async function switchDiagramProject(value: string) {
    setTaskId(value); setWorkspace(null);
    if (!connection || !value) return;
    setMessage("正在读取资产…");
    try { setWorkspace(await loadWorkspace(connection, value)); setMessage("项目图表资产已载入"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "资产读取失败"); }
  }

  async function moveNode(key: string, x: number, y: number) {
    const revisionId = active.latest_revision?.revision_id;
    if (!connection || !workspace || !revisionId) return;
    setSaving(true);
    setMessage(`正在保存节点 ${key}…`);
    try {
      const detail = await loadRevision(connection, revisionId);
      const move: OverlayOperation = { action: "node.move", target: key, payload: { x, y } };
      const operations = detail.operations.filter(
        (operation) => !(operation.action === "node.move" && operation.target === key),
      );
      operations.push(move);
      const saved = await saveRevision(
        connection, workspace.task_id, active.diagram_key, operations,
      );
      const refreshed = await loadWorkspace(connection, workspace.task_id);
      setWorkspace(refreshed);
      setUndoVersion(active.latest_revision?.version ?? null);
      setRedoVersion(null);
      setMessage(`节点已保存 · revision v${saved.version}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "节点保存失败，已恢复原位置");
      throw error;
    } finally {
      setSaving(false);
    }
  }

  async function openHistory() {
    if (!connection || !workspace) return;
    setMessage("正在读取历史版本…");
    try {
      const items = await listRevisions(connection, workspace.task_id, active.diagram_key);
      setRevisions(items);
      setHistoryOpen(true);
      setMessage(`已读取 ${items.length} 个修订版本`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "历史版本读取失败");
    }
  }

  async function restoreVersion(version: number, mode: "history" | "undo" | "redo" = "history") {
    if (!connection || !workspace) return;
    const previousVersion = active.latest_revision?.version ?? null;
    setSaving(true);
    setMessage(`正在恢复 revision v${version}…`);
    try {
      const restored = await rollbackRevision(
        connection, workspace.task_id, active.diagram_key, version,
      );
      const [refreshed, items] = await Promise.all([
        loadWorkspace(connection, workspace.task_id),
        listRevisions(connection, workspace.task_id, active.diagram_key),
      ]);
      setWorkspace(refreshed);
      setRevisions(items);
      setPreviewRevision(null);
      if (mode === "undo") {
        setUndoVersion(null);
        setRedoVersion(previousVersion);
        setMessage(`已撤销到 v${version} · 新 revision v${restored.version}`);
      } else if (mode === "redo") {
        setUndoVersion(previousVersion);
        setRedoVersion(null);
        setMessage(`已重做到 v${version} · 新 revision v${restored.version}`);
      } else {
        setUndoVersion(previousVersion);
        setRedoVersion(null);
        setMessage(`已从 v${version} 创建最新 revision v${restored.version}`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "版本恢复失败");
    } finally {
      setSaving(false);
    }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">著</span><div>
        <strong>软著材料助手</strong><small>本地证据化工作台</small>
      </div></div>
      <nav>
        <button className={`nav-item ${page === "overview" ? "active" : ""}`}
          onClick={() => setPage("overview")}>项目概览</button>
        <button className={`nav-item ${page === "source" ? "active" : ""}`}
          onClick={() => setPage("source")}>源码材料</button>
        <button className={`nav-item ${page === "manual" ? "active" : ""}`}
          onClick={() => setPage("manual")}>说明书</button>
        <button className={`nav-item ${page === "screenshots" ? "active" : ""}`}
          onClick={() => setPage("screenshots")}>界面截图</button>
        <button className={`nav-item ${page === "diagrams" ? "active" : ""}`}
          onClick={() => setPage("diagrams")}>图表资产</button>
        <button className={`nav-item ${page === "assets" ? "active" : ""}`}
          onClick={() => setPage("assets")}>我的资产</button>
        <button className={`nav-item ${page === "settings" ? "active" : ""}`}
          onClick={() => setPage("settings")}>设置</button>
        <button className="nav-item" disabled>质量检查 <small>待开发</small></button>
      </nav>
      <div className="side-status"><i className={connection ? "online" : "offline"} />
        <span>{message}</span></div>
    </aside>

    {page === "overview" ? <ProjectOverview connection={connection}
      ensureConnection={ensureConnection} onTaskCreated={(value) => setTaskId(value)} /> : page === "source" ?
      <SourceMaterials connection={connection} taskId={taskId}
        onTaskCreated={setTaskId} onBackToOverview={() => setPage("overview")}
        previewRequested={previewRequested} /> : page === "manual" ?
      <ManualWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenDiagrams={() => setPage("diagrams")} /> : page === "assets" ?
      <AssetLibrary connection={connection} onOpen={(value) => { setTaskId(value); setPage("source"); }}
        onPreview={(value) => { setTaskId(value); setPreviewRequested((count) => count + 1); setPage("source"); }}
        onPreviewManual={(value) => { setTaskId(value); setPage("manual"); }} /> :
      page === "settings" ? <Settings connection={connection} /> : page === "diagrams" ?
      <FormalDiagramWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenManual={() => setPage("manual")} /> : page === "screenshots" ?
      <ScreenshotAssetWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenManual={() => setPage("manual")} /> : <main>
      <header className="topbar">
        <div><p className="eyebrow">DOCUMENT ASSETS</p><h1>图表资产</h1>
          <p>自动生成后仍可修改，所有调整均保留版本与证据关联。</p></div>
        <ProjectSwitcher connection={connection} taskId={taskId} onChange={switchDiagramProject} />
      </header>

      <section className="workspace">
        <div className="asset-list">
          <div className="section-title"><span>资产列表</span><em>{assets.length}</em></div>
          {assets.map((asset) => <button key={asset.diagram_key}
            className={`asset-card ${selected === asset.diagram_key ? "selected" : ""}`}
            onClick={() => setSelected(asset.diagram_key)}>
            <span className="asset-icon">{asset.diagram_key === "system_architecture" ? "架" : "流"}</span>
            <span><strong>{asset.title}</strong><small>
              {asset.latest_revision ? `修订 v${asset.latest_revision.version}` : "等待首次生成"}
            </small></span>
            <b className={asset.latest_revision?.status === "conflicted" ? "warning" : "clean"}>
              {asset.latest_revision?.status === "conflicted" ? "有冲突" : "可编辑"}
            </b>
          </button>)}
        </div>

        <div className="canvas-panel">
          <div className="canvas-toolbar"><div><strong>{active.title}</strong>
            <span>{active.revision_count} 个修订版本</span></div>
            <div className="toolbar-actions"><button disabled={undoVersion === null || saving || !!previewRevision}
              onClick={() => undoVersion !== null && restoreVersion(undoVersion, "undo")}>撤销</button>
              <button disabled={redoVersion === null || saving || !!previewRevision}
                onClick={() => redoVersion !== null && restoreVersion(redoVersion, "redo")}>重做</button>
              <button disabled={!currentRevisionId || saving}
              onClick={openHistory}>历史版本</button>
              <button className="primary" disabled={!previewSvg || saving || !!previewRevision}>
                {saving ? "正在保存…" : previewRevision ? `预览 v${previewRevision.version}` : "拖拽编辑已开启"}
              </button></div>
          </div>
          <div className="canvas">
            {previewSvg ?
              <InteractiveDiagram svg={previewSvg} disabled={saving || !!previewRevision}
                onMove={moveNode} onSelect={setSelectedNode} /> :
              <div className="empty-state"><div className="empty-diagram">
                <span /><span /><span /><i /><i />
              </div><h2>准备好后在这里查看和编辑</h2>
                <p>载入已有任务，或先完成图表语义生成。</p></div>}
          </div>
          {historyOpen && <div className="history-drawer">
            <div className="history-head"><div><strong>历史版本</strong>
              <small>恢复操作会创建新版本，不覆盖历史</small></div>
              <button onClick={() => { setHistoryOpen(false); setPreviewRevision(null); }}>×</button>
            </div>
            <div className="history-list">
              {revisions.map((revision) => <div className={`history-item ${
                displayedRevisionId === revision.revision_id ? "viewing" : ""}`}
                key={revision.revision_id}>
                <button className="history-preview" onClick={() => setPreviewRevision(
                  revision.revision_id === currentRevisionId ? null : revision
                )}>
                  <b>v{revision.version}</b><span>{revision.edit_source === "ai" ? "AI" : "人工"}</span>
                  <small>{revision.operation_count ?? 0} 项操作 · {
                    revision.status === "conflicted" ? "有冲突" : "正常"}</small>
                </button>
                <button className="restore" disabled={revision.revision_id === currentRevisionId || saving}
                  onClick={() => restoreVersion(revision.version)}>
                  {revision.revision_id === currentRevisionId ? "当前" : "恢复"}
                </button>
              </div>)}
            </div>
          </div>}
          <footer><span>缩放 自适应</span><span>拖动结束后保存</span><span>证据关联开启</span></footer>
        </div>

        <aside className="inspector">
          <div className="section-title"><span>属性与 AI 修改</span></div>
          <div className="info-block"><label>当前资产</label><strong>{active.title}</strong>
            <small>{active.latest_revision ? `revision v${active.latest_revision.version}` : "尚无修订"}</small></div>
          <div className="info-block"><label>选中节点</label>
            <strong>{selectedNode ?? "尚未选择"}</strong>
            <small>{selectedNode ? "拖动节点以创建新修订版本" : "在画布中点击任意节点"}</small></div>
          <div className="ai-card"><span>AI</span><h3>对话修改图表</h3>
            <p>例如：突出核心生成流程，减少回退线的视觉干扰。</p>
            <textarea placeholder="描述你希望如何调整…" disabled={!active.latest_revision} />
            <button disabled={!active.latest_revision}>生成修改预览</button></div>
          <p className="hint">AI 只生成白名单修改操作，应用前会展示差异并等待确认。</p>
        </aside>
      </section>
    </main>}
  </div>;
}
