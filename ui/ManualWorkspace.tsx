import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import {
  exportManualDocument, FormalManualDocument, FormalManualJob, FormalManualPreview,
  generateFormalManual, listFormalManualDocuments, listFormalManualJobs, listModelConfigs,
  loadAppSettings, loadFormalManualImage, loadFormalManualPreview, ModelConfig,
  revealExportedDocument, SidecarConnection,
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
  const [preview, setPreview] = useState<FormalManualPreview | null>(null);
  const [images, setImages] = useState<Record<string, string>>({});
  const [generating, setGenerating] = useState(false);
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
    setJobs([]); setDocuments([]); setSelectedDocument(null); setPreview(null);
    setExportedPath(null); releaseImages(images); setImages({});
    if (!connection || !taskId) return;
    setMessage("正在读取正式说明书版本…");
    loadVersions(connection, taskId).then(({ jobItems, documentItems }) => {
      setJobs(jobItems); setDocuments(documentItems);
      setSelectedDocument(documentItems[0] || null); setMessage("");
    }).catch((error) => setMessage(error instanceof Error ? error.message : "说明书版本读取失败"));
  }, [connection, taskId]);

  useEffect(() => () => releaseImages(images), [images]);

  async function generate() {
    if (!connection || !taskId || !modelId) return;
    setGenerating(true); setPreview(null); setExportedPath(null);
    setMessage("AI 正在研究项目证据、撰写正文并生成图表，完成后将自动装配 Word 文档…");
    try {
      const result = await generateFormalManual(connection, taskId, modelId);
      const versions = await loadVersions(connection, taskId);
      setJobs(versions.jobItems); setDocuments(versions.documentItems);
      setSelectedDocument(result.document);
      setMessage(result.document.qa.warning_count
        ? `正式说明书 v${result.document.version} 已生成，含 ${result.document.qa.warning_count} 项非阻塞提示。`
        : `正式说明书 v${result.document.version} 已生成，可以立即预览或导出。`);
      await openPreview(result.document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "正式说明书生成失败");
    } finally { setGenerating(false); }
  }

  async function openPreview(document = selectedDocument) {
    if (!connection || !document) return;
    setMessage("正在载入说明书内容与图片…");
    try {
      const value = await loadFormalManualPreview(connection, document.job_id, document.version);
      const loaded: Record<string, string> = {};
      await Promise.all([
        ...value.figures.map(async (item) => {
          try { loaded[`figure:${item.figure_key}`] = await loadFormalManualImage(
            connection, document.job_id, "figure", item.figure_key); } catch { /* non-blocking */ }
        }),
        ...value.screenshots.map(async (item) => {
          try { loaded[`screenshot:${item.screenshot_key}`] = await loadFormalManualImage(
            connection, document.job_id, "screenshot", item.screenshot_key); } catch { /* non-blocking */ }
        }),
      ]);
      releaseImages(images); setImages(loaded); setPreview(value); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书预览失败"); }
  }

  async function exportDocument() {
    if (!selectedDocument) return;
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
          <div><button onClick={() => openPreview()}>程序内查看</button><button className="primary"
            onClick={exportedPath ? showExport : exportDocument}>{exportedPath ? "在文件夹中显示" : "导出…"}</button></div>
        </section>}

        {documents.length > 0 && <section className="manual-versions"><header><strong>文档版本</strong>
          <small>{documents.length} 个可用版本</small></header><div>{documents.map((item) => <button
            className={selectedDocument?.id === item.id ? "active" : ""} key={item.id}
            onClick={() => { setSelectedDocument(item); setPreview(null); setExportedPath(null); }}>
            <b>v{item.version}</b><span>{item.created_at.replace("T", " ").slice(0, 16)}</span>
            <em>{item.integrity.status === "verified" ? "完整性已验证" : "文件异常"}</em></button>)}</div></section>}

        {jobs.length > 0 && <details className="manual-advanced"><summary>高级：查看生成阶段留痕</summary>
          <p>阶段状态仅用于进度、失败定位和独立重试，不再要求用户逐项点击。</p>
          <div className="pipeline-history">{jobs.map((job) => <article key={job.id}><header>
            <strong>生成任务 v{job.version}</strong><span>{job.progress.percent}% · {job.status}</span></header>
            <div>{job.steps.map((step) => <span className={step.status} key={step.key}>
              {stepLabel(step.key)} · {step.status}</span>)}</div></article>)}</div></details>}

        <button className="diagram-link" onClick={onOpenDiagrams}>进入图表资产页继续微调可编辑 Draw.io</button>
      </section>}

    {preview && selectedDocument && <div className="document-viewer manual-document-viewer" role="dialog" aria-modal="true">
      <div className="document-viewer-panel"><header><div><strong>{preview.document.project_name} 软件说明书</strong>
        <small>{preview.document.project_version} · 文档 v{preview.document.version}</small></div><div>
        <button onClick={exportedPath ? showExport : exportDocument}>{exportedPath ? "在文件夹中显示" : "导出 DOCX…"}</button>
        <button onClick={() => setPreview(null)}>关闭</button></div></header>
        <div className="manual-preview-body"><article className="manual-preview-paper"><section className="manual-preview-cover">
          <span>软件著作权登记材料</span><h2>{preview.document.project_name}</h2><h3>软件说明书</h3>
          <p>{preview.document.project_version}</p></section>{preview.sections.map((section, index) => <section
          className="manual-preview-section" key={section.section_key}><h2>{index + 1}　{section.title}</h2>
          {section.blocks.map((block, blockIndex) => <PreviewBlock block={block} key={blockIndex}
            figureUrl={block.type === "figure_request" ? images[`figure:${block.figure_key}`] : undefined} />)}
          {preview.screenshots.filter((item) => item.section_key === section.section_key).map((item) => <div
            className="manual-preview-figure" key={item.screenshot_key}>{images[`screenshot:${item.screenshot_key}`] &&
            <img src={images[`screenshot:${item.screenshot_key}`]} alt={item.title} />}<small>界面　{item.title}</small>
            {descriptionLines(item.description).map(([label, value]) => <p className="screenshot-detail" key={label}>
              <b>{label}：</b>{value}</p>)}</div>)}</section>)}</article></div>
      </div></div>}
  </main>;
}

