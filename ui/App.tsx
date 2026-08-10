import { FormEvent, useEffect, useState } from "react";
import {
  connectSidecar, DiagramAsset, loadPreview, loadWorkspace,
  SidecarConnection, WorkspaceSnapshot,
} from "./api";

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
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);

  useEffect(() => {
    connectSidecar().then((value) => {
      setConnection(value);
      setMessage(`本地服务已连接 · v${value.version}`);
    }).catch(() => setMessage("浏览器预览模式 · 桌面服务未启动"));
  }, []);

  const assets = workspace?.assets ?? fallbackAssets;
  const active = assets.find((asset) => asset.diagram_key === selected) ?? assets[0];

  useEffect(() => {
    const revisionId = active.latest_revision?.revision_id;
    if (!connection || !revisionId) { setPreviewSrc(null); return; }
    let objectUrl: string | null = null;
    loadPreview(connection, revisionId).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      setPreviewSrc(objectUrl);
    }).catch(() => setMessage("SVG 预览加载失败"));
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [connection, active.latest_revision?.revision_id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!connection || !taskId.trim()) return;
    setMessage("正在读取资产…");
    try {
      const result = await loadWorkspace(connection, taskId.trim());
      setWorkspace(result);
      setMessage(`已载入任务 ${result.task_id.slice(0, 8)}…`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "资产读取失败");
    }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">著</span><div>
        <strong>软著材料助手</strong><small>本地证据化工作台</small>
      </div></div>
      <nav>
        <button className="nav-item">项目概览</button>
        <button className="nav-item">源码材料</button>
        <button className="nav-item active">图表资产</button>
        <button className="nav-item">说明书</button>
        <button className="nav-item">质量检查</button>
      </nav>
      <div className="side-status"><i className={connection ? "online" : "offline"} />
        <span>{message}</span></div>
    </aside>

    <main>
      <header className="topbar">
        <div><p className="eyebrow">DOCUMENT ASSETS</p><h1>图表资产</h1>
          <p>自动生成后仍可修改，所有调整均保留版本与证据关联。</p></div>
        <form onSubmit={submit} className="task-form">
          <input value={taskId} onChange={(event) => setTaskId(event.target.value)}
                 placeholder="输入任务 ID" aria-label="任务 ID" />
          <button disabled={!connection || !taskId.trim()}>载入</button>
        </form>
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
            <div className="toolbar-actions"><button>历史版本</button><button className="primary">编辑图表</button></div>
          </div>
          <div className="canvas">
            {previewSrc ?
              <img src={previewSrc} alt={active.title} /> :
              <div className="empty-state"><div className="empty-diagram">
                <span /><span /><span /><i /><i />
              </div><h2>准备好后在这里查看和编辑</h2>
                <p>载入已有任务，或先完成图表语义生成。</p></div>}
          </div>
          <footer><span>缩放 100%</span><span>自动保存关闭</span><span>证据关联开启</span></footer>
        </div>

        <aside className="inspector">
          <div className="section-title"><span>属性与 AI 修改</span></div>
          <div className="info-block"><label>当前资产</label><strong>{active.title}</strong>
            <small>{active.latest_revision ? `revision v${active.latest_revision.version}` : "尚无修订"}</small></div>
          <div className="ai-card"><span>AI</span><h3>对话修改图表</h3>
            <p>例如：突出核心生成流程，减少回退线的视觉干扰。</p>
            <textarea placeholder="描述你希望如何调整…" disabled={!active.latest_revision} />
            <button disabled={!active.latest_revision}>生成修改预览</button></div>
          <p className="hint">AI 只生成白名单修改操作，应用前会展示差异并等待确认。</p>
        </aside>
      </section>
    </main>
  </div>;
}
