import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AppSettings, createQuickStartRun, listModelConfigs, listQuickStartRuns,
  discardQuickStartRun, loadAppSettings, loadQuickStartRun, ModelConfig, QuickStartConfig, QuickStartRun,
  QuickStartStage,
  FormalManualDocument, FormalManualPreview, FormalManualScreenshot, ProjectScreenshotAsset,
  listFormalManualDocuments, listFormalManualSections, listFormalScreenshots,
  loadFormalFigureAsset, loadFormalManualPreview, loadFormalManualQa, loadFormalManualQaPage,
  loadFormalScreenshotImage, loadScreenshotEvidenceImage, loadScreenshotEvidenceWorkspace,
  ManualSectionBlock,
  retryQuickStartRun, SidecarConnection,
} from "./api";
import "./quick-start.css";
import "./quick-start-enhancements.css";

type Props = {
  connection: SidecarConnection | null;
  ensureConnection: () => Promise<SidecarConnection>;
  onTaskChange: (taskId: string) => void;
  onOpenAssets: () => void;
  onOpenSettings: () => void;
  onNavigate: (page: "quick" | "overview" | "source" | "manual" | "screenshots" |
    "diagrams" | "assets" | "settings") => void;
};

const defaultConfig: QuickStartConfig = {
  project_path: "", software_name: "", version: "V1.0", screenshot_folder: "",
  manual_model_id: "", diagram_model_id: "", vision_model_id: "",
  source_strategy: "standard", concurrency: 3, retry_limit: 2,
  recursive_screenshots: true, finalize_with_warnings: true,
  sensitive_confirmed: false, auto_adopt_confirmed: false,
};

