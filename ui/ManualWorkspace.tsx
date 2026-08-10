import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import {
  exportManualDocument, FormalManualDocument, FormalManualJob, FormalManualQa,
  generateFormalManual, listFormalManualDocuments, listFormalManualJobs, listModelConfigs,
  loadAppSettings, loadFormalManualQa, loadFormalManualQaPage, ModelConfig,
  revealExportedDocument, runFormalManualQa, SidecarConnection,
} from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

export function ManualWorkspace({ connection, taskId, onTaskChange, onOpenDiagrams }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (taskId: string) => void;
  onOpenDiagrams: () => void;
}) {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [modelId, setModelId] = useState("");
  const [jobs, setJobs] = useState<FormalManualJob[]>([]);
  const [documents, setDocuments] = useState<FormalManualDocument[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<FormalManualDocument | null>(null);
  const [quality, setQuality] = useState<FormalManualQa | null>(null);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewPageUrl, setPreviewPageUrl] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState("");
  const [exportedPath, setExportedPath] = useState<string | null>(null);

  useEffect(() => {
    if (!connection) return;
    Promise.all([listModelConfigs(connection), loadAppSettings(connection)]).then(([items, settings]) => {
      const available = items.filter((item) => item.enabled && !!item.verified_at);
      setModels(available);
      setModelId(available.some((item) => item.id === settings.manual_model_id)
        ? settings.manual_model_id || "" : available[0]?.id || "");
    }).catch(() => setModels([]));
  }, [connection]);

  useEffect(() => {
    setJobs([]); setDocuments([]); setSelectedDocument(null); setQuality(null);
    setExportedPath(null); releasePreviewPage(previewPageUrl); setPreviewPageUrl(null);
    if (!connection || !taskId) return;
    setMessage("正在读取正式说明书版本…");
    loadVersions(connection, taskId).then(({ jobItems, documentItems }) => {
      setJobs(jobItems); setDocuments(documentItems);
      setSelectedDocument(documentItems[0] || null); setMessage("");
    }).catch((error) => setMessage(error instanceof Error ? error.message : "说明书版本读取失败"));
  }, [connection, taskId]);

  useEffect(() => () => releasePreviewPage(previewPageUrl), [previewPageUrl]);

  async function generate() {
    if (!connection || !taskId || !modelId) return;
    setGenerating(true); setQuality(null); setExportedPath(null);
    setMessage("AI 正在研究证据、撰写正文和生成图表，随后将装配 Word 并逐页质检…");
    try {
      const result = await generateFormalManual(connection, taskId, modelId);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document);
      setMessage(result.quality.passed
        ? `正式说明书 v${result.document.version} 已生成并通过 ${result.quality.page_count} 页质量检查。`
        : `正式说明书 v${result.document.version} 已生成，但未通过质量检查，请查看检查项。`);
      await openPreview(result.document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "正式说明书生成失败");
    } finally { setGenerating(false); }
  }

  async function openPreview(document = selectedDocument) {
    if (!connection || !document) return;
    setMessage("正在载入逐页质量检查预览…");
    try {
      const value = await loadFormalManualQa(connection, document.job_id, document.version);
      const url = await loadFormalManualQaPage(connection, document.job_id, document.version, 1);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(1);
      setQuality(value); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书预览失败"); }
  }

  async function changePreviewPage(page: number) {
    if (!connection || !selectedDocument || !quality || page < 1 || page > quality.page_count) return;
    setMessage(`正在载入第 ${page} 页…`);
    try {
      const url = await loadFormalManualQaPage(
        connection, selectedDocument.job_id, selectedDocument.version, page);
      releasePreviewPage(previewPageUrl); setPreviewPageUrl(url); setPreviewPage(page); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书预览页读取失败"); }
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
      setMessage(result.qa_run.passed ? "逐页质量检查通过，可以导出。" :
        "质量检查未通过，请查看检查结果后重新生成或修订。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书质量检查失败"); }
    finally { setChecking(false); }
  }

  async function exportDocument() {
    if (!selectedDocument || selectedDocument.status !== "qa_passed") return;
    const destination = await save({ title: "导出软件说明书",
      defaultPath: selectedDocument.filename,
      filters: [{ name: "Word 文档", extensions: ["docx"] }] });
    if (!destination) return;
    try {
      await exportManualDocument(selectedDocument.job_id, selectedDocument.version, destination);
      setExportedPath(destination); setMessage(`说明书已导出到 ${destination}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书导出失败"); }
  }

  async function showExport() {
    if (!exportedPath) return;
    try { await revealExportedDocument(exportedPath); }
    catch (error) { setMessage(error instanceof Error ? error.message : "导出文件定位失败"); }
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
          <button disabled={!modelId || generating} onClick={generate}>{generating ? "正在生成正式文档…" :
            !modelId ? "请先验证可用模型" : documents.length ? "生成新版本" : "生成正式说明书"}</button></div>

        {selectedDocument && <section className="manual-result"><div><span>DOCX</span><div>
          <strong>{selectedDocument.filename}</strong><small>文档 v{selectedDocument.version} · {
            selectedDocument.qa.section_count} 章 · {selectedDocument.qa.figure_count} 张图表 · {
            selectedDocument.qa.screenshot_count} 张截图 · {(selectedDocument.integrity.size_bytes || 0) / 1024 / 1024 < 0.1
              ? `${Math.round((selectedDocument.integrity.size_bytes || 0) / 1024)} KiB`
              : `${((selectedDocument.integrity.size_bytes || 0) / 1024 / 1024).toFixed(2)} MiB`}</small></div></div>
          <div><button disabled={checking} onClick={selectedDocument.status === "assembled"
            ? runQualityCheck : () => openPreview()}>{checking ? "正在质检…" :
              selectedDocument.status === "assembled" ? "执行逐页质检" : "逐页预览"}</button><button className="primary"
            disabled={selectedDocument.status !== "qa_passed"}
            onClick={exportedPath ? showExport : exportDocument}>{exportedPath ? "在文件夹中显示" :
              selectedDocument.status === "qa_passed" ? "导出…" : "质检通过后可导出"}</button></div>
        </section>}

        {documents.length > 0 && <section className="manual-versions"><header><strong>文档版本</strong>
          <small>{documents.length} 个可用版本</small></header><div>{documents.map((item) => <button
            className={selectedDocument?.id === item.id ? "active" : ""} key={item.id}
            onClick={() => { setSelectedDocument(item); setQuality(null); setExportedPath(null); }}>
            <b>v{item.version}</b><span>{item.created_at.replace("T", " ").slice(0, 16)}</span>
            <em>{item.integrity.status !== "verified" ? "文件异常" : item.status === "qa_passed" ?
              "质量检查通过" : item.status === "qa_failed" ? "质量检查未通过" : "待质量检查"}</em></button>)}</div></section>}

        {jobs.length > 0 && <details className="manual-advanced"><summary>高级：查看生成阶段留痕</summary>
          <p>阶段状态仅用于进度、失败定位和独立重试，不再要求用户逐项点击。</p>
          <div className="pipeline-history">{jobs.map((job) => <article key={job.id}><header>
            <strong>生成任务 v{job.version}</strong><span>{job.progress.percent}% · {job.status}</span></header>
            <div>{job.steps.map((step) => <span className={step.status} key={step.key}>
              {stepLabel(step.key)} · {step.status}</span>)}</div></article>)}</div></details>}

        <button className="diagram-link" onClick={onOpenDiagrams}>进入图表资产页继续微调可编辑 Draw.io</button>
      </section>}

    {quality && selectedDocument && <div className="document-viewer manual-document-viewer" role="dialog" aria-modal="true">
      <div className="document-viewer-panel"><header><div><strong>{selectedDocument.project_name} 软件说明书</strong>
        <small>{selectedDocument.project_version} · 文档 v{selectedDocument.version} · {
          quality.passed ? "质量检查通过" : "质量检查未通过"}</small></div><div>
        <button disabled={selectedDocument.status !== "qa_passed"}
          onClick={exportedPath ? showExport : exportDocument}>{exportedPath ? "在文件夹中显示" : "导出 DOCX…"}</button>
        <button onClick={() => setQuality(null)}>关闭</button></div></header>
        <div className="manual-page-toolbar"><button disabled={previewPage <= 1}
          onClick={() => changePreviewPage(previewPage - 1)}>上一页</button><span>第 {previewPage} / {
            quality.page_count} 页</span><button disabled={previewPage >= quality.page_count}
          onClick={() => changePreviewPage(previewPage + 1)}>下一页</button></div>
        <div className="manual-preview-body">{previewPageUrl && <img className="manual-qa-page"
          src={previewPageUrl} alt={`说明书第 ${previewPage} 页`} />}</div>
        <footer className="manual-render-disclosure">{quality.summary.renderer_disclosure}</footer>
      </div></div>}
  </main>;
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
    screenshots: "界面截图", assemble_docx: "Word 装配", render_qa: "逐页质检" } as Record<string, string>)[key] || key;
}
