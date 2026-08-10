import { useEffect, useState } from "react";
import {
  createFormalFigureRevision, FormalFigureRevision, FormalManualFigure,
  listFormalFigureRevisions, listFormalManualFigures, listFormalManualJobs,
  loadFormalFigureAsset, OverlayOperation, previewFormalFigureAiEdit,
  rollbackFormalFigure, SidecarConnection,
} from "./api";
import { InteractiveDiagram } from "./InteractiveDiagram";
import { ProjectSwitcher } from "./ProjectSwitcher";

type Format = "svg" | "png" | "drawio";
type AiPreview = { operations: OverlayOperation[]; preview_svg: string;
  elapsed_ms: number; model_name: string };

export function FormalDiagramWorkspace({ connection, taskId, onTaskChange, onOpenManual }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (value: string) => void;
  onOpenManual: () => void;
}) {
  const [jobId, setJobId] = useState("");
  const [figures, setFigures] = useState<FormalManualFigure[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [format, setFormat] = useState<Format>("svg");
  const [asset, setAsset] = useState("");
  const [revisions, setRevisions] = useState<FormalFigureRevision[]>([]);
  const [selectedNode, setSelectedNode] = useState("");
  const [nodeLabel, setNodeLabel] = useState("");
  const [instruction, setInstruction] = useState("");
  const [aiPreview, setAiPreview] = useState<AiPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [message, setMessage] = useState("");

  const selected = figures.find((item) => item.figure_key === selectedKey) || figures[0];
  const selectedSemanticNode = selected?.semantic.nodes.find((item) => item.key === selectedNode);

  useEffect(() => {
    setJobId(""); setFigures([]); setSelectedKey(""); setAsset(""); setRevisions([]);
    setAiPreview(null); setSelectedNode("");
    if (!connection || !taskId) return;
    setMessage("正在读取说明书正式图表…");
    (async () => {
      const jobs = await listFormalManualJobs(connection, taskId);
      for (const job of jobs) {
        const items = await listFormalManualFigures(connection, job.id);
        if (items.length) {
          setJobId(job.id); setFigures(items); setSelectedKey(items[0].figure_key);
          setMessage(""); return;
        }
      }
      setMessage("当前项目还没有正式说明书图表，请先一键生成说明书。");
    })().catch((error) => setMessage(error instanceof Error ? error.message : "正式图表读取失败"));
  }, [connection, taskId]);

  useEffect(() => {
    if (!connection || !jobId || !selected) return;
    let active = true;
    if (format === "png" && asset.startsWith("blob:")) URL.revokeObjectURL(asset);
    setAsset(""); setAiPreview(null); setSelectedNode(""); setHistoryOpen(false);
    Promise.all([
      loadFormalFigureAsset(connection, jobId, selected.figure_key, format),
      listFormalFigureRevisions(connection, jobId, selected.figure_key),
    ]).then(([source, history]) => { if (active) { setAsset(source); setRevisions(history); } })
      .catch((error) => setMessage(error instanceof Error ? error.message : "图表资产读取失败"));
    return () => { active = false; };
  }, [connection, jobId, selectedKey, selected?.version, format]);

  useEffect(() => {
    setNodeLabel(selectedSemanticNode?.display_label || selectedSemanticNode?.label || "");
  }, [selectedSemanticNode?.key, selectedSemanticNode?.display_label, selectedSemanticNode?.label]);

  async function refresh(figureKey = selected?.figure_key) {
    if (!connection || !jobId || !figureKey) return;
    const items = await listFormalManualFigures(connection, jobId);
    setFigures(items); setSelectedKey(figureKey);
    setRevisions(await listFormalFigureRevisions(connection, jobId, figureKey));
  }

  async function applyOperations(operations: OverlayOperation[], source: "manual" | "ai") {
    if (!connection || !jobId || !selected) return;
    setBusy(true); setMessage(source === "ai" ? "正在应用 AI 修改并渲染新版本…" : "正在保存图表新版本…");
    try {
      const result = await createFormalFigureRevision(
        connection, jobId, selected.figure_key, operations, source);
      await refresh(); setAiPreview(null); setInstruction("");
      setMessage(`图表 v${result.version} 已保存；原说明书未被覆盖，请重新装配后再导出。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "图表修改失败"); }
    finally { setBusy(false); }
  }

  async function moveNode(key: string, x: number, y: number) {
    await applyOperations([{ action: "node.move", target: key, payload: { x, y } }], "manual");
  }

  async function saveLabel() {
    if (!selectedNode || !nodeLabel.trim()) return;
    await applyOperations([{ action: "node.label", target: selectedNode,
      payload: { value: nodeLabel.trim() } }], "manual");
  }

  async function requestAiPreview() {
    if (!connection || !jobId || !selected || instruction.trim().length < 3) return;
    setBusy(true); setMessage("AI 正在把自然语言要求转换为受限图表操作…");
    try {
      const result = await previewFormalFigureAiEdit(
        connection, jobId, selected.figure_key, instruction.trim());
      setAiPreview(result); setFormat("svg");
      setMessage(`AI 已生成 ${result.operations.length} 项修改预览，确认后才会保存。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "AI 修改预览失败"); }
    finally { setBusy(false); }
  }

  async function restore(version: number) {
    if (!connection || !jobId || !selected) return;
    setBusy(true); setMessage(`正在从 v${version} 创建新的恢复版本…`);
    try {
      const result = await rollbackFormalFigure(connection, jobId, selected.figure_key, version);
      await refresh(); setMessage(`已恢复历史内容并创建 v${result.version}，历史版本未被覆盖。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "图表恢复失败"); }
    finally { setBusy(false); }
  }

  return <main className="formal-diagram-page"><header className="topbar"><div>
    <p className="eyebrow">EDITABLE DIAGRAM ASSETS</p><h1>图表资产</h1>
    <p>这里展示实际插入说明书的 Draw.io 图表；修改会创建新版本，并提示重新装配文档。</p></div>
    <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} />
  </header>
  {!taskId || !selected ? <section className="overview-placeholder source-empty"><span>图</span>
    <h2>{message || "请先选择项目"}</h2><p>正式图表由说明书正文语义与项目证据共同生成。</p>
    {taskId && <button onClick={onOpenManual}>返回说明书生成</button>}</section> :
  <section className="formal-diagram-content">
    {message && <div className="source-notice">{message}</div>}
    <div className="diagram-workbench">
      <aside className="asset-list"><div className="section-title"><span>说明书图表</span><em>{figures.length}</em></div>
        {figures.map((figure) => <button key={figure.figure_key}
          className={`asset-card ${selected.figure_key === figure.figure_key ? "selected" : ""}`}
          onClick={() => setSelectedKey(figure.figure_key)}><span className="asset-icon">图</span><span>
          <strong>{figure.title}</strong><small>{figure.section_key} · v{figure.version}</small></span>
          <b className="clean">可编辑</b></button>)}</aside>
      <div className="canvas-panel"><div className="canvas-toolbar"><div><strong>{selected.title}</strong>
        <span>正式资产 v{selected.version} · {selected.figure_type}</span></div><div className="toolbar-actions">
        {(["svg", "png", "drawio"] as Format[]).map((item) => <button key={item}
          className={format === item ? "primary" : ""} onClick={() => setFormat(item)}>{item.toUpperCase()}</button>)}
        <button onClick={() => setHistoryOpen(!historyOpen)}>历史版本</button></div></div>
        <div className="canvas">{format === "svg" && (aiPreview?.preview_svg || asset) ?
          <InteractiveDiagram svg={aiPreview?.preview_svg || asset} disabled={busy || !!aiPreview}
            onMove={moveNode} onSelect={(key) => setSelectedNode(key || "")} /> :
          format === "png" && asset ? <img className="formal-diagram-png" src={asset} alt={selected.title} /> :
          format === "drawio" && asset ? <pre className="drawio-source">{asset}</pre> :
          <div className="empty-state"><h2>正在载入图表…</h2></div>}</div>
        <footer><span>拖动节点即创建修订</span><span>Draw.io 源文件始终保留</span>
          <button onClick={onOpenManual}>返回说明书重新装配</button></footer>
        {historyOpen && <div className="history-drawer"><div className="history-head"><div><strong>历史版本</strong>
          <small>恢复会创建新版本，不覆盖历史</small></div><button onClick={() => setHistoryOpen(false)}>×</button></div>
          <div className="history-list">{revisions.map((revision) => <div className="history-item" key={revision.revision_id}>
            <div className="history-preview"><b>v{revision.version}</b><span>{revision.edit_source === "ai" ? "AI 修改" :
              revision.edit_source === "manual" ? "人工修改" : "AI 初稿"}</span><small>{revision.operation_count} 项操作 · {
              revision.created_at.replace("T", " ").slice(0, 16)}</small></div>
            <button className="restore" disabled={revision.version === selected.version || busy}
              onClick={() => restore(revision.version)}>{revision.version === selected.version ? "当前" : "恢复"}</button>
          </div>)}</div></div>}
      </div>
      <aside className="inspector"><div className="section-title"><span>属性与 AI 修改</span></div>
        <div className="info-block"><label>当前正式资产</label><strong>{selected.title}</strong>
          <small>{selected.semantic.nodes.length} 节点 · {selected.semantic.edges.length} 关系</small></div>
        <div className="info-block node-label-editor"><label>选中节点</label><strong>{selectedNode || "尚未选择"}</strong>
          {selectedNode && <><input value={nodeLabel} onChange={(event) => setNodeLabel(event.target.value)} />
            <button disabled={busy || !nodeLabel.trim()} onClick={saveLabel}>保存节点名称</button></>}</div>
        <div className="ai-card"><span>AI</span><h3>对话修改图表</h3>
          <p>AI 只可生成白名单局部操作，先预览、再确认，不会直接覆盖当前图。</p>
          <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)}
            placeholder="例如：突出业务服务，并把入口节点移到左上方…" disabled={busy} />
          {!aiPreview ? <button disabled={busy || instruction.trim().length < 3}
            onClick={requestAiPreview}>{busy ? "正在生成预览…" : "生成修改预览"}</button> :
            <div className="ai-preview-actions"><button disabled={busy}
              onClick={() => applyOperations(aiPreview.operations, "ai")}>确认应用 {aiPreview.operations.length} 项</button>
              <button className="secondary" onClick={() => setAiPreview(null)}>取消预览</button></div>}</div>
      </aside>
    </div>
  </section>}
  </main>;
}
