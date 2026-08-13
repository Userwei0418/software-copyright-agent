import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import {
  assembleFormalManualDocument, editFormalManualSection, exportManualDocument,
  deferFormalManualQaCheck,
  finalizeFormalManualDocument,
  FormalManualDocument, FormalManualJob, FormalManualPreview, FormalManualQa, ManualExportResult,
  generateFormalManual, generateFormalManualFigures, listFormalManualDocuments,
  listFormalManualJobs, listFormalManualSections, listModelConfigs, loadAppSettings, loadFormalManualPreview,
  loadFormalManualQa, loadFormalManualQaPage, ManualSectionBlock, ModelConfig,
  regenerateFormalManualFigure, regenerateFormalManualSection, revealExportedDocument,
  retryScreenshotAnalysisNode, runFormalManualQa, SidecarConnection,
} from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

export function ManualWorkspace({ connection, taskId, onTaskChange, onOpenDiagrams,
  onOpenScreenshots, trackedJob }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (taskId: string) => void;
  trackedJob: FormalManualJob | null;
  onOpenDiagrams: () => void; onOpenScreenshots: () => void;
}) {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [modelId, setModelId] = useState("");
  const [jobs, setJobs] = useState<FormalManualJob[]>([]);
  const [documents, setDocuments] = useState<FormalManualDocument[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<FormalManualDocument | null>(null);
  const [quality, setQuality] = useState<FormalManualQa | null>(null);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewPageUrl, setPreviewPageUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState("");
  const [finalization, setFinalization] = useState<{
    state: "working" | "success" | "error"; message: string;
  } | null>(null);
  const [exportState, setExportState] = useState<{
    state: "choosing" | "working" | "success" | "error" | "canceled"; message: string;
  } | null>(null);
  const [qualityAction, setQualityAction] = useState<{
    checkKey: string; kind: "repair" | "defer"; state: "working" | "success" | "error" | "info"; message: string;
  } | null>(null);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const [exportReceipt, setExportReceipt] = useState<ManualExportResult | null>(null);
  const [editor, setEditor] = useState<FormalManualPreview | null>(null);
  const [sectionViewer, setSectionViewer] = useState<FormalManualPreview["sections"][number] | null>(null);
  const [activeSectionKey, setActiveSectionKey] = useState("");
  const [editorBusy, setEditorBusy] = useState(false);
  const [figuresDirty, setFiguresDirty] = useState(false);
  const [dirtySections, setDirtySections] = useState<string[]>([]);
  const [showAllDocuments, setShowAllDocuments] = useState(false);
  const [busyNodeKey, setBusyNodeKey] = useState("");
  const [clock, setClock] = useState(Date.now());
  const activeJobs = jobs.filter((item) => item.status === "queued" || item.status === "running");
  const activeJob = activeJobs[0] || null;
  const visibleJob = activeJob || jobs[0] || (trackedJob?.task_id === taskId ? trackedJob : null);
  const activeJobId = activeJob?.id || "";
  const activeModel = visibleJob
    ? models.find((item) => item.id === visibleJob.model_config_id) : null;
  const cockpitJob = activeJob || visibleJob;
  const terminalIssues = !activeJob && visibleJob ? visibleJob.nodes.filter((node) =>
    ["failed", "completed_with_warnings", "waiting_for_authorization", "waiting_for_review",
      "waiting_for_screenshots", "outdated"].includes(node.status) ||
    (node.kind === "screenshot" && node.status === "skipped")) : [];
  const isCheckpoint = selectedDocument?.document_kind === "review_checkpoint";
  const isFinalDocument = selectedDocument?.document_kind === "final_document";
  const selectedJob = selectedDocument ? jobs.find((item) => item.id === selectedDocument.job_id) : null;
  const readyFigureCount = selectedJob?.nodes.filter((node) =>
    node.kind === "figure" && node.status === "completed").length || 0;
  const exportBusy = exportState?.state === "choosing" || exportState?.state === "working";
  const canFinalize = !!selectedDocument && selectedDocument.document_kind === "formal_candidate" &&
    selectedDocument.integrity.status === "verified" && selectedDocument.freshness.status === "current" &&
    ["passed", "failed"].includes(selectedDocument.quality.status);

  useEffect(() => {
    if (!connection) return;
    let canceled = false;
    (async () => {
      try {
        const items = await listModelConfigs(connection);
        if (canceled) return;
        const available = items.filter((item) => item.enabled && !!item.verified_at);
        setModels(available);
        const settings = await loadAppSettings(connection).catch(() => null);
        if (canceled) return;
        setModelId((current) => {
          if (available.some((item) => item.id === current)) return current;
          return available.some((item) => item.id === settings?.manual_model_id)
            ? settings?.manual_model_id || "" : available[0]?.id || "";
        });
      } catch {
        if (!canceled) {
          setModels([]);
          setModelId("");
        }
      }
    })();
    return () => { canceled = true; };
  }, [connection]);

  useEffect(() => {
    setJobs(trackedJob?.task_id === taskId ? [trackedJob] : []);
    setDocuments([]); setSelectedDocument(null); setQuality(null); setQualityAction(null);
    setFinalization(null); setExportState(null);
    setExportedPath(null); setExportReceipt(null); setEditor(null); setSectionViewer(null);
    setFiguresDirty(false); setDirtySections([]);
    setShowAllDocuments(false);
    releasePreviewPage(previewPageUrl); setPreviewPageUrl(null);
    setPreviewLoading(false); setPreviewError("");
    if (!connection || !taskId) return;
    setMessage("正在读取正式说明书版本…");
    loadVersions(connection, taskId).then(({ jobItems, documentItems }) => {
      setJobs(jobItems); setDocuments(documentItems);
      setSelectedDocument(documentItems[0] || null);
      const running = jobItems.find((item) => item.status === "queued" || item.status === "running");
      setMessage(running ? `已恢复生成任务 v${running.version}，正在${stepLabel(running.current_step)}…` : "");
    }).catch((error) => setMessage(error instanceof Error ? error.message : "说明书版本读取失败"));
  }, [connection, taskId]);

  useEffect(() => {
    if (!trackedJob || trackedJob.task_id !== taskId) return;
    setJobs((current) => [trackedJob, ...current.filter((item) => item.id !== trackedJob.id)]
      .sort((a, b) => b.version - a.version));
    if (["queued", "running"].includes(trackedJob.status)) {
      setClock(Date.now());
      setMessage(`生成任务 v${trackedJob.version}正在${stepLabel(
        trackedJob.current_step)}…切换页面不会丢失任务。`);
    }
  }, [trackedJob, taskId]);

  useEffect(() => {
    if (!connection || !taskId || !activeJobId) return;
    const timer = window.setInterval(async () => {
      setClock(Date.now());
      try {
        const versions = await loadVersions(connection, taskId);
        setJobs(versions.jobItems); setDocuments(versions.documentItems);
        setSelectedDocument((current) => current && versions.documentItems.some(
          (item) => item.id === current.id) ? current : versions.documentItems[0] || null);
        const running = versions.jobItems.find((item) => item.status === "queued" || item.status === "running");
        if (running) {
          setMessage(`生成任务 v${running.version}正在${stepLabel(running.current_step)}…`);
        } else {
          const latest = versions.documentItems[0] || null;
          if (latest) setSelectedDocument(latest);
          const newest = versions.jobItems[0];
          setMessage(newest?.status === "failed"
            ? `生成任务 v${newest.version}失败：${newest.safe_error_message || "请查看阶段记录"}`
            : latest ? `生成任务已完成，审阅稿 v${latest.version}已恢复。` : "生成任务已结束。");
        }
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "生成进度读取失败");
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [connection, taskId, activeJobId]);

  useEffect(() => () => releasePreviewPage(previewPageUrl), [previewPageUrl]);

  async function generate() {
    if (!connection || !taskId || !modelId || activeJob) return;
    setGenerating(true); setQuality(null); setExportedPath(null); setExportReceipt(null);
    setMessage("AI 正在研究证据、撰写正文和生成图表，随后将装配 Word 并逐页质检…");
    try {
      const job = await generateFormalManual(connection, taskId, modelId);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setMessage(`生成任务 v${job.version}已创建，正在${stepLabel(
        job.current_step)}…切换页面不会中断。`);
      await new Promise((resolve) => window.setTimeout(resolve, 300));
      const started = await loadVersions(connection, taskId);
      setJobs(started.jobItems); setDocuments(started.documentItems);
      const running = started.jobItems.find((item) =>
        item.status === "queued" || item.status === "running");
      if (running) setMessage(`生成任务 v${running.version}已开始，正在${stepLabel(
        running.current_step)}…离开本页也会继续执行。`);
      if (!running && started.documentItems[0]) {
        setSelectedDocument(started.documentItems[0]);
        setMessage(`审阅候选稿 v${started.documentItems[0].version} 已生成。`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "正式说明书生成失败");
    } finally { setGenerating(false); }
  }

  async function openPreview(document = selectedDocument) {
    if (!connection || !document) return;
    setPreviewLoading(true); setPreviewError("");
    setQualityAction(null);
    setMessage("正在载入逐页质量检查预览…");
    try {
      const value = await loadFormalManualQa(connection, document.job_id, document.version);
      setQuality(value); setSelectedDocument(document); setPreviewPage(1);
      const url = await loadFormalManualQaPage(connection, document.job_id, document.version, 1);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(1);
      setMessage("");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "说明书预览失败";
      setPreviewPageUrl(null); setPreviewError(detail); setMessage(detail);
    } finally { setPreviewLoading(false); }
  }

  async function changePreviewPage(page: number) {
    if (!connection || !selectedDocument || !quality || page < 1 || page > quality.page_count) return;
    setPreviewLoading(true); setPreviewError("");
    setMessage(`正在载入第 ${page} 页…`);
    try {
      const url = await loadFormalManualQaPage(
        connection, selectedDocument.job_id, selectedDocument.version, page);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(page); setMessage("");
    } catch (error) { const detail = error instanceof Error ? error.message : "说明书预览页读取失败";
      setPreviewPageUrl(null); setPreviewError(detail); setMessage(detail);
    } finally { setPreviewLoading(false); }
  }

  async function runQualityCheck() {
    if (!connection || !selectedDocument) return;
    setChecking(true); setMessage("正在逐页渲染并检查说明书…");
    try {
      const result = await runFormalManualQa(
        connection, selectedDocument.job_id, selectedDocument.version);
      setSelectedDocument(result.document);
      setDocuments((current) => current.map((item) => item.id === result.document.id
        ? result.document : item));
      setQuality(result.qa_run);
      const url = await loadFormalManualQaPage(
        connection, result.document.job_id, result.document.version, 1);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(1);
      setMessage(result.qa_run.passed ? "逐页质量检查通过，请人工确认是否生成终稿。" :
        "质量检查未通过，请查看检查结果后重新生成或修订。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书质量检查失败"); }
    finally { setChecking(false); }
  }

  async function exportDocument(reviewDraft = false) {
    if (!selectedDocument || exportBusy) return;
    if (selectedDocument.integrity.status !== "verified") {
      setExportState({ state: "error", message: "当前文档文件不完整，无法导出。" }); return;
    }
    if (!reviewDraft && selectedDocument.document_kind !== "final_document") {
      setExportState({ state: "error", message: "当前版本不是人工终稿，请先生成终稿。" }); return;
    }
    if (!reviewDraft && selectedDocument.freshness.status !== "current") {
      setExportState({ state: "error", message: "终稿引用的正文或资产已有更新，请重新定稿后导出。" }); return;
    }
    const defaultName = reviewDraft && selectedDocument.document_kind !== "review_checkpoint"
      ? selectedDocument.filename.replace(/\.docx$/i, "-审阅稿.docx")
      : selectedDocument.filename;
    setExportState({ state: "choosing", message: "正在打开保存位置选择窗口…" });
    try {
      const destination = await save({ title: reviewDraft ? "导出审阅稿" : "导出终稿",
        defaultPath: defaultName,
        filters: [{ name: "Word 文档", extensions: ["docx"] }] });
      if (!destination) {
        const canceled = "已取消导出，当前终稿没有发生变化。";
        setMessage(canceled); setExportState({ state: "canceled", message: canceled }); return;
      }
      setExportState({ state: "working", message: "正在写入文件并校验完整性…" });
      const result = await exportManualDocument(
        selectedDocument.job_id, selectedDocument.version, destination, reviewDraft);
      const success = reviewDraft
        ? `审阅稿已真实落盘并校验：${result.destinationPath}`
        : `终稿已导出并校验：${result.destinationPath}`;
      setExportedPath(result.destinationPath); setExportReceipt(result); setMessage(success);
      setExportState({ state: "success", message: success });
    } catch (error) {
      const detail = error instanceof Error ? error.message : "说明书导出失败";
      setMessage(detail); setExportState({ state: "error", message: detail });
    }
  }

  async function generateFinalDocument() {
    if (!connection || !selectedDocument || !canFinalize || checking) return;
    const warning = selectedDocument.quality.status === "failed"
      ? "当前审阅稿仍有未通过质量项。系统会保留检查记录，但最终决定由你承担。\n\n"
      : "";
    if (!window.confirm(`${warning}将以当前审阅稿 v${selectedDocument.version} 生成人工终稿。` +
      "终稿会删除审阅提示语并形成独立版本，继续吗？")) return;
    const workingMessage = "正在清理审阅措辞、生成独立终稿并执行最终逐页质检，请不要关闭应用。";
    setChecking(true); setMessage(workingMessage);
    setFinalization({ state: "working", message: workingMessage });
    try {
      const result = await finalizeFormalManualDocument(
        connection, selectedDocument.job_id, selectedDocument.version);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document); setQuality(result.qa_run);
      setExportedPath(null); setExportReceipt(null);
      const url = await loadFormalManualQaPage(
        connection, result.document.job_id, result.document.version, 1);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(1);
      const successMessage = `终稿 v${result.document.version} 已按人工决定生成，可以直接导出。`;
      setMessage(successMessage); setFinalization({ state: "success", message: successMessage });
    } catch (error) {
      const detail = error instanceof Error ? error.message : "终稿生成失败";
      setMessage(detail); setFinalization({ state: "error", message: detail });
    } finally { setChecking(false); }
  }

  async function showExport() {
    if (!exportedPath) return;
    try { await revealExportedDocument(exportedPath); }
    catch (error) { setMessage(error instanceof Error ? error.message : "导出文件定位失败"); }
  }

  function renderFinalizationStatus() {
    if (!finalization) return null;
    return <section className={`manual-finalization-status ${finalization.state}`}
      role="status" aria-live="assertive"><span aria-hidden="true">{
        finalization.state === "working" ? "" : finalization.state === "success" ? "✓" : "!"}</span><div>
        <strong>{finalization.state === "working" ? "正在生成终稿" : finalization.state === "success"
          ? "终稿已经生成" : "终稿没有生成"}</strong><small>{finalization.message}</small></div>
      {finalization.state === "error" && canFinalize && <button disabled={checking}
        onClick={generateFinalDocument}>重试生成终稿</button>}
      {finalization.state !== "working" && <button className="dismiss" aria-label="关闭终稿状态提示"
        onClick={() => setFinalization(null)}>×</button>}</section>;
  }

  function renderExportStatus() {
    if (!exportState) return null;
    return <section className={`manual-export-status ${exportState.state}`} role="status" aria-live="assertive">
      <span aria-hidden="true">{exportBusy ? "" : exportState.state === "success" ? "✓" :
        exportState.state === "error" ? "!" : "i"}</span><div><strong>{
        exportState.state === "choosing" ? "请选择保存位置" : exportState.state === "working" ? "正在导出终稿" :
          exportState.state === "success" ? "终稿已导出" : exportState.state === "error" ? "终稿导出失败" :
            "已取消导出"}</strong><small>{exportState.message}</small></div>
      {exportState.state === "error" && isFinalDocument && <button disabled={exportBusy}
        onClick={() => exportDocument(false)}>重试导出</button>}
      {!exportBusy && <button className="dismiss" aria-label="关闭导出状态提示"
        onClick={() => setExportState(null)}>×</button>}</section>;
  }

  async function openEditor(initialSectionKey = "") {
    if (!connection || !selectedDocument) return;
    setMessage("正在载入当前章节内容…");
    try {
      const value = await loadFormalManualPreview(
        connection, selectedDocument.job_id, selectedDocument.version);
      setEditor(structuredClone(value));
      setActiveSectionKey(value.sections.some((item) => item.section_key === initialSectionKey)
        ? initialSectionKey : value.sections[0]?.section_key || "");
      setQuality(null); releasePreviewPage(previewPageUrl); setPreviewPageUrl(null);
      setFiguresDirty(false); setDirtySections([]); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "章节内容读取失败"); }
  }

  async function openSectionViewer(node: FormalManualJob["nodes"][number]) {
    if (!connection || !cockpitJob) return;
    const sectionKey = node.key === "ui_section_update"
      ? "ui_operations" : node.key.replace(/^section:/, "");
    setBusyNodeKey(node.key); setMessage(`正在读取“${node.title}”正文…`);
    try {
      const sections = await listFormalManualSections(connection, cockpitJob.id);
      const section = sections.find((item) => item.section_key === sectionKey);
      if (!section) throw new Error("本章尚无可浏览正文，请等待生成完成或重试失败节点");
      setSectionViewer(section); setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "章节正文读取失败");
    } finally { setBusyNodeKey(""); }
  }

  function closeEditor() {
    if (dirtySections.length && !window.confirm(
      `还有 ${dirtySections.length} 个章节未保存，确定放弃这些修改吗？`)) return;
    setEditor(null); setDirtySections([]); setFiguresDirty(false);
  }

  function updateSection(sectionKey: string, update: (section: FormalManualPreview["sections"][number]) =>
    FormalManualPreview["sections"][number], markDirty = true) {
    setEditor((current) => current ? { ...current, sections: current.sections.map((section) =>
      section.section_key === sectionKey ? update(section) : section) } : current);
    if (markDirty) setDirtySections((current) => current.includes(sectionKey)
      ? current : [...current, sectionKey]);
  }

  async function saveSection() {
    if (!connection || !selectedDocument || !editor) return;
    const section = editor.sections.find((item) => item.section_key === activeSectionKey);
    if (!section) return;
    setEditorBusy(true); setMessage(`正在保存“${section.title}”的新修订…`);
    try {
      const saved = await editFormalManualSection(connection, selectedDocument.job_id,
        section.section_key, section.title, section.blocks);
      updateSection(section.section_key, (current) => ({ ...current, title: saved.title,
        status: saved.status, blocks: saved.blocks }), false);
      setDirtySections((current) => current.filter((key) => key !== section.section_key));
      setMessage(`“${saved.title}”已保存为人工修订 v${saved.version}，原版本仍保留。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "章节保存失败"); }
    finally { setEditorBusy(false); }
  }

  async function regenerateSection() {
    if (!connection || !selectedDocument || !editor) return;
    const section = editor.sections.find((item) => item.section_key === activeSectionKey);
    if (!section) return;
    setEditorBusy(true); setMessage(`AI 正在根据项目证据重新撰写“${section.title}”…`);
    try {
      const generated = await regenerateFormalManualSection(
        connection, selectedDocument.job_id, section.section_key);
      updateSection(section.section_key, (current) => ({ ...current, title: generated.title,
        status: generated.status, blocks: generated.blocks }), false);
      setDirtySections((current) => current.filter((key) => key !== section.section_key));
      setFiguresDirty(true);
      setMessage(`“${generated.title}”已生成 AI 修订 v${generated.version}，请审阅后再装配。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "AI 章节生成失败"); }
    finally { setEditorBusy(false); }
  }

  async function assembleRevision() {
    if (!connection || !selectedDocument || !editor) return;
    setEditorBusy(true); setMessage(figuresDirty ? "正在同步章节图表、装配 Word 并逐页质检…" :
      "正在装配修订版 Word 并逐页质检…");
    try {
      for (const sectionKey of dirtySections) {
        const section = editor.sections.find((item) => item.section_key === sectionKey);
        if (section) {
          await editFormalManualSection(connection, selectedDocument.job_id,
            section.section_key, section.title, section.blocks);
          setDirtySections((current) => current.filter((key) => key !== sectionKey));
        }
      }
      if (figuresDirty) await generateFormalManualFigures(connection, selectedDocument.job_id);
      const document = await assembleFormalManualDocument(connection, selectedDocument.job_id);
      const result = await runFormalManualQa(connection, document.job_id, document.version);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document); setEditor(null); setFiguresDirty(false); setDirtySections([]);
      setExportedPath(null);
      setMessage(result.qa_run.passed ? `修订版 v${document.version} 已装配并通过逐页质检。` :
        `修订版 v${document.version} 已装配，但未通过逐页质检。`);
      await openPreview(result.document);
    } catch (error) { setMessage(error instanceof Error ? error.message : "修订版装配失败"); }
    finally { setEditorBusy(false); }
  }

  async function reassembleLatestAssets() {
    if (!connection || !selectedDocument) return;
    setChecking(true); setMessage("正在把最新正文、图表和截图重新装配为新的 Word 版本…");
    try {
      const document = await assembleFormalManualDocument(connection, selectedDocument.job_id);
      const result = await runFormalManualQa(connection, document.job_id, document.version);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document); setExportedPath(null);
      setMessage(result.qa_run.passed ? `最新资产已装配为 v${document.version}，并通过逐页质检。` :
        `最新资产已装配为 v${document.version}，但逐页质检未通过。`);
      await openPreview(result.document);
    } catch (error) { setMessage(error instanceof Error ? error.message : "最新资产装配失败"); }
    finally { setChecking(false); }
  }

  async function qualityRepairContext(check: FormalManualQa["checks"][number]) {
    if (!connection || !selectedDocument) return { preview: null, sectionKeys: [], figureKeys: [] };
    const preview = await loadFormalManualPreview(
      connection, selectedDocument.job_id, selectedDocument.version);
    const actual = Array.isArray(check.actual) ? check.actual : [];
    const sectionKeys = new Set<string>();
    if (check.key === "content.section_depth") {
      actual.forEach((item) => {
        if (item && typeof item === "object" && "section_key" in item) {
          sectionKeys.add(String((item as { section_key: unknown }).section_key));
        }
      });
    } else if (check.key === "content.required_sections") {
      const expected = Array.isArray(check.expected) ? check.expected.map(String) : [];
      const present = new Set(actual.map(String));
      expected.filter((key) => !present.has(key)).forEach((key) => sectionKeys.add(key));
    } else {
      const needles = actual.filter((item) => typeof item === "string").map(String);
      preview.sections.forEach((section) => {
        const content = JSON.stringify(section.blocks);
        if (needles.some((needle) => content.includes(needle))) sectionKeys.add(section.section_key);
      });
    }
    if (check.key === "content.figure_coverage") {
      const expected = Array.isArray(check.expected) ? check.expected.map(String) : [];
      const present = new Set(actual.map(String));
      expected.filter((key) => !present.has(key)).forEach((key) => sectionKeys.add(key));
    }
    const figureKeys = preview.sections.filter((section) => sectionKeys.has(section.section_key))
      .flatMap((section) => section.blocks.filter((block) => block.type === "figure_request")
        .map((block) => block.figure_key));
    return { preview, sectionKeys: Array.from(sectionKeys), figureKeys };
  }

  async function repairQualityCheck(check: FormalManualQa["checks"][number]) {
    if (!connection || !selectedDocument || checking) return;
    setChecking(true);
    setQualityAction({ checkKey: check.key, kind: "repair", state: "working", message: "正在定位该问题对应的章节和产物…" });
    try {
      const context = await qualityRepairContext(check);
      if (check.key === "content.figure_coverage") {
        if (!context.figureKeys.length) throw new Error("正文中没有找到缺失图表请求，无法定向恢复");
        if (!window.confirm(`将单项生成 ${context.figureKeys.length} 张缺失图表并重新装配说明书，继续吗？`)) {
          setQualityAction({ checkKey: check.key, kind: "repair", state: "info", message: "已取消，本次没有修改图表或文档。" });
          return;
        }
        setMessage(`正在恢复 ${context.figureKeys.length} 张缺失图表…`);
        for (const [index, figureKey] of context.figureKeys.entries()) {
          setQualityAction({ checkKey: check.key, kind: "repair", state: "working",
            message: `正在生成缺失图表 ${index + 1}/${context.figureKeys.length}…` });
          await regenerateFormalManualFigure(connection, selectedDocument.job_id, figureKey);
        }
      } else {
        if (!context.sectionKeys.length) throw new Error("没有定位到需要修复的章节");
        const titles = context.preview?.sections.filter((item) =>
          context.sectionKeys.includes(item.section_key)).map((item) => item.title) || context.sectionKeys;
        if (!window.confirm(`AI 将根据项目证据重新生成“${titles.join("、")}”并替换当前章节版本；历史修订仍保留。继续吗？`)) {
          setQualityAction({ checkKey: check.key, kind: "repair", state: "info", message: "已取消，本次没有修改章节或文档。" });
          return;
        }
        setMessage(`正在定向修复 ${context.sectionKeys.length} 个章节…`);
        for (const [index, sectionKey] of context.sectionKeys.entries()) {
          setQualityAction({ checkKey: check.key, kind: "repair", state: "working",
            message: `AI 正在修复命中章节 ${index + 1}/${context.sectionKeys.length}：${titles[index] || sectionKey}` });
          await regenerateFormalManualSection(connection, selectedDocument.job_id, sectionKey);
        }
      }
      setMessage("修复完成，正在使用当前可用资产重新装配并复检…");
      setQualityAction({ checkKey: check.key, kind: "repair", state: "working", message: "定向修复已完成，正在重新装配 Word…" });
      const document = await assembleFormalManualDocument(connection, selectedDocument.job_id);
      setQualityAction({ checkKey: check.key, kind: "repair", state: "working", message: `Word v${document.version} 已装配，正在逐页复检…` });
      const result = await runFormalManualQa(connection, document.job_id, document.version);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document); setQuality(result.qa_run); setExportedPath(null);
      const url = await loadFormalManualQaPage(
        connection, result.document.job_id, result.document.version, 1);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(1);
      setMessage(result.qa_run.passed ? "定向修复已闭环，新的候选稿通过逐页质检。" :
        "定向修复和重新装配已完成；仍有未通过项，请继续逐项处理。");
      setQualityAction({ checkKey: check.key, kind: "repair", state: "success", message: result.qa_run.passed
        ? `处理完成：新候选稿 v${document.version} 已生成并通过质检。`
        : `处理完成：新候选稿 v${document.version} 已生成；报告已刷新，仍有未通过项。` });
    } catch (error) {
      const detail = error instanceof Error ? error.message : "质量问题定向修复失败";
      setMessage(detail);
      setQualityAction({ checkKey: check.key, kind: "repair", state: "error", message: `处理失败：${detail}` });
      const versions = await loadVersions(connection, taskId).catch(() => null);
      if (versions) { setJobs(versions.jobItems); setDocuments(versions.documentItems); }
    } finally { setChecking(false); }
  }

  async function editQualityCheck(check: FormalManualQa["checks"][number]) {
    try {
      const context = await qualityRepairContext(check);
      if (!context.sectionKeys.length) throw new Error("没有定位到可手动编辑的章节");
      await openEditor(context.sectionKeys[0]);
      setMessage(`已定位到相关章节；修改后点击“装配修订版并质检”完成闭环。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "章节定位失败");
    }
  }

  async function deferQualityCheck(check: FormalManualQa["checks"][number]) {
    if (!connection || !selectedDocument || checking) return;
    const reason = window.prompt(
      "请填写忽略原因。该项会从待处理列表移除并保留审计记录；真实性、完整性和安全检查不能忽略。",
      "已人工复核并接受当前结果"
    );
    if (!reason) return;
    setChecking(true);
    setQualityAction({ checkKey: check.key, kind: "defer", state: "working", message: "正在保存本轮忽略原因和审计留痕…" });
    try {
      const updated = await deferFormalManualQaCheck(
        connection, selectedDocument.job_id, selectedDocument.version, check.key, reason);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      const refreshed = versions.documentItems.find((item) => item.id === selectedDocument.id);
      if (refreshed) setSelectedDocument(refreshed);
      setQuality(updated); setMessage(updated.passed
        ? "已忽略并留痕；当前没有剩余阻断项，请人工确认是否生成终稿。"
        : "已忽略并留痕；该项已从待处理列表移除，请继续处理其余问题。");
      setQualityAction({ checkKey: check.key, kind: "defer", state: "success",
        message: updated.passed ? "豁免已保存：质量门槛按剩余未豁免项重新计算，当前已可交付。"
          : "豁免已保存：该项已移出待处理列表，原始检查结果和原因仍保留。" });
    } catch (error) {
      const detail = error instanceof Error ? error.message : "质量问题留痕失败";
      setMessage(detail); setQualityAction({ checkKey: check.key, kind: "defer", state: "error", message: `留痕失败：${detail}` });
    } finally { setChecking(false); }
  }

  async function retryNode(node: FormalManualJob["nodes"][number]) {
    if (!connection || !cockpitJob || busyNodeKey) return;
    setBusyNodeKey(node.key);
    try {
      if (node.kind === "screenshot_analysis" && node.status === "failed") {
        await retryScreenshotAnalysisNode(connection, cockpitJob.id, node.key);
        setMessage(`“${node.title}”已重新排队；只会重试这一张截图。`);
      } else if (["screenshot", "screenshot_import", "screenshot_review"].includes(node.kind)) {
        onOpenScreenshots(); return;
      } else if (node.kind === "figure") {
        await regenerateFormalManualFigure(connection, cockpitJob.id, node.key.replace(/^figure:/, ""));
        setMessage(`“${node.title}”已单项重试完成；请装配当前可用资产生成新候选稿。`);
      } else if (node.kind === "section") {
        await regenerateFormalManualSection(connection, cockpitJob.id, node.key.replace(/^section:/, ""));
        setMessage(`“${node.title}”已单项重试完成；阶段正文与关联资产已标记更新。`);
      } else if (node.kind === "assemble" && selectedDocument) {
        await reassembleLatestAssets();
      } else if (node.kind === "qa" && selectedDocument && !isCheckpoint) {
        await runQualityCheck();
      }
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "节点重试失败");
    } finally { setBusyNodeKey(""); }
  }

  async function actOnNode(node: FormalManualJob["nodes"][number]) {
    const artifactVersion = Number(node.output.version || 0);
    const artifact = documents.find((item) => item.job_id === cockpitJob?.id &&
      item.version === artifactVersion);
    if (artifact && node.kind === "assemble" && node.status !== "failed") {
      setSelectedDocument(artifact); setQuality(null); setExportedPath(null);
      setMessage(`${artifact.document_kind === "review_checkpoint" ? "阶段审阅稿" :
        artifact.document_kind === "final_document" ? "人工终稿" : "审阅候选稿"} v${
        artifact.version} 已切换到产物区，可查看正文或真实落盘导出。`);
      return;
    }
    if (node.kind === "qa" && node.status !== "failed" && selectedDocument && !isCheckpoint) {
      await openPreview(); return;
    }
    if (node.kind === "figure" && node.status !== "failed") { onOpenDiagrams(); return; }
    if (node.kind === "section" && node.status !== "failed") {
      await openSectionViewer(node); return;
    }
    await retryNode(node);
  }

  function renderExecutionNode(node: ExecutionNode, compact = false) {
    if (!cockpitJob) return null;
    const model = models.find((item) => item.id === node.model_config_id);
    const action = nodeActionLabel(node, documents, cockpitJob.id, !!activeJob);
    return <article className={`${node.status}${compact ? " compact" : ""}`} key={node.key}><i />
      <div className="node-main"><strong>{node.title}</strong><small>{nodeStatusLabel(node.status)} · {
        nodeDurationLabel(node, clock)} · {nodeAttemptLabel(node)}</small>{!compact && node.dependencies.length > 0 &&
        <small>依赖：{node.dependencies.map(dependencyLabel).join("、")}</small>}{!compact &&
        node.model_config_id && <small>模型：{model ? `${model.name} · ${model.model_name}` :
          node.model_config_id.slice(0, 8)}</small>}{typeof node.output.retry_reason === "string" &&
        <small className="node-retry">上次未通过：{String(node.output.retry_reason)}</small>}{!compact &&
        typeof node.output.artifact_path === "string" && <small className="node-artifact">产物：{
          String(node.output.artifact_path)}</small>}{node.safe_error_message && <small className="node-error">{
          node.safe_error_message}</small>}{node.next_action && <small className="node-next">下一动作：{
          node.next_action}</small>}</div>{action && <button disabled={busyNodeKey === node.key}
        onClick={() => actOnNode(node)}>{busyNodeKey === node.key ? "处理中…" : action}</button>}
    </article>;
  }

  function renderFlowNode(node: ExecutionNode, variant = "default") {
    if (!cockpitJob) return null;
    const model = models.find((item) => item.id === node.model_config_id);
    const action = nodeActionLabel(node, documents, cockpitJob.id, !!activeJob);
    return <article className={`flow-node ${node.status} ${variant}`} key={node.key}>
      <header><i /><strong>{node.title}</strong><span>{nodeStatusLabel(node.status)}</span></header>
      <small>{nodeDurationLabel(node, clock)} · {nodeAttemptLabel(node)}</small>
      {node.safe_error_message && <p className="node-error">{node.safe_error_message}</p>}
      <div className="flow-node-actions">{action && <button disabled={busyNodeKey === node.key}
        onClick={() => actOnNode(node)}>{busyNodeKey === node.key ? "处理中…" : action}</button>}
        <details><summary>详情</summary><div>{node.dependencies.length > 0 &&
          <small>依赖：{node.dependencies.map(dependencyLabel).join("、")}</small>}
          {node.model_config_id && <small>模型：{model ? `${model.name} · ${model.model_name}` :
            node.model_config_id.slice(0, 8)}</small>}
          {typeof node.output.retry_reason === "string" && <small className="node-retry">上次未通过：{
            String(node.output.retry_reason)}</small>}
          {typeof node.output.artifact_path === "string" && <small className="node-artifact">产物：{
            String(node.output.artifact_path)}</small>}
          {node.next_action && <small className="node-next">下一动作：{node.next_action}</small>}
        </div></details></div>
    </article>;
  }

  function renderExecutionGraph(job: FormalManualJob) {
    const researchNodes = job.nodes.filter((node) => ["research", "profile"].includes(node.kind));
    const sections = job.nodes.filter((node) => node.kind === "section" && node.key.startsWith("section:"));
    const figures = job.nodes.filter((node) => node.kind === "figure");
    const screenshots = job.nodes.filter((node) => node.kind.startsWith("screenshot") ||
      node.key === "ui_section_update");
    const documentsAndQa = job.nodes.filter((node) => ["assemble", "qa"].includes(node.kind));
    const sectionTitles = new Map(sections.map((node) => [node.key, node.title]));
    const figureSources = (figure: ExecutionNode) => figure.dependencies
      .filter((dependency) => sectionTitles.has(dependency))
      .map((dependency) => sectionTitles.get(dependency) || dependencyLabel(dependency));
    return <div className="execution-flow-graph">
      <header><div><strong>执行依赖图</strong><small>连线表示真实依赖；章节完成后，其图表立即独立派发。</small></div>
        <div className="flow-legend"><span className="running">运行</span><span className="completed">完成</span>
          <span className="warning">警告 / 待授权</span><span className="failed">失败</span></div></header>
      {researchNodes.length > 0 && <div className="flow-root">{researchNodes.map((node) =>
        renderFlowNode(node, "root"))}</div>}
      {(sections.length > 0 || screenshots.length > 0) && <div className="flow-fork"><span>研究完成后并行派生</span></div>}
      {sections.length > 0 && <section className="flow-lane flow-section-lane"><header><div>
        <strong>正文并发撰写</strong><small>固定分层展示，不把图表子节点塞进章节卡片</small></div>
        <span>{sections.filter((node) => nodeIsResolved(node.status)).length}/{sections.length} 章</span></header>
        <div className="flow-node-grid flow-sections">{sections.map((node, index) =>
          <div className="flow-numbered-node" key={node.key}><b>{String(index + 1).padStart(2, "0")}</b>{
            renderFlowNode(node, "section")}</div>)}</div></section>}
      {screenshots.length > 0 && <section className="flow-lane flow-screenshot-lane"><header><div>
        <strong>并行旁路 · 截图证据</strong><small>导入、逐图解读、审核和第 7 章更新均为真实节点；等待用户不占并发槽</small></div>
        <span>{screenshots.filter((node) => nodeIsResolved(node.status)).length}/{screenshots.length}</span></header>
        <div className="flow-node-grid flow-screenshots">{screenshots.map((node) =>
          renderFlowNode(node, "screenshot"))}</div></section>}
      {figures.length > 0 && <><div className="flow-step-arrow"><span>已完成章节立即派发图表</span></div>
        <section className="flow-lane flow-figure-lane"><header><div><strong>章节衍生图表</strong>
          <small>每张图独立执行；失败只保留该节点供单项重试</small></div><span>{figures.filter((node) =>
            nodeIsResolved(node.status)).length}/{figures.length} 张</span></header>
          <div className="flow-node-grid flow-figures">{figures.map((node) => <div className="flow-derived-node"
            key={node.key}><p>来源：{figureSources(node).join("、") || "跨章节汇总"}</p>{
              renderFlowNode(node, "figure")}</div>)}</div></section></>}
      {documentsAndQa.length > 0 && <><div className="flow-merge"><span>当前可用资产汇合</span></div>
        <section className="flow-delivery"><header><div><strong>审阅与交付</strong>
          <small>正文先形成阶段稿；当前图表和截图再装配候选稿，最后逐页 QA。</small></div></header>
          <div>{documentsAndQa.map((node, index) => <div className="flow-delivery-step" key={node.key}>
            {index > 0 && <i aria-hidden="true" />}{renderFlowNode(node, node.kind)}</div>)}</div></section></>}
    </div>;
  }

  return <main className="manual-page"><header className="topbar"><div>
    <p className="eyebrow">TECHNICAL MANUAL</p><h1>说明书</h1>
    <p>AI 基于项目证据生成结构化正文与图表，并装配为可预览、可导出的正式 Word 文档。</p></div>
    <div className="manual-selectors"><ProjectSwitcher connection={connection} taskId={taskId}
      onChange={onTaskChange} /><label className="model-switcher"><small>生成模型</small>
      <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
        <option value="">尚无已验证模型</option>{models.map((item) => <option value={item.id}
          key={item.id}>{item.name} · {item.model_name}</option>)}</select></label></div></header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>DOC</span>
      <h2>请先选择项目</h2><p>说明书将复用项目扫描得到的事实、源码证据和确认信息。</p></section> :
      <section className="manual-content">{message && <div className="source-notice">{message}</div>}
        <div className="ai-generation-card"><div><span>AI</span><div><strong>一键生成正式软件说明书</strong>
          <p>自动完成证据研究、结构化正文、章节图表、截图决策与 DOCX 装配；内部步骤可独立追踪和重试。</p></div></div>
          <button disabled={!modelId || generating || !!activeJob} onClick={generate}>{activeJob
            ? `${stepLabel(activeJob.current_step)} ${activeJob.progress.percent}%` : generating ? "正在创建生成任务…" :
            !modelId ? "请先验证可用模型" : documents.length ? "生成新版本" : "生成正式说明书"}</button></div>

        <section className="manual-flow-guide"><header><div><strong>从项目到正式稿，只走 5 步</strong>
          <small>正文预览可以提前生成；图表与截图就绪后装配审阅稿，终稿必须由人工确认生成。</small></div>
          <span>正式装配有闸门</span></header><ol>{manualFlowStages(visibleJob, selectedDocument).map((stage, index) =>
            <li className={stage.status} key={stage.title}><b>{index + 1}</b><div><strong>{stage.title}</strong>
              <small>{stage.detail}</small></div><em>{stage.status === "done" ? "已完成" : stage.status === "active" ? "进行中" :
                stage.status === "blocked" ? "待处理" : "未开始"}</em></li>)}</ol></section>

        {activeJob && cockpitJob && activeJob.nodes.length > 0 && <section className="manual-task-cockpit"><header><div>
          <span className={`cockpit-state ${cockpitJob.status}`}>{jobStatusLabel(cockpitJob)}</span>
          <strong>任务驾驶舱 · v{cockpitJob.version}</strong><small>按真实执行节点聚合 · 已处理 {
            cockpitJob.progress.completed}/{cockpitJob.progress.total} · 已运行 {
            formatElapsed(clock - Date.parse(cockpitJob.started_at || cockpitJob.created_at))}</small></div><div>
          <b>{cockpitJob.progress.percent}%</b><small>预计剩余 {estimateJobEta(cockpitJob)}</small></div></header>
          <div className="manual-progress-track"><i style={{ width: `${cockpitJob.progress.percent}%` }} /></div>
          <div className="cockpit-metrics"><span><b>模型</b>{activeModel
            ? `${activeModel.name} · ${activeModel.model_name}` : cockpitJob.model_config_id.slice(0, 8)}</span>
            <span><b>并发</b>{jobConcurrency(cockpitJob)} 路</span><span><b>心跳</b>{
              heartbeatLabel(cockpitJob, clock)}</span><span><b>节点</b>{nodeStatusSummary(cockpitJob)}</span></div>
          <div className="cockpit-stage-strip">{cockpitGroups(cockpitJob).map((group) => <div key={group.key}
            className={groupStageStatus(group.nodes)}><i /><strong>{group.title}</strong><span>{
              group.nodes.filter((node) => nodeIsResolved(node.status)).length}/{group.nodes.length}</span></div>)}</div>
          <div className="cockpit-focus-grid"><section><header><strong>当前正在处理</strong><span>{
            cockpitFocusNodes(cockpitJob).length} 项</span></header><div>{cockpitFocusNodes(cockpitJob).length ?
              cockpitFocusNodes(cockpitJob).map((node) => renderExecutionNode(node, true)) :
              <p>正在等待下一批依赖节点进入队列。</p>}</div></section><section><header><strong>最近完成与待办</strong>
              <span>自动更新</span></header><div>{cockpitRecentNodes(cockpitJob).map((node) =>
                renderExecutionNode(node, true))}</div></section></div>
          <details className="cockpit-all-nodes"><summary>查看全部 {cockpitJob.nodes.length} 个执行节点、依赖与产物</summary>
            {renderExecutionGraph(cockpitJob)}</details>{
            activeJobs.length > 1 && <p className="duplicate-job-warning">检测到 {
                activeJobs.length} 个早先重复启动的任务；已禁止继续重复创建，当前展示最新任务。</p>}
        </section>}

        {!activeJob && visibleJob && <section className={`manual-run-summary ${terminalIssues.length ? "attention" : "clear"}`}>
          <header><div><span>{terminalIssues.length ? "待处理" : "已结束"}</span><div>
            <strong>最近任务 v{visibleJob.version} · {jobStatusLabel(visibleJob)}</strong>
            <small>已产出内容不等于通过产品验收；请先处理失败、截图与 QA 问题。</small></div></div>
            <time>{visibleJob.finished_at ? visibleJob.finished_at.replace("T", " ").slice(0, 19) :
              visibleJob.updated_at.replace("T", " ").slice(0, 19)}</time></header>
          {terminalIssues.length > 0 ? <div className="manual-run-issues">{terminalIssues.map((node) => {
            const action = nodeActionLabel(node, documents, visibleJob.id, false);
            return <article className={node.status} key={node.key}><i /><div><strong>{node.title}</strong>
              <small>{nodeStatusLabel(node.status)}{node.safe_error_message ? ` · ${node.safe_error_message}` : ""}</small>
              {node.next_action && <p>{node.next_action}</p>}</div>{action && <button
                disabled={busyNodeKey === node.key} onClick={() => actOnNode(node)}>{busyNodeKey === node.key
                  ? "处理中…" : action}</button>}</article>;
          })}</div> : <p className="manual-run-clear">执行节点没有遗留错误；文档内容质量仍需人工确认后才能视为完成。</p>}
        </section>}

        {selectedDocument && <><section className={`manual-result ${isCheckpoint ? "checkpoint" : ""} ${
          selectedDocument.freshness.status === "outdated" || selectedDocument.quality.status === "outdated" ? "outdated" : ""}`}><div><span>DOCX</span><div>
          <strong>{selectedDocument.filename}</strong><small>{isCheckpoint ? "正文预览快照（非最终装配）" :
            isFinalDocument ? "人工确认终稿" : "审阅候选稿"} v{
            selectedDocument.version} · {
            selectedDocument.qa.section_count} 章 · {selectedDocument.qa.figure_count} 张图表 · {
            selectedDocument.qa.screenshot_count} 张截图 · {(selectedDocument.integrity.size_bytes || 0) / 1024 / 1024 < 0.1
              ? `${Math.round((selectedDocument.integrity.size_bytes || 0) / 1024)} KiB`
              : `${((selectedDocument.integrity.size_bytes || 0) / 1024 / 1024).toFixed(2)} MiB`}</small></div></div>
          <div>{!isCheckpoint && (selectedDocument.freshness.status === "outdated" ||
            selectedDocument.quality.status === "outdated") && <button className="refresh-document"
            disabled={checking} onClick={reassembleLatestAssets}>{checking ? "正在重新装配…" : "重新装配最新内容"}</button>}
            {!isFinalDocument && <button onClick={() => openEditor()}>{isCheckpoint ? "预览正文" : "编辑内容"}</button>}{!isCheckpoint && <button disabled={checking} onClick={selectedDocument.quality.status === "not_checked"
            ? runQualityCheck : () => openPreview()}>{checking ? "正在质检…" :
              selectedDocument.quality.status === "not_checked" ? "执行逐页质检" : "逐页预览"}</button>}
            {!isCheckpoint && !isFinalDocument && <button disabled={exportBusy}
              onClick={() => exportDocument(true)}>{exportBusy ? "正在导出…" : "导出审阅稿…"}</button>}
            {canFinalize && <button className="primary" disabled={checking} onClick={generateFinalDocument}>{
              checking ? "正在生成终稿…" : "生成终稿"}</button>}
            {(isCheckpoint || isFinalDocument) && <button className="primary"
            disabled={selectedDocument.integrity.status !== "verified" || exportBusy}
            onClick={exportedPath ? showExport : () => exportDocument(isCheckpoint)}>{exportedPath ? "在文件夹中显示" :
              exportState?.state === "choosing" ? "请选择保存位置…" :
              exportState?.state === "working" ? "正在导出终稿…" :
              isCheckpoint ? "导出阶段审阅稿…" :
              selectedDocument.freshness.status === "outdated" ? "重新装配后导出" :
              selectedDocument.quality.status === "outdated" ? "按新标准重新装配" :
              "导出终稿…"}</button>}</div>
        </section>{!quality && renderFinalizationStatus()}{!quality && renderExportStatus()}{isCheckpoint && <section className="manual-recovery-card"><div>
          <strong>{readyFigureCount ? `已有 ${readyFigureCount} 张图表可回填` : "这是正文预览，不代表流程已装配"}</strong>
          <p>正文预览快照只用于提前审阅文字，不包含尚未完成的图表或截图；审阅候选稿会在证据资产闸门通过后另行装配。</p></div><div>
          <button disabled={checking || !!activeJob} onClick={reassembleLatestAssets}>{checking
            ? "正在装配…" : "用当前资产装配候选稿"}</button>
          {selectedDocument.qa.screenshot_count === 0 && <button className="primary"
            onClick={onOpenScreenshots}>补充界面截图</button>}</div></section>}{exportReceipt && <div className="manual-export-receipt"><span>已校验落盘</span><div>
          <strong>{exportReceipt.destinationPath}</strong><small>{formatBytes(exportReceipt.sizeBytes)} · SHA-256 {
            exportReceipt.sha256.slice(0, 12)}… · {exportReceipt.receiptRecorded ? "导出记录已保存" : "导出记录保存失败"}</small></div>
          <button onClick={showExport}>在文件夹中显示</button></div>}
        {!isCheckpoint && selectedDocument.freshness.status === "outdated" && <div className="manual-stale-notice">
          正文、图表或截图在此文档生成后发生了变化。当前 v{selectedDocument.version} 仍保留为历史版本，
          请重新装配生成新版本后再导出。</div>}
        {!isCheckpoint && selectedDocument.quality.status === "outdated" && <div className="manual-stale-notice">
          当前 v{selectedDocument.version} 曾按历史标准通过，但 Word 生成器或质检规则已经升级。
          文件仍可预览，请重新装配并按当前标准检查后再导出。</div>}
        </>}

        {documents.length > 0 && <section className="manual-versions"><header><strong>文档版本</strong>
          <small>默认显示最近 {Math.min(6, documents.length)} 个 · 共 {documents.length} 个</small></header><div>{(
            showAllDocuments ? documents : documents.slice(0, 6)).map((item) => <button
            className={selectedDocument?.id === item.id ? "active" : ""} key={item.id}
            onClick={() => { setSelectedDocument(item); setQuality(null); setFinalization(null); setExportState(null); setExportedPath(null);
              setExportReceipt(null); }}>
            <b>v{item.version}</b><span>{item.document_kind === "review_checkpoint" ? "正文预览快照" :
              item.document_kind === "final_document" ? "人工终稿" :
              item.created_at.replace("T", " ").slice(0, 16)}</span>
            <em>{item.integrity.status !== "verified" ? "文件异常" : item.document_kind === "review_checkpoint" ?
              "非最终装配，仅供文字预览" : item.freshness.status === "outdated" ?
              "内容已更新，待重新装配" : item.quality.status === "outdated" ? "历史标准已过期" :
              item.quality.status === "passed" ? "质量检查通过" : item.quality.status === "failed" ?
              "质量检查未通过" : "待质量检查"}</em></button>)}</div>{documents.length > 6 && <button
                className="manual-version-toggle" onClick={() => setShowAllDocuments((value) => !value)}>{
                showAllDocuments ? "收起历史版本" : `查看全部 ${documents.length} 个版本`}</button>}</section>}

        {!activeJob && visibleJob && visibleJob.nodes.length > 0 && <details className="manual-finished-graph">
          <summary>查看本次执行依赖图 · {visibleJob.nodes.length} 个真实节点</summary>{
            renderExecutionGraph(visibleJob)}</details>}

        {jobs.length > 0 && <details className="manual-advanced"><summary>高级：查看生成阶段留痕</summary>
          <p>阶段状态仅用于进度、失败定位和独立重试，不再要求用户逐项点击。</p>
          <div className="pipeline-history">{jobs.map((job) => <article key={job.id}><header>
            <strong>生成任务 v{job.version}</strong><span>{job.progress.percent}% · {job.status}</span></header>
            <div>{job.steps.map((step) => <span className={step.status} key={step.key}>
              {stepLabel(step.key)} · {step.status} · 第 {step.attempt} 次{
              step.started_at ? ` · ${stepDurationLabel(step, clock)}` : ""}{
              step.safe_error_message ? ` · ${step.safe_error_message}` : ""}</span>)}</div></article>)}</div></details>}

      </section>}

    {quality && selectedDocument && <div className="document-viewer manual-document-viewer" role="dialog" aria-modal="true">
      <div className="document-viewer-shell manual-document-shell"><header><div><strong>{selectedDocument.project_name} 软件说明书</strong>
        <small>{selectedDocument.project_version} · 文档 v{selectedDocument.version} · {
          isFinalDocument ? "人工已定稿" : quality.passed ? "质量检查通过" : "质量检查未通过"}</small></div><div>
        {isFinalDocument ? <button disabled={selectedDocument.integrity.status !== "verified" || exportBusy}
          onClick={exportedPath ? showExport : () => exportDocument(false)}>{exportedPath ? "在文件夹中显示" :
            exportState?.state === "choosing" ? "请选择保存位置…" :
              exportState?.state === "working" ? "正在导出终稿…" : "导出终稿…"}</button> : <>{canFinalize && <button className="primary" disabled={checking}
              onClick={generateFinalDocument}>{checking ? "正在生成终稿…" : "生成终稿"}</button>}
            <button onClick={() => exportDocument(true)}>导出审阅稿…</button></>}
        <button onClick={() => { setQuality(null); releasePreviewPage(previewPageUrl);
          setPreviewPageUrl(null); setPreviewError(""); setQualityAction(null); }}>关闭</button></div></header>
        {renderFinalizationStatus()}
        {renderExportStatus()}
        {!isFinalDocument && qualityAction && <div className={`manual-qa-action ${qualityAction.state}`} role="status" aria-live="polite">
          <span className="manual-qa-action-icon">{qualityAction.state === "working" ? "" :
            qualityAction.state === "success" ? "✓" : qualityAction.state === "error" ? "!" : "i"}</span>
          <div><strong>{qualityAction.state === "working" ? "正在处理" : qualityAction.state === "success" ?
            "操作已完成" : qualityAction.state === "error" ? "操作未完成" : "操作已取消"}</strong>
            <small>{qualityAction.message}</small></div>
          {qualityAction.state !== "working" && <button aria-label="关闭操作提示"
            onClick={() => setQualityAction(null)}>×</button>}
        </div>}
        <div className="document-viewer-body"><aside className="source-page-nav">
          <label>跳转页码<input aria-label="说明书跳转页码" type="number" min={1} max={quality.page_count}
            value={previewPage} onChange={(event) => { const page = Number(event.target.value);
              if (page >= 1 && page <= quality.page_count) changePreviewPage(page); }} /></label>
          <small>快速页码</small><div>{nearbyManualPages(previewPage, quality.page_count).map((page, index) =>
            page === null ? <span key={`gap-${index}`}>…</span> : <button key={page}
              className={previewPage === page ? "active" : ""} onClick={() => changePreviewPage(page)}>{page}</button>)}</div>
          <p>这里展示 Word 的真实渲染页，与源码文档保持相同的翻页方式。</p>
        </aside><section className="source-docx-preview manual-docx-preview">
          {!isFinalDocument && quality.checks.some((item) => !item.passed && !(quality.decisions || []).some(
            (decision) => decision.check_key === item.key)) &&
            <details className="manual-qa-issues" open><summary>
            {quality.checks.filter((item) => !item.passed && !(quality.decisions || []).some(
              (decision) => decision.check_key === item.key)).length
            } 项待处理质量问题 · 点击查看处理建议</summary>
            <ul>{quality.checks.filter((item) => !item.passed && !(quality.decisions || []).some(
              (decision) => decision.check_key === item.key)).map((item) => {
              const decision = (quality.decisions || []).find((value) => value.check_key === item.key);
              const canRepair = ["content.section_depth", "content.unverified_outcomes",
                "content.placeholders", "content.epistemic_caveats", "content.inference_claims",
                "content.required_sections", "content.figure_coverage"].includes(item.key);
              const canEdit = canRepair && item.key !== "content.figure_coverage";
              const acting = checking && qualityAction?.checkKey === item.key;
              return <li className={item.severity} key={item.key}><b>{qaCheckLabel(item.key)}</b>
                <span>{item.message}{decision && <small className="qa-decision">已留痕忽略：{
                  decision.reason}</small>}</span><div className="qa-issue-actions">
                  {canRepair && <button disabled={checking} onClick={() => repairQualityCheck(item)}>{
                    acting && qualityAction?.kind === "repair" ? "正在处理…" : item.key === "content.figure_coverage" ?
                      "生成缺失图表并装配" : "AI 修复命中章节并装配"}</button>}
                  {canEdit && <button disabled={checking} onClick={() => editQualityCheck(item)}>手动编辑命中章节</button>}
                  {!decision && ["render.page_density", "content.section_depth",
                    "content.figure_coverage"].includes(item.key) && <button className="secondary"
                    disabled={checking} onClick={() => deferQualityCheck(item)}>{acting && qualityAction?.kind === "defer" ?
                      "正在保存…" : "忽略并留痕"}</button>}
                </div></li>;
            })}</ul>
            {quality.checks.some((item) => item.key === "content.ui_screenshot" && !item.passed) && <button
              onClick={() => { setQuality(null); onOpenScreenshots(); }}>导入真实界面截图</button>}</details>}
          <div className="source-docx-pager"><button disabled={previewPage <= 1 || previewLoading}
            onClick={() => changePreviewPage(previewPage - 1)}>上一页</button><strong>第 {previewPage} / {
              quality.page_count} 页</strong><button disabled={previewPage >= quality.page_count || previewLoading}
            onClick={() => changePreviewPage(previewPage + 1)}>下一页</button></div>
          {previewLoading && <div className="manual-preview-state">
          正在读取第 {previewPage} 页真实渲染…</div>}
          {!previewLoading && previewError && <div className="manual-preview-state error"><strong>本页加载失败</strong>
            <p>{previewError}</p><button onClick={() => changePreviewPage(previewPage)}>重新加载本页</button></div>}
          {!previewLoading && previewPageUrl && <img className="manual-qa-page"
          src={previewPageUrl} alt={`说明书第 ${previewPage} 页`} />}</section></div>
      </div></div>}

    {sectionViewer && <div className="document-viewer manual-section-viewer" role="dialog" aria-modal="true">
      <div className="document-viewer-shell manual-section-viewer-shell"><header><div>
        <strong>{sectionViewer.title}</strong><small>只读正文浏览 · {sectionStatusLabel(sectionViewer.status)}</small>
      </div><button onClick={() => setSectionViewer(null)}>关闭</button></header>
      <section className="manual-section-reader">{sectionViewer.blocks.map((block, index) =>
        <ReadOnlyBlock block={block} key={`${block.type}-${index}`} />)}</section></div></div>}

    {editor && selectedDocument && <div className="document-viewer manual-editor" role="dialog" aria-modal="true">
      <div className="document-viewer-shell manual-editor-shell"><header><div><strong>编辑说明书正文</strong>
        <small>{selectedDocument.project_name} · 文档 v{selectedDocument.version} · 修改保留历史版本</small></div><div>
        {dirtySections.length > 0 && <span className="editor-dirty-count">{dirtySections.length} 章未保存</span>}
        <button disabled={editorBusy} onClick={closeEditor}>关闭</button></div></header>
        <div className="manual-editor-layout"><nav>{editor.sections.map((section, index) => <button
          className={activeSectionKey === section.section_key ? "active" : ""}
          onClick={() => setActiveSectionKey(section.section_key)} key={section.section_key}>
          <b>{String(index + 1).padStart(2, "0")}</b><span>{section.title}</span>
          <small>{dirtySections.includes(section.section_key) ? "未保存" : sectionStatusLabel(section.status)}</small></button>)}</nav>
          <section className="manual-section-editor">{editor.sections.filter((section) =>
            section.section_key === activeSectionKey).map((section) => <div key={section.section_key}>
              <label>章节标题<input value={section.title} onChange={(event) => updateSection(
                section.section_key, (current) => ({ ...current, title: event.target.value }))} /></label>
              <p className="editor-hint">段落、列表和表格可直接修改；图表请求保持只读，避免正文与可编辑 Draw.io 资产失去关联。</p>
              <div className="manual-block-list">{section.blocks.map((block, index) => <BlockEditor
                block={block} index={index} key={`${block.type}-${index}`} onChange={(next) => updateSection(
                  section.section_key, (current) => ({ ...current, blocks: current.blocks.map(
                    (item, itemIndex) => itemIndex === index ? next : item) }))} />)}</div>
            </div>)}</section></div>
        <footer className="manual-editor-actions"><div><button
          disabled={editorBusy || dirtySections.includes(activeSectionKey)} onClick={regenerateSection}>
          {editorBusy ? "处理中…" : dirtySections.includes(activeSectionKey) ? "先保存本章再让 AI 重写" :
            "AI 重新生成本章"}</button><button className="primary" disabled={editorBusy}
          onClick={saveSection}>保存本章修订</button></div><button className="primary" disabled={editorBusy}
          onClick={assembleRevision}>{figuresDirty ? "保存修改、同步图表并装配" : dirtySections.length ?
            `保存 ${dirtySections.length} 章并装配新版本` : "装配新版本并质检"}</button></footer>
      </div></div>}
  </main>;
}

function ReadOnlyBlock({ block }: { block: ManualSectionBlock }) {
  if (block.type === "subheading") return <h3>{block.title}</h3>;
  if (block.type === "paragraph") return <p>{block.text}</p>;
  if (block.type === "list") return <div>{block.lead && <p>{block.lead}</p>}
    <ul>{block.items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
  if (block.type === "figure_request") return <aside className="manual-section-figure-ref">
    <strong>图表请求 · {block.title}</strong><p>{block.purpose}</p></aside>;
  return <figure><figcaption>{block.title}</figcaption><table><thead><tr>{block.headers.map(
    (header, index) => <th key={index}>{header}</th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) =>
    <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></figure>;
}

function BlockEditor({ block, index, onChange }: { block: ManualSectionBlock; index: number;
  onChange: (block: ManualSectionBlock) => void }) {
  if (block.type === "subheading") return <article className="manual-block-editor"><header>
    <b>小节标题 {index + 1}</b><span>自动编号</span></header><label>标题<input value={block.title}
      onChange={(event) => onChange({ ...block, title: event.target.value })} /></label></article>;
  if (block.type === "figure_request") return <article className="manual-block-editor figure-readonly">
    <header><b>图表 {index + 1}</b><span>保持资产关联</span></header><strong>{block.title}</strong>
    <p>{block.purpose}</p><small>{block.figure_type || "diagram"} · {block.figure_key}</small></article>;
  if (block.type === "paragraph") return <article className="manual-block-editor"><header>
    <b>段落 {index + 1}</b>{block.inference && <span>合理推断</span>}</header><textarea rows={5}
    value={block.text} onChange={(event) => onChange({ ...block, text: event.target.value })} /></article>;
  if (block.type === "list") return <article className="manual-block-editor"><header><b>列表 {index + 1}</b>
    {block.inference && <span>合理推断</span>}</header><label>引导句<input value={block.lead || ""}
      onChange={(event) => onChange({ ...block, lead: event.target.value })} /></label><label>列表项（每行一项）
      <textarea rows={5} value={block.items.join("\n")} onChange={(event) => onChange({ ...block,
        items: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} /></label></article>;
  return <article className="manual-block-editor"><header><b>表格 {index + 1}</b>
    {block.inference && <span>合理推断</span>}</header><label>表名<input value={block.title}
      onChange={(event) => onChange({ ...block, title: event.target.value })} /></label>
    <label>表头（Tab 分列）<input value={block.headers.join("\t")}
      onChange={(event) => onChange({ ...block, headers: event.target.value.split("\t") })} /></label>
    <label>数据（每行一条，Tab 分列）<textarea rows={6} value={block.rows.map((row) => row.join("\t")).join("\n")}
      onChange={(event) => onChange({ ...block, rows: event.target.value.split("\n").filter(Boolean)
        .map((row) => row.split("\t")) })} /></label></article>;
}

function qaCheckLabel(key: string) {
  return ({ "content.section_depth": "章节内容深度", "content.ui_screenshot": "真实界面截图",
    "content.required_sections": "必要章节完整性", "content.figure_coverage": "必要章节图表覆盖",
    "content.placeholders": "待确认内容", "render.page_density": "页面内容密度",
    "content.inference_claims": "AI 推断内容", "content.evidence_coverage": "证据覆盖",
    "content.epistemic_caveats": "推断性措辞",
    "content.unverified_outcomes": "无证据的测试与上线结论",
    "content.internal_source_names": "正文中的内部源文件名",
    "render.blank_pages": "空白页面", "structure.inline_images": "图像装配完整性" } as
    Record<string, string>)[key] || key;
}

function sectionStatusLabel(status: string) {
  return ({ generated: "AI 初稿", confirmed: "人工确认", edited: "已修订",
    failed: "生成失败" } as Record<string, string>)[status] || "已生成";
}

function nearbyManualPages(current: number, total: number): Array<number | null> {
  const values = new Set([1, total, current - 2, current - 1, current, current + 1, current + 2]
    .filter((page) => page >= 1 && page <= total));
  const sorted = Array.from(values).sort((a, b) => a - b);
  const result: Array<number | null> = [];
  sorted.forEach((page, index) => {
    if (index && page - sorted[index - 1] > 1) result.push(null);
    result.push(page);
  });
  return result;
}

function formatBytes(value: number) {
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(2)} MiB`
    : `${Math.max(1, Math.round(value / 1024))} KiB`;
}

async function loadVersions(connection: SidecarConnection, taskId: string) {
  const jobItems = await listFormalManualJobs(connection, taskId);
  const nested = await Promise.all(jobItems.map((job) => listFormalManualDocuments(connection, job.id)
    .catch(() => [] as FormalManualDocument[])));
  const documentItems = nested.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
  return { jobItems, documentItems };
}

function releasePreviewPage(url: string | null) {
  if (url) URL.revokeObjectURL(url);
}

function stepLabel(key: string) {
  return ({ research: "项目研究", draft: "结构化正文", diagrams: "专业图表",
    draft_sections: "结构化正文", render_figures: "专业图表",
    screenshots: "界面截图", screenshot_decisions: "界面截图",
    assemble_docx: "Word 装配", render_qa: "逐页质检" } as Record<string, string>)[key] || key;
}

type ExecutionNode = FormalManualJob["nodes"][number];

function cockpitGroups(job: FormalManualJob) {
  const definitions = [
    ["research", "研究与证据", ["research", "profile"]],
    ["sections", "正文章节", ["section"]],
    ["figures", "章节图表", ["figure"]],
    ["screenshots", "截图证据、解读与审核", ["screenshot", "screenshot_import", "screenshot_analysis", "screenshot_review"]],
    ["documents", "审阅稿与候选稿", ["assemble"]],
    ["qa", "真实渲染与 QA", ["qa"]],
  ] as const;
  return definitions.map(([key, title, kinds]) => ({ key, title,
    nodes: job.nodes.filter((node) => (kinds as readonly string[]).includes(node.kind)) }))
    .filter((group) => group.nodes.length > 0);
}

function cockpitFocusNodes(job: FormalManualJob) {
  const running = job.nodes.filter((node) => node.status === "running");
  const waiting = job.nodes.filter((node) => node.status === "queued").slice(0, 3);
  return [...running, ...waiting].slice(0, 6);
}

function cockpitRecentNodes(job: FormalManualJob) {
  const priority = (node: ExecutionNode) => ["failed", "waiting_for_authorization", "waiting_for_review",
    "waiting_for_screenshots", "outdated",
    "completed_with_warnings"].includes(node.status) ? 2 : node.status === "completed" ? 1 : 0;
  return job.nodes.filter((node) => nodeIsResolved(node.status)).sort((left, right) =>
    priority(right) - priority(left) || Date.parse(right.updated_at) - Date.parse(left.updated_at)
  ).slice(0, 5);
}

function groupStageStatus(nodes: ExecutionNode[]) {
  if (nodes.some((node) => node.status === "running")) return "running";
  if (nodes.some((node) => node.status === "failed")) return "failed";
  if (nodes.some((node) => ["waiting_for_authorization", "waiting_for_review",
    "waiting_for_screenshots", "outdated", "completed_with_warnings"].includes(
    node.status))) return "warning";
  if (nodes.length && nodes.every((node) => nodeIsResolved(node.status))) return "completed";
  return "queued";
}

function nodeIsResolved(status: string) {
  return ["completed", "completed_with_warnings", "failed", "skipped",
    "waiting_for_authorization", "waiting_for_review", "waiting_for_screenshots",
    "adopted", "outdated"].includes(status);
}

function nodeStatusLabel(status: string) {
  return ({ queued: "排队", running: "运行中", completed: "完成",
    completed_with_warnings: "警告", failed: "失败", skipped: "已跳过",
    waiting_for_authorization: "等待授权", waiting_for_review: "等待审核",
    waiting_for_screenshots: "等待截图", adopted: "已采用", outdated: "已过期" } as Record<string, string>)[status] || status;
}

function nodeDurationLabel(node: ExecutionNode, clock: number) {
  if (typeof node.duration_ms === "number" && node.status !== "running") {
    return formatElapsed(node.duration_ms);
  }
  if (!node.started_at) return "尚未开始";
  const end = node.finished_at ? Date.parse(node.finished_at) : clock;
  return formatElapsed(end - Date.parse(node.started_at));
}

function nodeAttemptLabel(node: ExecutionNode) {
  if (node.status === "queued") return node.attempt ? `第 ${node.attempt} 次请求等待并发槽` : "等待并发槽";
  if (node.status === "running") return node.attempt <= 1 ? "首次请求进行中" :
    `第 ${node.attempt} 次请求进行中（已自动重试 ${node.attempt - 1} 次）`;
  if (node.status === "failed") return `已尝试 ${Math.max(1, node.attempt)} 次，可单项重试`;
  if (node.attempt > 1) return `第 ${node.attempt} 次请求成功`;
  return "首次请求成功";
}

function heartbeatLabel(job: FormalManualJob, clock: number) {
  const stamps = [job.updated_at, ...job.nodes.map((node) => node.heartbeat_at || node.updated_at)]
    .map(Date.parse).filter(Number.isFinite);
  const latest = stamps.length ? Math.max(...stamps) : Date.parse(job.updated_at);
  const age = Math.max(0, clock - latest);
  return `${formatElapsed(age)}前${age > 45_000 ? " · 等待返回" : " · 正常"}`;
}

function jobConcurrency(job: FormalManualJob) {
  return Math.max(1, ...job.steps.map((step) => Number(step.summary.concurrency || 1))
    .filter(Number.isFinite));
}

function estimateJobEta(job: FormalManualJob) {
  if (!["queued", "running"].includes(job.status)) return "已结束";
  const completedDurations = job.nodes.map((node) => node.duration_ms)
    .filter((value): value is number => typeof value === "number" && value > 0);
  const remaining = job.nodes.filter((node) => !nodeIsResolved(node.status)).length;
  if (!completedDurations.length || !remaining) return remaining ? "计算中" : "收尾中";
  const average = completedDurations.reduce((sum, value) => sum + value, 0) /
    completedDurations.length;
  return formatElapsed(average * remaining / jobConcurrency(job));
}

function nodeStatusSummary(job: FormalManualJob) {
  const counts = job.progress.node_status_counts || {};
  const parts = [["running", "运行"], ["queued", "排队"],
    ["completed_with_warnings", "警告"], ["failed", "失败"],
    ["waiting_for_authorization", "待授权"]]
    .concat([["waiting_for_review", "待审核"], ["waiting_for_screenshots", "待截图"],
      ["outdated", "已过期"]])
    .filter(([key]) => counts[key]).map(([key, label]) => `${label} ${counts[key]}`);
  return parts.join(" · ") || "全部完成";
}

function dependencyLabel(key: string) {
  if (key.startsWith("section:")) return `章节·${key.slice(8)}`;
  if (key.startsWith("figure:")) return `图表·${key.slice(7)}`;
  return ({ research: "项目研究", review_checkpoint: "正文预览快照（非最终装配）",
    project_profile: "项目概要", screenshot_plan: "实验性自动采集规划", screenshots: "截图审核等待",
    ui_section_update: "用户界面章节更新", ui_document_reassemble: "文档重新装配",
    assemble: "审阅候选稿" } as Record<string, string>)[key] || key;
}

function manualFlowStages(job: FormalManualJob | null, document: FormalManualDocument | null) {
  const nodes = job?.nodes || [];
  const sections = nodes.filter((node) => node.kind === "section" && node.key.startsWith("section:"));
  const figures = nodes.filter((node) => node.kind === "figure");
  const screenshotGate = nodes.find((node) => node.key === "screenshots");
  const assemble = nodes.find((node) => node.key === "assemble" || node.key === "ui_document_reassemble");
  const qa = nodes.find((node) => node.key === "qa" || node.key === "ui_screenshot_qa");
  const resolved = (node: ExecutionNode | undefined) => !!node &&
    ["completed", "completed_with_warnings", "adopted", "skipped"].includes(node.status);
  const active = (items: ExecutionNode[]) => items.some((node) => ["queued", "running"].includes(node.status));
  const blocked = (items: ExecutionNode[]) => items.some((node) => ["failed", "waiting_for_review",
    "waiting_for_screenshots", "waiting_for_authorization", "outdated"].includes(node.status));
  const evidenceNodes = [...figures, ...(screenshotGate ? [screenshotGate] : [])];
  return [
    { title: "确认项目信息", detail: "名称、版本等必填事实；缺少版本时默认 V1.0。", status: "done" },
    { title: "生成并审阅正文", detail: "各章完成后可先看正文预览，但这不是最终稿。",
      status: sections.length && sections.every(resolved) ? "done" : active(sections) ? "active" : blocked(sections) ? "blocked" : "idle" },
    { title: "并行完成图表与截图", detail: "图表需结束；截图需审核采用或明确不适用。",
      status: evidenceNodes.length && evidenceNodes.every(resolved) ? "done" : active(evidenceNodes) ? "active" : blocked(evidenceNodes) ? "blocked" : "idle" },
    { title: "装配审阅候选稿", detail: "只使用闸门通过后的当前正文、图表和截图。",
      status: resolved(assemble) ? "done" : assemble?.status === "running" ? "active" : assemble?.status === "failed" ? "blocked" : "idle" },
    { title: "人工定稿与导出", detail: "逐页质检通过后，由人工点击生成终稿，再导出交付。",
      status: document?.document_kind === "final_document" && document.quality.status === "passed" ? "done" :
        document?.quality.status === "passed" ? "active" : qa?.status === "running" ? "active" :
        document?.quality.status === "failed" || qa?.status === "failed" ? "blocked" : "idle" },
  ];
}

function nodeActionLabel(node: ExecutionNode, documents: FormalManualDocument[], jobId: string,
  jobActive: boolean) {
  if (node.kind === "assemble" && node.status !== "failed" && documents.some((item) =>
    item.job_id === jobId && item.version === Number(node.output.version || 0))) return "查看产物";
  if (node.kind === "screenshot_analysis" && node.status === "failed") return "重试此截图";
  if (["screenshot", "screenshot_import", "screenshot_analysis", "screenshot_review"].includes(node.kind)) return ["waiting_for_authorization", "waiting_for_review", "waiting_for_screenshots", "outdated", "completed_with_warnings", "skipped"]
    .includes(node.status) ? "补截图 / 授权" : node.status === "failed" ? "进入截图页" : null;
  if (node.status === "failed" && ["figure", "section"].includes(node.kind)) {
    return "重试此项";
  }
  if (node.status === "failed" && node.kind === "assemble") return jobActive ? null : "重新装配";
  if (node.status === "failed" && node.kind === "qa") return jobActive ? null : "重跑质检";
  if (node.kind === "figure" && node.status === "completed") return "查看图表";
  if (node.kind === "section" && node.status === "completed") return "查看正文";
  if (node.kind === "qa" && nodeIsResolved(node.status)) return "查看质检";
  return null;
}

function stageProgressLabel(job: FormalManualJob) {
  const completed = job.progress.stage_completed;
  const total = job.progress.stage_total;
  if (typeof completed !== "number" || typeof total !== "number" || total < 1) return "";
  const unit = ["diagrams", "render_figures"].includes(job.current_step) ? "张图" :
    ["draft", "draft_sections"].includes(job.current_step) ? "章" : "项";
  const title = job.progress.current_title ? ` · ${job.progress.current_title}` : "";
  return `（${completed}/${total} ${unit}${title}）`;
}

function runningStepLabel(summary: Record<string, unknown>) {
  const completed = summary.completed_items;
  const total = summary.total_items;
  const title = typeof summary.current_title === "string" ? summary.current_title : "";
  if (typeof completed !== "number" || typeof total !== "number" || total < 1) {
    return title || "正在执行";
  }
  return `${completed}/${total}${title ? ` · ${title}` : ""}`;
}

function formatElapsed(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}

function stepDurationLabel(step: FormalManualJob["steps"][number], clock: number) {
  if (!step.started_at) return "0 秒";
  const end = step.finished_at ? Date.parse(step.finished_at) : clock;
  return formatElapsed(end - Date.parse(step.started_at));
}

type ProgressItem = { key: string; title: string; status: string; attempt: number;
  started_at: string | null; finished_at: string | null; error: string | null };

function progressItems(summary: Record<string, unknown> | undefined): ProgressItem[] {
  if (!summary || !Array.isArray(summary.items)) return [];
  return summary.items.filter((item): item is ProgressItem => !!item && typeof item === "object" &&
    typeof (item as ProgressItem).key === "string" && typeof (item as ProgressItem).title === "string");
}

function jobStatusLabel(job: FormalManualJob) {
  if (!["queued", "running"].includes(job.status) && job.nodes.some((node) =>
    ["failed", "completed_with_warnings", "waiting_for_authorization", "waiting_for_review",
      "waiting_for_screenshots", "outdated"].includes(node.status) ||
    (node.kind === "screenshot" && node.status === "skipped"))) return "已产出，仍有问题";
  return ({ queued: "等待执行", running: "正在生成", completed: "生成完成",
    completed_with_warnings: "生成完成，有提示", failed: "生成失败" } as
    Record<string, string>)[job.status] || job.status;
}
