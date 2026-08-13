import { useCallback, useEffect, useRef, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import {
  assembleFormalManualDocument, exportFormalFigureAsset, FormalFigureRevision, FormalManualFigure,
  listFormalFigureRevisions, listFormalManualFigures, listFormalManualJobs, listModelConfigs,
  loadFormalFigureAsset, ModelConfig, regenerateFormalManualFigure,
  revealExportedAsset, rollbackFormalFigure, runFormalManualQa, saveFormalFigureEditorRevision,
  SidecarConnection, streamFormalFigureAiPatch,
} from "./api";
import { DrawioEditor } from "./DrawioEditor";
import { ProjectSwitcher } from "./ProjectSwitcher";

type ExportFormat = "drawio" | "svg" | "png";
type ChatMessage = { role: "user" | "assistant"; text: string; streaming?: boolean };
type CanvasUndoSnapshot = { figureKey: string; xml: string; instruction: string };
const DRAWIO_CHAT_INTRO: ChatMessage[] = [{ role: "assistant",
  text: "我只以中间画布的当前 Draw.io XML 为基线。告诉我需要调整的节点、文字、颜色、尺寸或连线路径；结果会先载入画布，由你审阅后点击“确认并装配说明书”。" }];

function figureFailureLabel(figure: FormalManualFigure): string {
  const labels: Record<string, string> = {
    model_request: "模型请求失败", semantic_parse: "模型返回格式无效",
    semantic_validation: "图表语义校验失败", drawio_build: "Draw.io 本地构建失败",
    drawio_validation: "Draw.io 本地校验失败", svg_render: "SVG 本地渲染失败",
    png_render: "PNG 本地渲染失败", unexpected: "生成过程异常",
  };
  return labels[String(figure.qa?.category || "")] || "生成失败";
}

export function FormalDiagramWorkspace({ connection, taskId, onTaskChange, onOpenManual }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (value: string) => void;
  onOpenManual: () => void;
}) {
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [figures, setFigures] = useState<FormalManualFigure[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [revisions, setRevisions] = useState<FormalFigureRevision[]>([]);
  const [editorXml, setEditorXml] = useState("");
  const [liveXml, setLiveXml] = useState("");
  const [editorLoading, setEditorLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [exportedPath, setExportedPath] = useState("");
  const [aiInput, setAiInput] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [jobModelId, setJobModelId] = useState("");
  const [aiModelId, setAiModelId] = useState("");
  const [chatByFigure, setChatByFigure] = useState<Record<string, ChatMessage[]>>({});
  const [canvasUndoStack, setCanvasUndoStack] = useState<CanvasUndoSnapshot[]>([]);
  const activeJobRef = useRef("");
  const liveXmlRef = useRef("");
  const persistedXmlRef = useRef("");

  const selected = figures.find((item) => item.figure_key === selectedKey) || figures[0];
  const chatKey = selected ? `${selected.figure_key}:${aiModelId || "default"}` : "";

  useEffect(() => {
    if (!connection) { setModels([]); return; }
    let active = true;
    listModelConfigs(connection).then((items) => {
      if (active) setModels(items.filter((item) => item.enabled));
    }).catch((error) => {
      if (active) setMessage(error instanceof Error ? error.message : "模型配置读取失败");
    });
    return () => { active = false; };
  }, [connection]);

  useEffect(() => {
    setAiModelId((current) => {
      if (models.some((item) => item.id === current)) return current;
      if (models.some((item) => item.id === jobModelId)) return jobModelId;
      return models[0]?.id || "";
    });
  }, [models, jobModelId]);

  useEffect(() => {
    activeJobRef.current = ""; liveXmlRef.current = ""; persistedXmlRef.current = "";
    setJobId(""); setJobStatus(""); setJobModelId(""); setFigures([]); setSelectedKey(""); setRevisions([]);
    setEditorXml(""); setLiveXml(""); setExportedPath(""); setHistoryOpen(false);
    setCanvasUndoStack([]);
    setChatByFigure({});
    if (!connection || !taskId) return;
    let active = true;
    let timer = 0;
    setMessage("正在读取说明书正式图表…");
    const load = async () => {
      try {
        const jobs = await listFormalManualJobs(connection, taskId);
        if (!active) return;
        const newest = jobs[0];
        const newestRunning = newest && ["queued", "running"].includes(newest.status);
        let chosen = newestRunning ? newest : null;
        let items = chosen ? await listFormalManualFigures(connection, chosen.id).catch(() => []) : [];
        if (!chosen) {
          for (const job of jobs) {
            const candidates = await listFormalManualFigures(connection, job.id).catch(() => []);
            if (candidates.length) { chosen = job; items = candidates; break; }
          }
        }
        if (!active) return;
        if (chosen) {
          if (activeJobRef.current !== chosen.id) {
            activeJobRef.current = chosen.id;
            setJobModelId(chosen.model_config_id);
            setAiModelId(chosen.model_config_id);
          }
          setJobId(chosen.id); setJobStatus(chosen.status);
          setJobModelId(chosen.model_config_id); setFigures(items);
          setSelectedKey((current) => items.some((item) => item.figure_key === current)
            ? current : (items.find((item) => item.available) || items[0])?.figure_key || "");
          setMessage(newestRunning ? `说明书任务 v${chosen.version} 正在生成图表，本页自动更新。` : "");
        } else setMessage("当前项目还没有正式说明书图表，请先生成说明书。 ");
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : "正式图表读取失败");
      } finally { if (active) timer = window.setTimeout(load, 2000); }
    };
    void load();
    return () => { active = false; window.clearTimeout(timer); };
  }, [connection, taskId]);

  useEffect(() => {
    liveXmlRef.current = ""; persistedXmlRef.current = "";
    setEditorXml(""); setLiveXml(""); setRevisions([]); setHistoryOpen(false);
    setExportedPath(""); setCanvasUndoStack([]);
    if (!connection || !jobId || !selected?.available) return;
    let active = true;
    setEditorLoading(true);
    Promise.all([
      loadFormalFigureAsset(connection, jobId, selected.figure_key, "drawio"),
      listFormalFigureRevisions(connection, jobId, selected.figure_key),
    ]).then(([source, history]) => {
      if (active) { setEditorXml(source); setLiveXml(source);
        liveXmlRef.current = source; persistedXmlRef.current = source;
        setRevisions(history); }
    }).catch((error) => {
      if (active) setMessage(error instanceof Error ? error.message : "Draw.io 源文件读取失败");
    }).finally(() => { if (active) setEditorLoading(false); });
    return () => { active = false; };
  }, [connection, jobId, selectedKey, selected?.version, selected?.available]);

  useEffect(() => {
    if (chatKey) setChatByFigure((current) => current[chatKey]
      ? current : { ...current, [chatKey]: DRAWIO_CHAT_INTRO });
  }, [chatKey]);

  const refresh = useCallback(async (figureKey: string) => {
    if (!connection || !jobId) return;
    const items = await listFormalManualFigures(connection, jobId);
    setFigures(items); setSelectedKey(figureKey);
    setRevisions(await listFormalFigureRevisions(connection, jobId, figureKey));
  }, [connection, jobId]);

  async function regenerateSelected() {
    if (!connection || !jobId || !selected || busy) return;
    setBusy(true); setMessage(`正在重新生成“${selected.title}”…`);
    try {
      await regenerateFormalManualFigure(connection, jobId, selected.figure_key);
      await refresh(selected.figure_key);
      setMessage(`“${selected.title}”已重新生成并载入 Draw.io。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "图表重新生成失败"); }
    finally { setBusy(false); }
  }

  const saveEditorRevision = useCallback(async (payload: { xml: string; svg: string; png: string }) => {
    if (!connection || !jobId || !selectedKey) throw new Error("图表编辑会话已失效");
    let figureVersion = selected?.version || 0;
    if (payload.xml !== persistedXmlRef.current) {
      setMessage("正在保存 Draw.io、SVG 和 PNG 新版本…");
      const saved = await saveFormalFigureEditorRevision(
        connection, jobId, selectedKey, payload);
      figureVersion = saved.version;
      persistedXmlRef.current = payload.xml;
      setLiveXml(payload.xml); liveXmlRef.current = payload.xml;
    }
    setCanvasUndoStack([]);
    if (["queued", "running"].includes(jobStatus)) {
      await refresh(selectedKey);
      const detail = `图表 v${figureVersion} 已确认；当前说明书任务仍在运行，流程将在装配节点自动使用此版本。`;
      setMessage(detail);
      return { version: figureVersion, documentVersion: null, qaPassed: null, message: detail };
    }
    setMessage(`图表 v${figureVersion} 已确认，正在自动装配新的 Word 版本并逐页质检…`);
    try {
      const document = await assembleFormalManualDocument(connection, jobId);
      const qa = await runFormalManualQa(connection, jobId, document.version);
      await refresh(selectedKey);
      const detail = qa.qa_run.passed
        ? `图表 v${figureVersion} 已确认，并自动装配为说明书 v${document.version}；逐页质检通过。`
        : `图表 v${figureVersion} 已确认，并自动装配为说明书 v${document.version}；逐页质检未通过，请回到说明书查看问题。`;
      setMessage(detail);
      return { version: figureVersion, documentVersion: document.version,
        qaPassed: qa.qa_run.passed, message: detail };
    } catch (error) {
      await refresh(selectedKey);
      const reason = error instanceof Error ? error.message : "Word 装配失败";
      const detail = `图表 v${figureVersion} 已确认，但自动装配未完成：${reason}。图表版本已保留，可在说明书页重试装配。`;
      setMessage(detail);
      return { version: figureVersion, documentVersion: null, qaPassed: null, message: detail };
    }
  }, [connection, jobId, jobStatus, selectedKey, selected?.version, refresh]);

  const loadCanvasXml = useCallback((xml: string) => {
    setEditorXml(xml); setLiveXml(xml); liveXmlRef.current = xml;
  }, []);

  function undoLatestAiPatch() {
    if (!selected || aiBusy) return;
    const snapshot = canvasUndoStack[canvasUndoStack.length - 1];
    if (!snapshot || snapshot.figureKey !== selected.figure_key) return;
    loadCanvasXml(snapshot.xml);
    setCanvasUndoStack((current) => current.slice(0, -1));
    setMessage(`已撤销“${snapshot.instruction}”产生的 AI 修改；未创建新版本。`);
    setChatByFigure((current) => ({ ...current,
      [chatKey]: [...(current[chatKey] || DRAWIO_CHAT_INTRO),
        { role: "assistant", text: "已一键恢复到本次 AI 修改前的画布；没有请求模型，也没有创建新版本。" }] }));
  }

  function restoreConfirmedCanvas() {
    if (!selected || !persistedXmlRef.current || aiBusy) return;
    loadCanvasXml(persistedXmlRef.current);
    setCanvasUndoStack([]);
    setMessage(`已恢复“${selected.title}”当前已确认的 v${selected.version}；所有未确认修改已放弃。`);
  }

  async function applyAiPatch() {
    const instruction = aiInput.trim();
    if (!connection || !jobId || !selected || !instruction || !liveXml || !aiModelId || aiBusy) return;
    const figureKey = selected.figure_key;
    const activeChatKey = `${figureKey}:${aiModelId}`;
    const sourceXml = liveXml;
    setAiInput(""); setAiBusy(true);
    setChatByFigure((current) => ({ ...current,
      [activeChatKey]: [...(current[activeChatKey] || DRAWIO_CHAT_INTRO),
        { role: "user", text: instruction },
        { role: "assistant", text: "正在连接所选模型…", streaming: true }] }));
    let streamed = "";
    const updateProgress = (text: string) => setChatByFigure((current) => {
      const items = [...(current[activeChatKey] || DRAWIO_CHAT_INTRO)];
      const last = items.length - 1;
      if (last >= 0 && items[last].role === "assistant" && items[last].streaming) {
        items[last] = { role: "assistant", text, streaming: true };
      } else items.push({ role: "assistant", text, streaming: true });
      return { ...current, [activeChatKey]: items };
    });
    try {
      const result = await streamFormalFigureAiPatch(connection, jobId, figureKey,
        { instruction, xml: sourceXml, model_config_id: aiModelId }, (event) => {
          if (event.type === "phase") {
            if (event.phase === "repair") streamed = "";
            updateProgress(event.message);
          }
          if (event.type === "delta") {
            streamed += event.text;
            updateProgress(`模型正在流式生成受控操作（${streamed.length} 字）\n${streamed.slice(-1400)}`);
          }
        });
      if (liveXmlRef.current !== sourceXml) {
        throw new Error("生成期间画布已被手工修改；为避免覆盖当前 XML，本次结果未载入，请重新发送指令。");
      }
      setCanvasUndoStack((current) => [...current,
        { figureKey, xml: sourceXml, instruction }].slice(-8));
      loadCanvasXml(result.xml);
      setChatByFigure((current) => ({ ...current,
        [activeChatKey]: [...(current[activeChatKey] || DRAWIO_CHAT_INTRO).slice(0, -1),
          { role: "assistant",
            text: `${result.model_name} 已按当前 XML 将 ${result.operations.length} 项局部修改载入画布。${result.context_cache_hit ? "已续用本图与本模型的上下文缓存。" : "已建立本图与本模型的专用上下文。"}请检查效果；满意后点击“确认并装配说明书”。` }] }));
    } catch (error) {
      setChatByFigure((current) => ({ ...current,
        [activeChatKey]: [...(current[activeChatKey] || DRAWIO_CHAT_INTRO).slice(0, -1),
          { role: "assistant",
            text: error instanceof Error ? error.message : "AI 图表修改失败，请换一种更具体的说法。" }] }));
    } finally { setAiBusy(false); }
  }

  async function exportAsset(format: ExportFormat) {
    if (!selected) return;
    const destination = await save({ title: "导出图表资产",
      defaultPath: `${selected.title}-v${selected.version}.${format}`,
      filters: [{ name: format === "drawio" ? "Draw.io 可编辑文件" : format.toUpperCase(),
        extensions: [format] }] });
    if (!destination) return;
    setBusy(true);
    try {
      await exportFormalFigureAsset(jobId, selected.figure_key, format, destination);
      setExportedPath(destination); setMessage(`图表已导出：${destination}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "图表资产导出失败"); }
    finally { setBusy(false); }
  }

  async function restore(version: number) {
    if (!connection || !jobId || !selected || busy) return;
    setBusy(true);
    try {
      const result = await rollbackFormalFigure(connection, jobId, selected.figure_key, version);
      await refresh(selected.figure_key);
      setMessage(`已恢复完整 Draw.io、SVG 和 PNG，并创建 v${result.version}。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "图表恢复失败"); }
    finally { setBusy(false); }
  }

  return <main className="formal-diagram-page"><header className="topbar diagram-topbar"><div>
    <p className="eyebrow">DRAW.IO WORKBENCH</p><h1>图表资产</h1>
    <p>Draw.io 负责渲染与人工微调；AI 候选、会话撤销和正式历史由本地应用管理。</p></div>
    <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} />
  </header>
  {!taskId || !selected ? <section className="overview-placeholder source-empty"><span>图</span>
    <h2>{message || "请先选择项目"}</h2><p>正式图表由说明书正文语义与项目证据共同生成。</p>
    {taskId && <button onClick={onOpenManual}>返回说明书生成</button>}</section> :
  <section className="diagram-editor-page">
    {message && <div className="source-notice">{message}</div>}
    <div className="diagram-editor-toolbar"><div>
      <strong>{selected.title}</strong><span>v{selected.version} · {selected.figure_type} · 确认后自动装配新文档版本</span>
    </div><div>
      <button disabled={!selected.available || busy} onClick={() => exportAsset("drawio")}>导出源文件</button>
      <button disabled={!selected.available || busy} onClick={() => exportAsset("png")}>导出 PNG</button>
      {exportedPath && <button disabled={busy} onClick={() => revealExportedAsset(exportedPath)}>显示导出文件</button>}
      <button disabled={!selected.available || busy} onClick={() => setHistoryOpen(!historyOpen)}>历史版本</button>
      <button className="secondary" onClick={onOpenManual}>返回说明书</button>
    </div></div>
    <div className="diagram-editor-grid">
      <aside className="diagram-asset-column"><header><strong>说明书图表</strong><span>{figures.length}</span></header>
        <div>{figures.map((figure) => <button key={figure.figure_key}
          disabled={aiBusy}
          className={`${selected.figure_key === figure.figure_key ? "selected" : ""} ${figure.available ? "" : "failed"}`}
          onClick={() => setSelectedKey(figure.figure_key)}><span>图</span><div>
            <strong>{figure.title}</strong><small>{figure.section_key} · {figure.available ? `v${figure.version}` : figureFailureLabel(figure)}</small>
          </div><b>{figure.available ? "可编辑" : "重试"}</b></button>)}</div>
      </aside>
      <section className="diagram-canvas-column">
        {!selected.available ? <div className="diagram-inline-failure"><span>!</span>
          <h2>{figureFailureLabel(selected)}</h2><p>{selected.error || "生成过程没有产出有效图表资产。"}</p>
          <button disabled={busy} onClick={regenerateSelected}>{busy ? "正在重试…" : "重试此图"}</button></div> :
        editorLoading || !editorXml ? <div className="diagram-editor-loading"><i /><strong>正在加载 Draw.io…</strong></div> :
        <DrawioEditor key={`${selected.figure_key}-${selected.version}`} title={selected.title}
          xml={editorXml} onXmlChange={(xml) => { setLiveXml(xml); liveXmlRef.current = xml; }}
          onSave={saveEditorRevision}
          canUndoAi={Boolean(canvasUndoStack.length &&
            canvasUndoStack[canvasUndoStack.length - 1].figureKey === selected.figure_key)}
          hasUnconfirmedChanges={Boolean(liveXml && persistedXmlRef.current &&
            liveXml !== persistedXmlRef.current)}
          onUndoAi={undoLatestAiPatch} onRestoreConfirmed={restoreConfirmedCanvas} />}
        {historyOpen && <div className="diagram-inline-history"><header><div><strong>历史版本</strong>
          <small>本地应用保存完整的 Draw.io、SVG 和 PNG，不依赖编辑器历史</small></div><button onClick={() => setHistoryOpen(false)}>×</button></header>
          <div>{revisions.map((revision) => <article key={revision.revision_id}><div><b>v{revision.version}</b>
            <span>{revision.edit_source === "ai_generation" ? "AI 初稿" : "Draw.io 编辑"}</span>
            <small>{revision.created_at.replace("T", " ").slice(0, 16)}</small></div>
            <button disabled={revision.version === selected.version || busy} onClick={() => restore(revision.version)}>
              {revision.version === selected.version ? "当前" : "恢复"}</button></article>)}</div></div>}
      </section>
      <aside className="diagram-ai-column"><header><div><strong>AI 图表助手</strong><small>当前 XML · 专用提示词 · 图/模型独立缓存</small></div>
        <div className="diagram-ai-model"><select aria-label="AI 图表模型" value={aiModelId}
          disabled={aiBusy || !models.length} onChange={(event) => setAiModelId(event.target.value)}>
          {models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.model_name}</option>)}
        </select><span>{aiBusy ? "流式生成中" : models.length ? "可用" : "未配置"}</span></div></header>
        <div className="diagram-ai-chat">{Boolean(canvasUndoStack.length &&
          canvasUndoStack[canvasUndoStack.length - 1].figureKey === selected.figure_key) &&
          <div className="diagram-ai-recovery"><strong>这次 AI 修改尚未确认</strong>
            <span>应用已保留 {canvasUndoStack.length} 个会话快照，可逐步回退，不请求模型。</span>
            <button disabled={aiBusy} onClick={undoLatestAiPatch}>撤销上一次 AI 修改</button></div>}
          {(chatByFigure[chatKey] || DRAWIO_CHAT_INTRO).map((item, index) => <article key={index} className={`${item.role}${item.streaming ? " streaming" : ""}`}>
          <b>{item.role === "user" ? "你" : "AI"}</b><p>{item.text}</p></article>)}</div>
        <form onSubmit={(event) => { event.preventDefault(); void applyAiPatch(); }}><textarea value={aiInput}
          onChange={(event) => setAiInput(event.target.value)} disabled={!selected.available || aiBusy || !aiModelId}
          placeholder="例如：把核心服务改为深蓝色，将入口节点向左移动，并缩短连线文字…" />
          <div><small>AI 只生成受控局部操作，不会直接覆盖已保存版本。</small>
            <button disabled={!aiInput.trim() || !liveXml || !aiModelId || aiBusy}>{aiBusy ? "流式生成中…" : "载入画布"}</button></div></form>
      </aside>
    </div>
  </section>}
  </main>;
}
