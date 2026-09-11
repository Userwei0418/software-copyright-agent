import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useRef, useState } from "react";
import { deleteTask, exportManualDocument, exportSourceDocument, FormalManualDocument,
  listFormalManualDocuments, listFormalManualJobs, listRecentTasks, loadSourceMaterials, RecentTask,
  revealExportedDocument, SidecarConnection, SourceMaterialsSnapshot } from "./api";

type AssetRow = { task: RecentTask; source: SourceMaterialsSnapshot["source_document"];
  project: SourceMaterialsSnapshot["project"]; manuals: FormalManualDocument[] };

export function AssetLibrary({ connection, onOpen, onPreview, onPreviewManual, onDeleted }: {
  connection: SidecarConnection | null; onOpen: (taskId: string) => void;
  onPreview: (taskId: string) => void; onPreviewManual?: (taskId: string) => void;
  onDeleted?: (taskId: string) => void;
}) {
  const [rows, setRows] = useState<AssetRow[]>([]);
  const [message, setMessage] = useState("正在汇总本地产物…");
  const [exported, setExported] = useState<Record<string, string>>({});
  const [deleting, setDeleting] = useState("");
  const [exporting, setExporting] = useState("");
  const operationPending = useRef(false);
  const openManual = onPreviewManual || onOpen;
  useEffect(() => {
    if (!connection) return;
    let disposed = false;
    listRecentTasks(connection).then(async (tasks) => {
      const loaded = await Promise.all(tasks.map(async (task) => {
        try { const [snapshot, jobs] = await Promise.all([
          loadSourceMaterials(connection, task.task_id), listFormalManualJobs(connection, task.task_id),
        ]); const manuals = (await Promise.all(jobs.map((job) =>
          listFormalManualDocuments(connection, job.id).catch(() => [])))).flat()
            .sort((a, b) => b.created_at.localeCompare(a.created_at));
          return { task, source: snapshot.source_document, project: snapshot.project, manuals }; }
        catch { return { task, source: null, project: { name: task.display_name, version: "" }, manuals: [] }; }
      }));
      if (!disposed) { setRows(loaded); setMessage(""); }
    }).catch(() => { if (!disposed) setMessage("资产读取失败，请检查本地服务。"); });
    return () => { disposed = true; };
  }, [connection]);

  async function exportDoc(row: AssetRow) {
    if (operationPending.current || !sourceReady(row.source)) return;
    operationPending.current = true; setExporting(row.task.task_id);
    try {
      const destination = await save({ title: "导出源代码文档",
        defaultPath: `${safeFilename(row.project.name)}-${safeFilename(row.project.version || "未标版本")}-源代码文档.docx`,
        filters: [{ name: "Word 文档", extensions: ["docx"] }] });
      if (!destination) return;
      await exportSourceDocument(row.task.task_id, destination);
      setExported((current) => ({ ...current, [row.task.task_id]: destination }));
      setMessage(`已导出到 ${destination}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "导出失败"); }
    finally { operationPending.current = false; setExporting(""); }
  }

  async function exportManual(manual: FormalManualDocument) {
    if (operationPending.current || !manualReady(manual)) return;
    operationPending.current = true; setExporting(`manual:${manual.id}`);
    const reviewDraft = manual.document_kind !== "final_document";
    try {
      const destination = await save({ title: reviewDraft ? "导出审阅稿" : "导出说明书终稿",
        defaultPath: reviewDraft ? manual.filename.replace(/\.docx$/i, "-审阅稿.docx") : manual.filename,
        filters: [{ name: "Word 文档", extensions: ["docx"] }] });
      if (!destination) return;
      await exportManualDocument(manual.job_id, manual.version, destination, reviewDraft);
      setExported((current) => ({ ...current, [`manual:${manual.id}`]: destination }));
      setMessage(`已导出到 ${destination}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "说明书导出失败"); }
    finally { operationPending.current = false; setExporting(""); }
  }

  async function reveal(path: string) {
    try { await revealExportedDocument(path); }
    catch (error) { setMessage(error instanceof Error ? error.message : "无法定位导出文件"); }
  }

  async function removeProject(row: AssetRow) {
    if (!connection || operationPending.current || !window.confirm(
      `删除项目“${row.task.display_name}”的任务记录和应用内产物？\n原始项目目录不会被删除。`)) return;
    operationPending.current = true; setDeleting(row.task.task_id); setMessage(`正在删除“${row.task.display_name}”…`);
    try {
      await deleteTask(connection, row.task.task_id);
      setRows((current) => current.filter((item) => item.task.task_id !== row.task.task_id));
      setExported((current) => { const next = { ...current }; delete next[row.task.task_id]; return next; });
      onDeleted?.(row.task.task_id);
      setMessage(`“${row.task.display_name}”及应用内产物已删除，原项目文件未受影响。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "项目删除失败");
    } finally { operationPending.current = false; setDeleting(""); }
  }

  return <main className="assets-page"><header className="topbar"><div>
    <p className="eyebrow">MY ASSETS</p><h1>我的资产</h1><p>集中管理项目，并查看、预览和导出本地生成材料。</p>
  </div></header><section className="assets-content">
    {message && <div className="source-notice">{message}</div>}
    <div className="asset-library-list">{rows.map((row) => {
      const manual = row.manuals[0] || null;
      const finalManual = manual?.document_kind === "final_document" && manualReady(manual) ? manual : null;
      const readyManual = manualReady(manual);
      const readySource = sourceReady(row.source);
      const delivered = !!finalManual && readySource;
      return <article key={row.task.task_id}>
      <div className={`asset-project ${delivered ? "completed" : ""}`}><span>项目</span><div><strong>{row.task.display_name}</strong>
        <small>{row.task.updated_at.replace("T", " ").slice(0, 16)} · {row.task.task_id.slice(0, 8)} · {
          delivered ? "双文档可交付" : manual && !readyManual ? "文档待处理" : "尚未完成双文档交付"}</small></div>
        <div className="asset-project-actions"><button onClick={() => onOpen(row.task.task_id)}>进入项目</button>
          <button className="danger" disabled={!!deleting || !!exporting} onClick={() => removeProject(row)}>{
            deleting === row.task.task_id ? "删除中…" : "删除"}</button></div></div>
      <div className={`asset-file ${row.source ? "ready" : "pending"}`}><b>DOCX</b><div>
        <strong>源代码文档</strong><small>{row.source ? `v${row.source.version} · ${row.source.summary.total_pages_expected} 页 · ${
          row.source.integrity.status !== "verified" ? "文件缺失或校验异常，需重新生成" :
          row.source.quality.status === "passed" ? "逐页质检通过" :
          row.source.quality.status === "failed" ? "逐页质检未通过" :
          row.source.quality.status === "outdated" ? "历史标准已过期" : "未逐页质检"}` : "尚未生成"}</small></div>
        {row.source && <div className="asset-file-actions"><button onClick={() => onPreview(row.task.task_id)}>程序内查看</button>
          {readySource ? <button disabled={!!exporting || !!deleting}
            onClick={() => exported[row.task.task_id] ? reveal(exported[row.task.task_id]) : exportDoc(row)}>
            {exporting === row.task.task_id ? "正在导出…" : exported[row.task.task_id] ? "在文件夹中显示" : "导出…"}</button> :
            <button onClick={() => onPreview(row.task.task_id)}>继续处理源码材料</button>}</div>}
      </div>
      <div className={`asset-file ${readyManual ? "ready" : "pending"}`}><b>DOCX</b><div>
        <strong>软件说明书{finalManual && <span className="asset-final-badge">终稿</span>}</strong><small>{manual ?
          `v${manual.version} · ${manual.qa.section_count} 章 · ${manualStatus(manual)}` : "尚未生成"}</small></div>
        {manual && <div className="asset-file-actions"><button className={!readyManual ? "primary" : ""}
          onClick={() => openManual(row.task.task_id)}>{readyManual ? "查看说明书" : "检查并修复说明书"}</button>
          {readyManual && <button disabled={!!exporting || !!deleting} onClick={() => exported[`manual:${manual.id}`]
            ? reveal(exported[`manual:${manual.id}`]) : exportManual(manual)}>
            {exporting === `manual:${manual.id}` ? "正在导出…" : exported[`manual:${manual.id}`] ? "在文件夹中显示" :
              finalManual ? "导出终稿…" : "导出审阅稿…"}</button>}</div>}
      </div>
    </article>;})}{!rows.length && !message && <div className="asset-library-empty">
      <strong>暂无本地产物</strong><p>扫描项目并生成文档后，会在这里统一管理。</p></div>}</div>
  </section></main>;
}

function manualReady(item: FormalManualDocument | null): boolean {
  return !!item && item.quality.status === "passed" && item.freshness.status === "current" &&
    item.integrity.status === "verified";
}

function sourceReady(item: SourceMaterialsSnapshot["source_document"]): boolean {
  return !!item && item.quality.status === "passed" && item.integrity.status === "verified";
}

function manualStatus(item: FormalManualDocument): string {
  if (item.integrity.status !== "verified") return "文件缺失或校验异常，需修复";
  if (item.freshness.status !== "current") return "引用内容已有更新，需重新装配";
  if (item.quality.status === "failed") return "质量检查未通过，待修复";
  if (item.quality.status === "outdated") return "质量标准已更新，需重新装配并检查";
  if (item.quality.status !== "passed") return "尚未完成质量检查";
  return item.document_kind === "final_document" ? "终稿检查通过，可导出" : "审阅稿检查通过，待确认终稿";
}

function safeFilename(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/g, "").trim() || "项目";
}