export function QuickStart({ connection, ensureConnection, onTaskChange, onOpenAssets,
  onOpenSettings, onNavigate }: Props) {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [config, setConfig] = useState<QuickStartConfig>(defaultConfig);
  const [run, setRun] = useState<QuickStartRun | null>(null);
  const [history, setHistory] = useState<QuickStartRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("先把必须确认的信息一次填完，随后可离开此页等待结果。");
  const executionCanvas = useRef<HTMLDivElement | null>(null);
  const canvasFlow = useRef<HTMLDivElement | null>(null);
  const lastPointerPosition = useRef<{ x: number; y: number } | null>(null);
  const [hoveredRelationKey, setHoveredRelationKey] = useState("");
  const [previewBusyNodeKey, setPreviewBusyNodeKey] = useState("");
  const [previewPageBusy, setPreviewPageBusy] = useState(false);
  const [quickPreview, setQuickPreview] = useState<QuickArtifactPreview | null>(null);
  const [dependencyGraph, setDependencyGraph] = useState({ width: 0, height: 0,
    paths: [] as Array<{ key: string; sourceKey: string; targetKey: string; d: string; active: boolean }> });

  useEffect(() => {
    if (!connection) return;
    Promise.all([listModelConfigs(connection), loadAppSettings(connection),
      listQuickStartRuns(connection)]).then(([items, settings, runs]) => {
        setModels(items.filter((item) => item.enabled));
        setHistory(runs);
        setRun(runs[0] || null);
        setConfig((current) => withDefaults(current, items, settings));
      }).catch((error) => setNotice(error instanceof Error ? error.message : "快速开始初始化失败"));
  }, [connection]);

  useEffect(() => {
    if (!connection || !run || !["queued", "running"].includes(run.status)) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const value = await loadQuickStartRun(connection, run.id);
        if (!disposed) {
          setRun(value);
          if (value.task_id) onTaskChange(value.task_id);
          if (value.status === "completed") setNotice("双文档已生成并进入“我的资产”。");
        }
      } catch (error) {
        if (!disposed) setNotice(error instanceof Error ? error.message : "进度刷新失败");
      }
    };
    const timer = window.setInterval(refresh, 1400);
    void refresh();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [connection, run?.id, run?.status]);

  const progress = useMemo(() => {
    if (!run) return 0;
    if (run.status === "completed") return 100;
    const weights: Record<string, number> = {
      scan: .07, confirm: .04,
      source_plan: .08, code_preview: .09, source_docx: .13,
      screenshots: .12, manual: .29,
      finalize: .11, delivery: .07,
    };
    let resolved = 0;
    for (const stage of run.stages) {
      const weight = weights[stage.key] ?? (1 / Math.max(1, run.stages.length));
      if (stage.status === "completed") {
        resolved += weight;
        continue;
      }
      if (stage.status !== "running") continue;
      let fraction = .12;
      if (stage.key === "manual" && run.manual_job) {
        // This long stage owns the real chapter/diagram/screenshot node graph.
        // Reuse its measured progress so the global orb does not appear frozen.
        fraction = Math.min(.96, Math.max(.05, run.manual_job.progress.percent / 100));
      }
      resolved += weight * fraction;
    }
    return Math.min(99, Math.max(1, Math.round(resolved * 100)));
  }, [run]);
  // Keep the empty collection referentially stable. A fresh [] on every render
  // retriggered the layout effect below, which then wrote dependencyGraph again
  // and caused React's maximum-update-depth crash when Quick Start had no nodes.
  const liveNodes = useMemo(() => run?.manual_job?.nodes ?? [], [run?.manual_job?.nodes]);
  const runningNodes = liveNodes.filter((node) => node.status === "running");
  const settledNodes = liveNodes.filter((node) => ["completed", "completed_with_warnings"].includes(node.status));
  const recentActivity = useMemo(() => buildActivity(run), [run]);
  const elapsed = run?.started_at ? durationLabel(Date.now() - new Date(run.started_at).getTime()) : "尚未启动";
  const visionModels = models.filter((item) => item.vision_verified && item.supports_vision === true);
  const canvasLanes = useMemo(() => buildCanvasLanes(liveNodes), [liveNodes]);
  const focusNodeKey = runningNodes[0]?.key || liveNodes.find((node) => node.status === "failed")?.key || "";
  // Relations are intentionally ephemeral. Persisting the last clicked or running
  // node left apparently orphaned lines after a fast scroll or pointer switch.
  const activeRelationKey = hoveredRelationKey;
  const activeRelations = dependencyGraph.paths.filter((path) =>
    path.sourceKey === activeRelationKey || path.targetKey === activeRelationKey);
  const relatedNodeKeys = new Set(activeRelations.flatMap((path) => [path.sourceKey, path.targetKey]));
  useEffect(() => () => {
    const url = quickPreview?.kind === "figure" ? quickPreview.url
      : quickPreview?.kind === "document" ? quickPreview.url
        : quickPreview?.kind === "screenshots" ? quickPreview.url : null;
    if (url) URL.revokeObjectURL(url);
  }, [quickPreview]);
  useEffect(() => {
    if (!quickPreview) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeQuickPreview();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [quickPreview]);
  useEffect(() => {
    let reconcileFrame = 0;
    const clearRelation = () => setHoveredRelationKey("");
    const relationAt = (x: number, y: number) => {
      const hit = document.elementFromPoint(x, y);
      const node = hit?.closest<HTMLElement>("[data-node-key]") || null;
      const viewport = executionCanvas.current;
      setHoveredRelationKey(viewport && node && viewport.contains(node) ? node.dataset.nodeKey || "" : "");
    };
    const trackPointer = (event: PointerEvent) => {
      lastPointerPosition.current = { x: event.clientX, y: event.clientY };
      relationAt(event.clientX, event.clientY);
    };
    const clearOutsideWindow = (event: PointerEvent) => {
      if (event.relatedTarget) return;
      lastPointerPosition.current = null;
      clearRelation();
    };
    const reconcileAfterScroll = () => {
      clearRelation();
      window.cancelAnimationFrame(reconcileFrame);
      reconcileFrame = window.requestAnimationFrame(() => {
        const point = lastPointerPosition.current;
        if (point) relationAt(point.x, point.y);
      });
    };
    const clearHiddenWindow = () => {
      if (document.hidden) clearRelation();
    };
    window.addEventListener("pointermove", trackPointer, true);
    window.addEventListener("pointerout", clearOutsideWindow, true);
    window.addEventListener("scroll", reconcileAfterScroll, true);
    window.addEventListener("blur", clearRelation);
    document.addEventListener("visibilitychange", clearHiddenWindow);
    return () => {
      window.cancelAnimationFrame(reconcileFrame);
      window.removeEventListener("pointermove", trackPointer, true);
      window.removeEventListener("pointerout", clearOutsideWindow, true);
      window.removeEventListener("scroll", reconcileAfterScroll, true);
      window.removeEventListener("blur", clearRelation);
      document.removeEventListener("visibilitychange", clearHiddenWindow);
    };
  }, []);
  useEffect(() => {
    if (!focusNodeKey || !executionCanvas.current) return;
    const target = executionCanvas.current.querySelector<HTMLElement>(`[data-node-key="${CSS.escape(focusNodeKey)}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [focusNodeKey]);
  useLayoutEffect(() => {
    const flow = canvasFlow.current;
    if (!flow || !liveNodes.length) {
      setDependencyGraph((current) => current.width || current.height || current.paths.length ?
        { width: 0, height: 0, paths: [] } : current);
      return;
    }
    const measure = () => {
      const box = flow.getBoundingClientRect();
      const paths: Array<{ key: string; sourceKey: string; targetKey: string; d: string; active: boolean }> = [];
      for (const target of liveNodes) {
        const targetElement = flow.querySelector<HTMLElement>(
          `[data-node-key="${CSS.escape(target.key)}"]`);
        if (!targetElement) continue;
        const targetBox = targetElement.getBoundingClientRect();
        for (const dependency of target.dependencies) {
          const sourceElement = flow.querySelector<HTMLElement>(
            `[data-node-key="${CSS.escape(dependency)}"]`);
          if (!sourceElement) continue;
          const sourceBox = sourceElement.getBoundingClientRect();
          const sameColumn = Math.abs(sourceBox.left - targetBox.left) < 20;
          const forward = targetBox.left > sourceBox.left + 20;
          const sx = (sameColumn || forward ? sourceBox.right : sourceBox.left) - box.left;
          const tx = (sameColumn ? targetBox.right : forward ? targetBox.left : targetBox.right) - box.left;
          const sy = sourceBox.top - box.top + sourceBox.height / 2;
          const ty = targetBox.top - box.top + targetBox.height / 2;
          const bend = sameColumn ? 28 : Math.max(24, Math.abs(tx - sx) * .42);
          paths.push({ key: `${dependency}->${target.key}`, sourceKey: dependency, targetKey: target.key,
            d: sameColumn
              ? `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx + bend} ${ty}, ${tx} ${ty}`
              : `M ${sx} ${sy} C ${sx + (forward ? bend : -bend)} ${sy}, ${tx - (forward ? bend : -bend)} ${ty}, ${tx} ${ty}`,
            active: true });
        }
      }
      setDependencyGraph({ width: flow.scrollWidth, height: flow.scrollHeight, paths });
    };
    const frame = window.requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(flow);
    return () => { window.cancelAnimationFrame(frame); observer.disconnect(); };
  }, [liveNodes]);
  const canStart = Boolean(config.project_path && config.software_name.trim() && config.version.trim() &&
    config.screenshot_folder && config.manual_model_id && config.diagram_model_id &&
    config.vision_model_id && config.sensitive_confirmed && config.auto_adopt_confirmed &&
    !busy && !["queued", "running"].includes(run?.status || ""));

  async function pickProject(kind: "directory" | "zip") {
    const value = await open(kind === "directory" ? { directory: true, multiple: false,
      title: "选择软件项目目录" } : { directory: false, multiple: false,
      title: "选择项目 ZIP", filters: [{ name: "ZIP 项目", extensions: ["zip"] }] });
    if (typeof value === "string") setConfig((item) => ({ ...item, project_path: value,
      software_name: item.software_name || baseName(value) }));
  }

  async function pickScreenshots() {
    const value = await open({ directory: true, multiple: false, title: "选择真实界面截图文件夹" });
    if (typeof value === "string") setConfig((item) => ({ ...item, screenshot_folder: value }));
  }

  async function start() {
    setBusy(true); setNotice("正在创建可恢复的后台任务…");
    try {
      const active = connection ?? await ensureConnection();
      const value = await createQuickStartRun(active, config);
      setRun(value); setHistory((items) => [value, ...items.filter((item) => item.id !== value.id)]);
      if (value.task_id) onTaskChange(value.task_id);
      setNotice("已接管流程。现在可以切换页面，后台任务不会中断。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "快速任务启动失败"); }
    finally { setBusy(false); }
  }

  async function retry() {
    if (!connection || !run) return;
    setBusy(true); setNotice("从失败阶段恢复，已完成阶段不会重跑…");
    try { setRun(await retryQuickStartRun(connection, run.id)); }
    catch (error) { setNotice(error instanceof Error ? error.message : "恢复失败"); }
    finally { setBusy(false); }
  }

  async function clearAndRestart() {
    if (!connection || !run) return;
    if (!window.confirm("清空本次快速流程并开始新任务？项目、截图和已生成文档会继续保留在“我的资产”。")) return;
    setBusy(true); setNotice("正在清空本次流程记录；已有项目与文档资产会保留…");
    try {
      await discardQuickStartRun(connection, run.id);
      const settings = await loadAppSettings(connection);
      setHistory((items) => items.filter((item) => item.id !== run.id));
      setRun(null);
      setConfig(withDefaults({ ...defaultConfig }, models, settings));
      setNotice("已清空本次快速流程。项目、截图和文档仍保留在“我的资产”，可以重新开始。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "清空流程失败"); }
    finally { setBusy(false); }
  }

  function navigateTo(page: Parameters<Props["onNavigate"]>[0]) {
    if (run?.task_id) onTaskChange(run.task_id);
    onNavigate(page);
  }

  function openStage(key: string) {
    navigateTo(stageMeta(key).page);
  }

  async function openNodeArtifact(node: ExecutionNode) {
    if (!connection || !run?.manual_job || !["research", "profile", "section", "figure", "screenshot",
      "assemble", "qa"].includes(node.kind)) {
      navigateTo(nodeArtifactPage(node)); return;
    }
    setPreviewBusyNodeKey(node.key);
    try {
      if (["research", "profile"].includes(node.kind)) {
        const workspace = run.task_id ? await loadScreenshotEvidenceWorkspace(connection, run.task_id)
          .catch(() => null) : null;
        const profile = workspace?.profile?.profile || {};
        setQuickPreview({ kind: "summary", node, sections: buildNodeSummary(node, profile) });
      } else if (node.kind === "section") {
        const sections = await listFormalManualSections(connection, run.manual_job.id);
        const sectionKey = node.key === "ui_section_update"
          ? "ui_operations" : node.key.replace(/^section:/, "");
        const section = sections.find((item) => item.section_key === sectionKey);
        if (!section) throw new Error("本章尚无可浏览正文");
        setQuickPreview({ kind: "section", node, section });
      } else if (node.kind === "figure") {
        const figureKey = node.key.replace(/^figure:/, "");
        const url = await loadFormalFigureAsset(connection, run.manual_job.id, figureKey, "png");
        setQuickPreview({ kind: "figure", node, url });
      } else if (node.kind === "screenshot") {
        let items: QuickScreenshotItem[] = [];
        if (run.task_id) {
          const workspace = await loadScreenshotEvidenceWorkspace(connection, run.task_id)
            .catch(() => null);
          items = (workspace?.assets || []).filter((item) => !item.archived).map(projectScreenshotItem);
        }
        if (!items.length) {
          items = (await listFormalScreenshots(connection, run.manual_job.id, false)
            .catch(() => [])).map(formalScreenshotItem);
        }
        if (!items.length) {
          setQuickPreview({ kind: "summary", node, sections: buildNodeSummary(node, {}) });
        } else {
          const url = await loadQuickScreenshot(connection, run, run.manual_job.id, items[0]);
          setQuickPreview({ kind: "screenshots", node, items, index: 0, url });
        }
      } else {
        const documents = await listFormalManualDocuments(connection, run.manual_job.id);
        const document = resolveNodeDocument(node, run, liveNodes, documents);
        if (!document) throw new Error(node.kind === "qa" ? "终稿尚未形成" : "审阅稿尚未形成");
        try {
          const qa = await loadFormalManualQa(connection, document.job_id, document.version);
          const url = await loadFormalManualQaPage(connection, document.job_id, document.version, 1);
          setQuickPreview({ kind: "document", node, document, qaPageCount: qa.page_count,
            page: 1, url, structured: null });
        } catch {
          const structured = await loadFormalManualPreview(
            connection, document.job_id, document.version);
          setQuickPreview({ kind: "document", node, document, qaPageCount: 0,
            page: 1, url: null, structured });
        }
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "产物预览读取失败");
    } finally { setPreviewBusyNodeKey(""); }
  }

  function closeQuickPreview() {
    setQuickPreview(null);
  }

  async function changeQuickPreviewPage(page: number) {
    if (!connection || quickPreview?.kind !== "document" || !quickPreview.qaPageCount ||
      page < 1 || page > quickPreview.qaPageCount || previewPageBusy) return;
    setPreviewPageBusy(true);
    try {
      const url = await loadFormalManualQaPage(connection, quickPreview.document.job_id,
        quickPreview.document.version, page);
      setQuickPreview((current) => current?.kind === "document"
        ? { ...current, page, url } : current);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "文档预览页读取失败");
    } finally { setPreviewPageBusy(false); }
  }

  async function changeQuickScreenshot(index: number) {
    if (!connection || !run || quickPreview?.kind !== "screenshots" || previewPageBusy ||
      index < 0 || index >= quickPreview.items.length) return;
    setPreviewPageBusy(true);
    try {
      const url = await loadQuickScreenshot(connection, run, run.manual_job?.id || "",
        quickPreview.items[index]);
      setQuickPreview((current) => current?.kind === "screenshots"
        ? { ...current, index, url } : current);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "截图预览读取失败");
    } finally { setPreviewPageBusy(false); }
  }

  function stageCard(key: string) {
    if (!run) return null;
    const stage = run.stages.find((item) => item.key === key);
    if (!stage) return null;
    const index = run.stages.findIndex((item) => item.key === key);
    const meta = stageMeta(key);
    return <article className={`quick-pipeline-stage ${stage.status}`} key={stage.key}>
      <span>{stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" :
        String(index + 1).padStart(2, "0")}</span>
      <div><strong>{stage.title}</strong><small>{stage.status === "running" ? stage.description : stage.message}</small>
        {stage.attempt > 1 && <em>节点重试 {stage.attempt}/{run.config.retry_limit + 1}</em>}</div>
      <button className="stage-help" data-help={meta.help}
        aria-label={`了解${stage.title}并查看详情`} onClick={() => openStage(key)}>?</button>
    </article>;
  }

  function manualWorkCard(kind: "research" | "sections" | "figures") {
    if (!run) return null;
    const manualStage = run.stages.find((item) => item.key === "manual");
    const specification = {
      research: { badge: "研", title: "项目证据研究", page: "manual" as QuickPage,
        help: "先研究源码证据并形成项目画像，为截图解读、正文撰写与专业图表提供统一事实基础。",
        kinds: ["research", "profile"] },
      sections: { badge: "文", title: "撰写说明书正文", page: "manual" as QuickPage,
        help: "按项目证据分章撰写正文；各章可独立并发，完成后参与说明书装配。",
        kinds: ["section"] },
      figures: { badge: "图", title: "生成专业图表", page: "diagrams" as QuickPage,
        help: "接收正文中的画图需求，并行生成可编辑 Draw.io、SVG 与 PNG 预览。",
        kinds: ["figure"] },
    }[kind];
    const nodes = liveNodes.filter((node) => specification.kinds.includes(node.kind));
    const status = manualWorkStatus(nodes, manualStage?.status || "pending");
    const detail = manualWorkDetail(kind, nodes, status);
    return <article className={`quick-pipeline-stage manual-work-stage ${status}`}>
      <span>{status === "completed" ? "✓" : status === "failed" ? "!" : specification.badge}</span>
      <div><strong>{specification.title}</strong><small>{detail}</small></div>
      <button className="stage-help" data-help={specification.help}
        aria-label={`了解${specification.title}并查看详情`}
        onClick={() => navigateTo(specification.page)}>?</button>
    </article>;
  }

  return <main className="workspace quick-start-page">
    <section className="quick-hero">
      <div><small>UNATTENDED PIPELINE</small><h1>快速开始</h1>
        <p>一次确认输入，自动完成源码材料、截图解读、专业图表、软件说明书与双文档交付。</p></div>
      <div className={`quick-orb ${run?.status || "idle"}`}><i /><span>{run ? `${progress}%` : "AUTO"}</span></div>
    </section>

    {!run || !["queued", "running"].includes(run.status) ? <section className="quick-config-card">
      <header><div><b>01</b><span><strong>把人工判断前置</strong><small>以下信息确认后，流程将不再逐步打断你。</small></span></div>
        <em>预计产物 · 代码文档 + 软件说明书</em></header>
      <div className="quick-form-grid">
        <label className="wide"><span>项目源码</span><div className="path-picker"><input value={config.project_path}
          readOnly placeholder="选择项目目录或 ZIP"/><button onClick={() => pickProject("directory")}>选择目录</button>
          <button onClick={() => pickProject("zip")}>选择 ZIP</button></div></label>
        <label><span>软件名称</span><input value={config.software_name} placeholder="例如：霓裳云枢"
          onChange={(event) => setConfig({ ...config, software_name: event.target.value })}/></label>
        <label><span>版本号</span><input value={config.version} placeholder="V1.0"
          onChange={(event) => setConfig({ ...config, version: event.target.value || "V1.0" })}/></label>
        <label className="wide"><span>真实界面截图文件夹</span><div className="path-picker"><input
          value={config.screenshot_folder} readOnly placeholder="只导入此文件夹中的 PNG / JPG / WebP"/>
          <button onClick={pickScreenshots}>选择文件夹</button></div></label>
        <ModelField title="正文模型" value={config.manual_model_id} models={models}
          onChange={(value) => setConfig({ ...config, manual_model_id: value })}/>
        <ModelField title="画图模型" value={config.diagram_model_id} models={models}
          onChange={(value) => setConfig({ ...config, diagram_model_id: value })}/>
        <ModelField title="图片解读模型（已实测）" value={config.vision_model_id} models={visionModels}
          empty="没有已验证图片能力的模型" onChange={(value) => setConfig({ ...config, vision_model_id: value })}/>
        <label><span>并发数</span><input type="number" min={1} max={10} value={config.concurrency}
          onChange={(event) => setConfig({ ...config, concurrency: Number(event.target.value) })}/></label>
        <label><span>失败自动重试</span><select value={config.retry_limit}
          onChange={(event) => setConfig({ ...config, retry_limit: Number(event.target.value) })}>
          {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value} 次</option>)}</select></label>
        <label><span>源码抽取策略</span><select value={config.source_strategy}
          onChange={(event) => setConfig({ ...config, source_strategy: event.target.value as QuickStartConfig["source_strategy"] })}>
          <option value="standard">标准 · 推荐</option><option value="relaxed">宽松</option><option value="maximum">最大覆盖</option>
        </select></label>
      </div>
      <div className="quick-consents">
        <label><input type="checkbox" checked={config.sensitive_confirmed} onChange={(event) =>
          setConfig({ ...config, sensitive_confirmed: event.target.checked })}/><span><b>截图安全确认</b>
          所选文件夹不含账号、密钥、身份证件等需遮挡内容。</span></label>
        <label><input type="checkbox" checked={config.auto_adopt_confirmed} onChange={(event) =>
          setConfig({ ...config, auto_adopt_confirmed: event.target.checked })}/><span><b>授权自动采用</b>
          解读成功后自动审核采用；原图、模型结果和版本历史仍完整留存。</span></label>
        <label><input type="checkbox" checked={config.finalize_with_warnings} onChange={(event) =>
          setConfig({ ...config, finalize_with_warnings: event.target.checked })}/><span><b>允许带非阻断警告定稿</b>
          阻断问题仍会停止；普通提示不会让流程卡死。</span></label>
      </div>
      <footer><span>{notice}</span><div>{visionModels.length === 0 && <button className="ghost" onClick={onOpenSettings}>去验证图片模型</button>}
        <button className="quick-launch" disabled={!canStart} onClick={start}>{busy ? "正在启动…" : "启动无人值守生成"}</button></div></footer>
    </section> : <section className="quick-run-summary"><div><i className="live-dot"/><span><strong>{run.config.software_name} · {run.config.version}</strong>
      <small>后台持续执行 · 可放心切换页面或关闭当前工作区</small></span></div><b>{progress}%</b></section>}

    {run && <section className={`quick-flow-board ${run.status}`}>
      <header><div><small>LIVE ORCHESTRATION</small><h2>{run.status === "completed" ? "双文档交付完成" :
        run.status === "failed" ? "流程已停在可恢复节点" : "自动化流水线正在运行"}</h2><p>{notice}</p></div>
        <div className="quick-progress-ring" style={{ "--progress": `${progress * 3.6}deg` } as React.CSSProperties}><span>{progress}%</span></div></header>
      <div className="quick-stage-track quick-parallel-pipeline">
        <section className="pipeline-terminal pipeline-entry" aria-label="共同准备阶段">
          <header><small>COMMON INPUT</small><strong>共同准备</strong></header>
          <div>{stageCard("scan")}<i className="pipeline-arrow" />{stageCard("confirm")}</div>
        </section>
        <div className="pipeline-branches" aria-label="源码文档与软件说明书并行执行">
          <section className="pipeline-branch source-branch"><header><span>A</span><div><strong>源码文档线</strong><small>选材、分页预检与 Word 装配</small></div></header>
            <div>{stageCard("source_plan")}<i className="pipeline-arrow" />{stageCard("code_preview")}<i className="pipeline-arrow" />{stageCard("source_docx")}</div></section>
          <section className="pipeline-branch manual-branch"><header><span>B</span><div><strong>软件说明书线</strong><small>先研究项目证据，再处理截图，正文与专业图表并行生成</small></div></header>
            <div className="manual-branch-flow">
              {manualWorkCard("research")}<i className="pipeline-arrow" />
              {stageCard("screenshots")}<i className="pipeline-arrow" />
              <div className="manual-generation-cluster" aria-label="正文与专业图表并行生成">
                <small className="manual-cluster-label">并行生成</small>
                <div className="manual-parallel-work">{manualWorkCard("sections")}{manualWorkCard("figures")}</div>
              </div>
            </div></section>
        </div>
        <section className="pipeline-terminal pipeline-exit" aria-label="汇合交付阶段">
          <header><small>MERGE &amp; DELIVERY</small><strong>汇合交付</strong></header>
          <div>{stageCard("finalize")}<i className="pipeline-arrow" />{stageCard("delivery")}</div>
        </section>
      </div>
      <div className="quick-telemetry">
        <article><small>持续时间</small><strong>{elapsed}</strong><span>后台心跳每 1.4 秒刷新</span></article>
        <article><small>并行执行</small><strong>{runningNodes.length} / {run.config.concurrency}</strong><span>当前活跃 / 并发上限</span></article>
        <article><small>节点兑现</small><strong>{settledNodes.length} / {liveNodes.length || "—"}</strong><span>每个节点独立留痕与恢复</span></article>
        <article><small>截图证据</small><strong>{screenshotMetric(run)}</strong><span>绑定稳定项目画像，命中直接复用</span></article>
      </div>
      {run.manual_job?.nodes?.length ? <div className="quick-command-center">
        <section className="quick-execution-canvas"><header><div><strong>执行流程画布</strong><small>悬浮任一节点查看一跳上下游关系 · 自动追踪当前主要任务</small></div>
          <span>{run.manual_job.progress.completed}/{run.manual_job.progress.total} 个实节点</span>
          <button onClick={() => executionCanvas.current?.querySelector<HTMLElement>("[data-focus='true']")?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" })}>定位当前任务</button></header>
          <div className="quick-canvas-viewport" ref={executionCanvas}
            onScroll={() => setHoveredRelationKey("")}
            onPointerLeave={() => { lastPointerPosition.current = null; setHoveredRelationKey(""); }}>
            <div className="quick-canvas-flow" ref={canvasFlow}>
            <svg className="quick-dependency-layer" width={dependencyGraph.width} height={dependencyGraph.height}
              viewBox={`0 0 ${dependencyGraph.width} ${dependencyGraph.height}`} aria-hidden="true">
              <defs><marker id="quick-endpoint" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
                <circle cx="4" cy="4" r="2.4" /></marker></defs>
              {activeRelations.map((path) => <path key={path.key} d={path.d}
                className={path.active ? "active" : "pending"} markerEnd="url(#quick-endpoint)" />)}
            </svg>
            {canvasLanes.map((lane, laneIndex) => <section className="quick-canvas-lane" key={lane.key}>
              <header><span>{String(laneIndex + 1).padStart(2, "0")}</span><div><strong>{lane.title}</strong><small>{lane.nodes.length} 个任务节点</small></div></header>
              <div>{lane.nodes.map((node) => <article className={`quick-canvas-node ${node.status} ${
                relatedNodeKeys.has(node.key) ? "relation-linked" : ""} ${node.key === activeRelationKey ? "relation-focus" : ""}`}
                data-node-key={node.key} data-focus={node.key === focusNodeKey ? "true" : "false"} key={node.id}
                >
                <header><i/><strong>{node.title}</strong><em>{statusLabel(node.status)}</em></header>
                {node.kind === "figure" && <div className="node-relation">
                  来源章节 · {figureSourceTitle(node, liveNodes)}</div>}
                <p>{nodeDetail(node)}</p><footer><span>{node.dependencies.length ? `依赖 ${node.dependencies.length} 项` : "流程起点"}</span>
                  <b>{node.attempt > 1 ? `重试 ${node.attempt}/${node.max_attempts}` : node.duration_ms ? durationLabel(node.duration_ms) : "等待执行"}</b></footer>
                {hasNodeArtifact(node) && <button type="button" className="node-artifact"
                  aria-label={`${nodeArtifactLabel(node, Boolean(outputDocumentVersion(run)))}：${node.title}`}
                  disabled={previewBusyNodeKey === node.key}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => { event.stopPropagation(); void openNodeArtifact(node); }}>
                  {previewBusyNodeKey === node.key ? "正在打开…" :
                    nodeArtifactLabel(node, Boolean(outputDocumentVersion(run)))}</button>}
              </article>)}</div>
            </section>)}</div></div>
          <details className="quick-activity-log"><summary><span><strong>运行日志</strong><small>默认收起 · 查看节点事件、复用与重试记录</small></span>
            <em>{recentActivity.length} 条最近记录</em></summary><ol>
            {recentActivity.map((item, index) => <li className={item.status} key={`${item.at}-${index}`}><time>{timeLabel(item.at)}</time>
              <span><b>{item.title}</b><small>{item.message}</small></span></li>)}</ol></details>
        </section>
      </div> : <div className="quick-preflight"><i/><span><strong>正在建立可追溯执行图</strong><small>扫描完成后会展开模型调用、文档装配、截图与图表子节点。</small></span></div>}
      {run.status === "failed" && <div className="quick-recovery"><span><strong>{run.safe_error_message || "当前节点失败"}</strong>
        <small>已完成结果均已保留；可以恢复此流程，也可以只清空流程记录重新开始。</small></span><div>
          <button className="clear-run" disabled={busy} onClick={clearAndRestart}>清空并重新开始</button>
          <button disabled={busy} onClick={retry}>一键恢复并继续</button></div></div>}
      {run.status === "completed" && <div className="quick-delivery"><div><span>DOCX</span><p><strong>源代码文档</strong><small>已生成、分页并完成逐页检查</small></p></div>
        <div><span>DOCX</span><p><strong>软件说明书</strong><small>已装配终稿并保留生成履历</small></p></div>
        <div className="quick-delivery-actions"><button className="clear-run" disabled={busy} onClick={clearAndRestart}>清空流程并开始新任务</button>
          <button onClick={() => { if (run.task_id) onTaskChange(run.task_id); onOpenAssets(); }}>前往我的资产</button></div></div>}
    </section>}

    {history.length > 1 && <details className="quick-history"><summary>历史快速任务 · {history.length} 个</summary><div>
      {history.map((item) => <button key={item.id} className={item.id === run?.id ? "active" : ""} onClick={() => setRun(item)}>
        <span><b>{item.config.software_name}</b><small>{new Date(item.created_at).toLocaleString()}</small></span><em>{statusLabel(item.status)}</em></button>)}</div></details>}

    {quickPreview && <div className="quick-artifact-preview" role="dialog" aria-modal="true"
      onMouseDown={(event) => { if (event.target === event.currentTarget) closeQuickPreview(); }}>
      <section><header><div><strong>{quickPreview.node.title}</strong><small>{quickPreview.kind === "section" ?
        "快速正文预览 · 当前已生成版本" : quickPreview.kind === "figure" ?
          "快速图表预览 · 当前 PNG 效果" : quickPreview.kind === "screenshots" ?
            `快速截图预览 · ${quickPreview.items.length} 张项目证据` : quickPreview.kind === "summary" ?
              "快速产物预览 · 已固化的结构化结果" : `${documentKindLabel(quickPreview.document)} · 文档 v${
                quickPreview.document.version}`}</small></div><div>
        <button onClick={() => { closeQuickPreview(); navigateTo(nodeArtifactPage(quickPreview.node)); }}>
          {quickPreview.kind === "figure" ? "进入图表编辑" : quickPreview.kind === "screenshots" ?
            "进入截图工作台" : quickPreview.kind === "summary" ? "查看完整详情" : "进入说明书详情"}</button>
        <button className="close-preview" onClick={closeQuickPreview}>关闭</button></div></header>
        {quickPreview.kind === "section" ? <article className="quick-section-reader">{
          quickPreview.section.blocks.map((block, index) => <QuickReadOnlyBlock block={block}
            key={`${block.type}-${index}`} />)}</article> : quickPreview.kind === "summary" ?
          <article className="quick-summary-reader">{quickPreview.sections.map((section) => <section key={section.title}>
            <h2>{section.title}</h2><dl>{section.fields.map((field) => <div key={field.label}>
              <dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl></section>)}</article> :
          quickPreview.kind === "screenshots" ? <div className="quick-screenshot-reader">
            <aside>{quickPreview.items.map((item, index) => <button key={`${item.source}-${item.id}`}
              className={index === quickPreview.index ? "active" : ""} disabled={previewPageBusy}
              onClick={() => changeQuickScreenshot(index)}><span>{String(index + 1).padStart(2, "0")}</span>
              <div><strong>{item.title || `截图 ${index + 1}`}</strong><small>{screenshotStatusLabel(item)}</small></div></button>)}</aside>
            <figure><img src={quickPreview.url} alt={quickPreview.items[quickPreview.index]?.title || "界面截图"}/>
              <figcaption><strong>{quickPreview.items[quickPreview.index]?.title}</strong>
                <span>{quickPreview.items[quickPreview.index]?.description || "真实界面截图证据"}</span>
                <small>第 {quickPreview.index + 1} / {quickPreview.items.length} 张</small></figcaption></figure>
          </div> : <figure className="quick-figure-reader">
              {quickPreview.kind === "figure" ? <img src={quickPreview.url} alt={quickPreview.node.title} />
                : quickPreview.url ? <div className="quick-document-reader"><nav>
                  <button disabled={previewPageBusy || quickPreview.page <= 1}
                    onClick={() => changeQuickPreviewPage(quickPreview.page - 1)}>上一页</button>
                  <strong>第 {quickPreview.page} / {quickPreview.qaPageCount} 页</strong>
                  <button disabled={previewPageBusy || quickPreview.page >= quickPreview.qaPageCount}
                    onClick={() => changeQuickPreviewPage(quickPreview.page + 1)}>下一页</button></nav>
                  <img src={quickPreview.url} alt={`${documentKindLabel(quickPreview.document)}第 ${
                    quickPreview.page} 页`} /></div> : <article className="quick-section-reader quick-document-structured">
                  {quickPreview.structured?.sections.map((section) => <section key={section.section_key}>
                    <h2>{section.title}</h2>{section.blocks.map((block, index) =>
                      <QuickReadOnlyBlock block={block} key={`${block.type}-${index}`} />)}</section>)}
                </article>}</figure>}
      </section>
    </div>}
  </main>;
}

function ModelField({ title, value, models, onChange, empty = "没有可用模型" }: { title: string;
  value: string; models: ModelConfig[]; onChange: (value: string) => void; empty?: string }) {
  return <label><span>{title}</span><select value={value} onChange={(event) => onChange(event.target.value)}>
    <option value="">{empty}</option>{models.map((model) => <option value={model.id} key={model.id}>
      {model.name} · {model.model_name}</option>)}</select></label>;
}

function withDefaults(current: QuickStartConfig, models: ModelConfig[], settings: AppSettings) {
  const enabled = models.filter((item) => item.enabled);
  const vision = enabled.find((item) => item.vision_verified && item.supports_vision === true);
  return { ...current, manual_model_id: current.manual_model_id || settings.manual_model_id || enabled[0]?.id || "",
    diagram_model_id: current.diagram_model_id || settings.diagram_model_id || enabled[0]?.id || "",
    vision_model_id: current.vision_model_id || settings.vision_model_id || vision?.id || "", source_strategy: settings.source_strategy,
    concurrency: settings.generation_concurrency };
}

function baseName(path: string) { return path.split(/[\\/]/).pop()?.replace(/\.zip$/i, "") || ""; }
function statusLabel(status: string) { return ({ queued: "排队中", running: "运行中", completed: "已完成",
  failed: "待恢复", pending: "等待", completed_with_warnings: "有警告" } as Record<string, string>)[status] || status; }

type ExecutionNode = NonNullable<QuickStartRun["manual_job"]>["nodes"][number];
type QuickArtifactPreview = { kind: "section"; node: ExecutionNode; section: {
  section_key: string; title: string; ordinal: number; status: string; blocks: ManualSectionBlock[];
} } | { kind: "figure"; node: ExecutionNode; url: string } |
  { kind: "summary"; node: ExecutionNode; sections: QuickSummarySection[] } |
  { kind: "screenshots"; node: ExecutionNode; items: QuickScreenshotItem[]; index: number; url: string } |
  { kind: "document"; node: ExecutionNode; document: FormalManualDocument;
    qaPageCount: number; page: number; url: string | null; structured: FormalManualPreview | null };
type QuickSummarySection = { title: string; fields: Array<{ label: string; value: string }> };
type QuickScreenshotItem = { id: string; source: "project" | "formal"; title: string;
  description: string; reviewStatus: string; adoptionStatus: string; asset?: ProjectScreenshotAsset;
  formal?: FormalManualScreenshot };
type QuickPage = Parameters<Props["onNavigate"]>[0];
const STAGE_META: Record<string, { page: QuickPage; help: string }> = {
  scan: { page: "overview", help: "扫描项目目录、过滤依赖并建立可追溯的源码证据索引。点击查看项目概览。" },
  confirm: { page: "overview", help: "固化软件名称、版本及必要项目事实，作为两条文档线的共同输入。" },
  source_plan: { page: "source", help: "按源码策略选择代表性业务代码，排除依赖、测试和敏感内容。" },
  code_preview: { page: "source", help: "进行确定性分页预检，核对代码覆盖与 60 页版式。" },
  source_docx: { page: "source", help: "装配源代码 Word 并逐页渲染检查。点击进入源码材料查看产物。" },
  screenshots: { page: "screenshots", help: "导入真实截图，调用已验证视觉模型解读并按授权自动采用。" },
  manual: { page: "manual", help: "研究证据、并发撰写章节，并把各章画图需求派发到图表节点。" },
  finalize: { page: "manual", help: "等待两条分支完成后装配文档、执行逐页质检并生成人工确认的终稿。" },
  delivery: { page: "assets", help: "双文档与图表、截图资产已归档，可在我的资产中查看和导出。" },
};
function stageMeta(key: string) {
  return STAGE_META[key] || { page: "quick" as QuickPage, help: "查看当前自动化阶段的执行详情。" };
}
function manualWorkStatus(nodes: ExecutionNode[], fallback: QuickStartStage["status"]) {
  const completed = new Set(["completed", "completed_with_warnings", "skipped", "adopted"]);
  if (nodes.some((node) => node.status === "failed")) return "failed";
  if (nodes.some((node) => node.status === "running")) return "running";
  if (nodes.length && nodes.every((node) => completed.has(node.status))) return "completed";
  if (nodes.some((node) => completed.has(node.status)) && fallback === "running") return "running";
  if (!nodes.length && fallback === "completed") return "completed";
  return "pending";
}
function manualWorkDetail(kind: "research" | "sections" | "figures", nodes: ExecutionNode[], status: string) {
  const completed = nodes.filter((node) => ["completed", "completed_with_warnings", "skipped", "adopted"]
    .includes(node.status)).length;
  const running = nodes.find((node) => node.status === "running");
  if (running) return `正在处理 · ${running.title}`;
  if (status === "failed") return nodes.find((node) => node.status === "failed")?.safe_error_message || "存在待恢复节点";
  if (nodes.length) return status === "completed" ? `${nodes.length} 个实节点已完成` : `${completed}/${nodes.length} 个实节点完成`;
  return kind === "research" ? "等待建立项目画像与证据索引" : kind === "sections" ? "等待项目研究完成后分章并发" : "等待正文派发画图需求";
}
function readableKey(key: string) {
  return ({ version: "版本", origin: "来源", fingerprint: "证据指纹", next_action: "下游用途",
    source_count: "证据来源", fact_count: "已固化事实", note_count: "研究记录",
    warning_count: "待核信息", screenshot_count: "截图数量", adopted: "已采用截图",
    reused: "复用截图", imported: "导入截图" } as Record<string, string>)[key] ||
    key.replaceAll("_", " ");
}
function readableValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map(readableValue).join("；") : "—";
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${readableKey(key)}：${readableValue(item)}`).join("；");
  return String(value);
}
function summaryFields(value: Record<string, unknown>) {
  return Object.entries(value).filter(([key]) => !["next_action", "profile"].includes(key))
    .map(([key, item]) => ({ label: readableKey(key), value: readableValue(item) }));
}
function buildNodeSummary(node: ExecutionNode, profile: Record<string, unknown>): QuickSummarySection[] {
  const result: QuickSummarySection[] = [];
  const output = summaryFields(node.output || {});
  if (output.length) result.push({ title: "本节点产物", fields: output });
  const profileFields = summaryFields(profile);
  if (profileFields.length) result.push({ title: "截图理解项目概要", fields: profileFields });
  result.push({ title: "执行留痕", fields: [
    { label: "状态", value: statusLabel(node.status) },
    { label: "执行耗时", value: node.duration_ms ? durationLabel(node.duration_ms) : "未记录" },
    { label: "执行次数", value: `${node.attempt} / ${node.max_attempts}` },
    { label: "结果用途", value: String(node.output.next_action || nodeDetail(node)) },
  ] });
  return result;
}
function projectScreenshotItem(asset: ProjectScreenshotAsset): QuickScreenshotItem {
  return { id: asset.id, source: "project", title: asset.interpretation?.page_title || asset.title,
    description: asset.interpretation?.suggested_caption || asset.interpretation?.purpose || "真实界面截图证据",
    reviewStatus: asset.review_status, adoptionStatus: asset.adoption_status, asset };
}
function formalScreenshotItem(asset: FormalManualScreenshot): QuickScreenshotItem {
  return { id: asset.screenshot_key, source: "formal", title: asset.title,
    description: asset.description.page_purpose || "已装配的真实界面截图",
    reviewStatus: "reviewed", adoptionStatus: "adopted", formal: asset };
}
async function loadQuickScreenshot(connection: SidecarConnection, run: QuickStartRun, jobId: string,
  item: QuickScreenshotItem) {
  if (item.source === "project" && run.task_id)
    return loadScreenshotEvidenceImage(connection, run.task_id, item.id);
  return loadFormalScreenshotImage(connection, jobId, item.id);
}
function screenshotStatusLabel(item: QuickScreenshotItem) {
  const review = item.reviewStatus === "reviewed" ? "已审核" : item.reviewStatus === "rejected" ? "已排除" : "待审核";
  const adoption = item.adoptionStatus === "adopted" ? "已采用" : item.adoptionStatus === "excluded" ? "未采用" : "待采用";
  return `${review} · ${adoption}`;
}
function nodeArtifactPage(node: ExecutionNode): QuickPage {
  if (node.kind === "figure") return "diagrams";
  if (node.kind === "screenshot") return "screenshots";
  if (["section", "assemble", "qa", "research", "profile"].includes(node.kind)) return "manual";
  return "manual";
}
function nodeArtifactLabel(node: ExecutionNode, finalReady = false) {
  if (node.kind === "figure") return "查看图表";
  if (node.kind === "screenshot") return "查看截图";
  if (["research", "profile"].includes(node.kind)) return "查看详情";
  if (node.kind === "section") return "查看正文";
  if (node.key === "review_checkpoint") return "查看正文快照";
  if (node.kind === "assemble") return "查看审阅稿";
  if (node.kind === "qa") return finalReady ? "查看终稿" : "查看审阅稿";
  return "查看产物";
}
function outputDocumentVersion(run: QuickStartRun) {
  const output = run.outputs.manual_document;
  return output && typeof output === "object" && "version" in output
    ? Number((output as { version?: unknown }).version || 0) : 0;
}
function resolveNodeDocument(node: ExecutionNode, run: QuickStartRun, nodes: ExecutionNode[],
  documents: FormalManualDocument[]) {
  let version = node.kind === "qa" ? outputDocumentVersion(run) : 0;
  if (!version) version = Number(node.output.version || 0);
  if (!version && node.kind === "qa") {
    const assembly = node.dependencies.map((key) => nodes.find((item) => item.key === key))
      .find((item) => item?.kind === "assemble");
    version = Number(assembly?.output.version || 0);
  }
  const exact = version ? documents.find((item) => item.version === version) : undefined;
  if (exact) return exact;
  if (node.key === "review_checkpoint")
    return documents.find((item) => item.document_kind === "review_checkpoint");
  if (node.kind === "qa") return documents.find((item) => item.document_kind === "final_document") ||
    documents.find((item) => item.document_kind === "formal_candidate");
  return documents.find((item) => item.document_kind === "formal_candidate") || documents[0];
}
function documentKindLabel(document: FormalManualDocument) {
  return ({ review_checkpoint: "正文预览快照", formal_candidate: "审阅稿",
    final_document: "终稿" } as Record<string, string>)[document.document_kind] || "文档预览";
}
function figureSourceTitle(node: ExecutionNode, nodes: ExecutionNode[]) {
  const source = node.dependencies.map((key) => nodes.find((item) => item.key === key))
    .find((item) => item?.kind === "section");
  return source?.title || "未绑定章节";
}
function hasNodeArtifact(node: ExecutionNode) {
  return ["completed", "completed_with_warnings"].includes(node.status) &&
    ["research", "profile", "section", "figure", "screenshot", "assemble", "qa"].includes(node.kind);
}
function QuickReadOnlyBlock({ block }: { block: ManualSectionBlock }) {
  if (block.type === "subheading") return <h3>{block.title}</h3>;
  if (block.type === "paragraph") return <p>{block.text}</p>;
  if (block.type === "list") return <div>{block.lead && <p>{block.lead}</p>}
    <ul>{block.items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
  if (block.type === "figure_request") return <aside><strong>关联图表 · {block.title}</strong>
    <p>{block.purpose}</p></aside>;
  return <figure><figcaption>{block.title}</figcaption><table><thead><tr>{block.headers.map(
    (header, index) => <th key={index}>{header}</th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) =>
    <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></figure>;
}
function buildCanvasLanes(nodes: ExecutionNode[]) {
  const stages = [
    ["research", "项目证据研究"], ["draft", "分章撰写"], ["diagrams", "专业图表"],
    ["screenshots", "截图证据"], ["assembly", "文档装配"], ["qa", "逐页质检与交付"],
  ];
  const known = new Set(stages.map(([key]) => key));
  const lanes = stages.map(([key, title]) => ({ key, title,
    nodes: nodes.filter((node) => node.stage_key === key).sort((a, b) => a.created_at.localeCompare(b.created_at)) }));
  const other = nodes.filter((node) => !known.has(node.stage_key));
  if (other.length) lanes.push({ key: "other", title: "其他协作任务", nodes: other });
  return lanes.filter((lane) => lane.nodes.length);
}
function nodeDetail(node: NonNullable<QuickStartRun["manual_job"]>["nodes"][number]) {
  if (node.safe_error_message) return node.safe_error_message;
  if (node.status === "completed" || node.status === "completed_with_warnings")
    return String(node.output.next_action || "结果已固化，可供下游直接复用");
  const details: Record<string, string> = { research: "正在建立源码证据图谱与项目画像", profile: "正在冻结截图理解所需的项目上下文",
    section: "模型正在按章节读取证据并生成结构化正文", figure: "语义设计、Draw.io 渲染与本地结构校验中",
    screenshot: "正在核对截图授权、采用状态与正文引用", assemble: "正在组合正文、图表、截图、目录与页眉页脚",
    qa: "正在逐页渲染并检查内容密度、编号与证据一致性" };
  return details[node.kind] || (node.status === "queued" ? "等待依赖节点释放" : "模型或本地工具正在处理");
}
function buildActivity(run: QuickStartRun | null) {
  if (!run) return [];
  const stageEvents = run.stages.flatMap((stage) => (stage.events || []).map((event) => ({
    at: event.at, status: event.status, title: stage.title, message: event.message,
  })));
  const nodeEvents = (run.manual_job?.nodes || []).filter((node) => node.updated_at).map((node) => ({
    at: node.updated_at, status: node.status, title: node.title, message: nodeDetail(node),
  }));
  return [...stageEvents, ...nodeEvents].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 12);
}
function screenshotMetric(run: QuickStartRun) {
  const output = run.stages.find((stage) => stage.key === "screenshots")?.output || {};
  const adopted = Number(output.adopted || 0), reused = Number(output.reused || 0), imported = Number(output.imported || 0);
  if (reused) return `${reused} 张复用`;
  if (adopted) return `${adopted} 张采用`;
  if (imported) return `${imported} 张导入`;
  return run.current_stage === "screenshots" ? "正在核验" : "等待处理";
}
function durationLabel(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return `${minutes} 分 ${rest} 秒`;
}
function timeLabel(value: string) { return new Date(value).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