function PreviewBlock({ block, figureUrl }: { block: FormalManualPreview["sections"][number]["blocks"][number];
  figureUrl?: string }) {
  if (block.type === "paragraph") return <p>{block.text}</p>;
  if (block.type === "list") return <div>{block.lead && <p>{block.lead}</p>}<ul>{block.items.map((item) =>
    <li key={item}>{item}</li>)}</ul></div>;
  if (block.type === "table") return <div className="manual-preview-table"><small>表　{block.title}</small><table><thead><tr>
    {block.headers.map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>{block.rows.map((row, index) =>
      <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>;
  return figureUrl ? <div className="manual-preview-figure"><img src={figureUrl} alt={block.title} />
    <small>图　{block.title}</small></div> : <div className="manual-preview-warning">图表尚未生成：{block.title}</div>;
}

async function loadVersions(connection: SidecarConnection, taskId: string) {
  const jobItems = await listFormalManualJobs(connection, taskId);
  const nested = await Promise.all(jobItems.map((job) => listFormalManualDocuments(connection, job.id)
    .catch(() => [] as FormalManualDocument[])));
  const documentItems = nested.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
  return { jobItems, documentItems };
}

function releaseImages(items: Record<string, string>) {
  Object.values(items).forEach((url) => URL.revokeObjectURL(url));
}

function stepLabel(key: string) {
  return ({ research: "项目研究", draft: "结构化正文", diagrams: "专业图表",
    screenshots: "界面截图", assemble_docx: "Word 装配", render_qa: "逐页质检" } as Record<string, string>)[key] || key;
}

function descriptionLines(description: Record<string, string>): Array<[string, string]> {
  const fields: Array<[string, string]> = [["page_purpose", "页面用途"], ["entry_conditions", "进入条件"],
    ["visible_regions", "可见区域与控件"], ["typical_workflow", "典型操作流程"],
    ["backend_interactions", "后台、接口与数据交互"],
    ["result_validation_recovery", "结果、校验与异常恢复"]];
  return fields.map(([key, label]) => [label, description[key]] as [string, string]).filter(([, value]) => !!value);
}
