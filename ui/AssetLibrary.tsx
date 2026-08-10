import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import { exportManualDocument, exportSourceDocument, FormalManualDocument,
  listFormalManualDocuments, listFormalManualJobs, listRecentTasks, loadSourceMaterials, RecentTask,
  revealExportedDocument, SidecarConnection, SourceMaterialsSnapshot } from "./api";

type AssetRow = { task: RecentTask; source: SourceMaterialsSnapshot["source_document"];
  project: SourceMaterialsSnapshot["project"]; manuals: FormalManualDocument[] };

export function AssetLibrary({ connection, onOpen, onPreview, onPreviewManual }: {
  connection: SidecarConnection | null; onOpen: (taskId: string) => void;
  onPreview: (taskId: string) => void; onPreviewManual?: (taskId: string) => void;
}) {
  const [rows, setRows] = useState<AssetRow[]>([]);
  const [message, setMessage] = useState("正在汇总本地产物…");
  const [exported, setExported] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!connection) return;
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
      setRows(loaded); setMessage("");
    }).catch(() => setMessage("资产读取失败，请检查本地服务。"));
  }, [connection]);

  async function exportDoc(row: AssetRow) {
    const destination = await save({ title: "导出源代码文档",
      defaultPath: `${safeFilename(row.project.name)}-${safeFilename(row.project.version || "未标版本")}-源代码文档.docx`,
      filters: [{ name: "Word 文档", extensions: ["docx"] }] });
    if (!destination) return;
    try { await exportSourceDocument(row.task.task_id, destination);
      setExported((current) => ({ ...current, [row.task.task_id]: destination }));
      setMessage(`已导出到 ${destination}`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "导出失败"); }
  }

  async function exportManual(manual: FormalManualDocument) {
    const destination = await save({ title: "导出软件说明书", defaultPath: manual.filename,
      filters: [{ name: "Word 文档", extensions: ["docx"] }] });
    if (!destination) return;
    try { await exportManualDocument(manual.job_id, manual.version, destination);
      setExported((current) => ({ ...current, [`manual:${manual.id}`]: destination }));
      setMessage(`已导出到 ${destination}`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "说明书导出失败"); }
  }

  return <main className="assets-page"><header className="topbar"><div>
    <p className="eyebrow">MY ASSETS</p><h1>我的资产</h1><p>按项目集中查看、预览和导出本地生成材料。</p>
  </div></header><section className="assets-content">
    {message && <div className="source-notice">{message}</div>}
    <div className="asset-library-list">{rows.map((row) => {
      const manual = row.manuals.find((item) => item.status === "qa_passed" &&
        item.freshness.status === "current");
      const staleManual = row.manuals.find((item) => item.status === "qa_passed" &&
        item.freshness.status === "outdated");
      return <article key={row.task.task_id}>
      <div className="asset-project"><span>项目</span><div><strong>{row.task.display_name}</strong>
        <small>{row.task.updated_at.replace("T", " ").slice(0, 16)} · {row.task.task_id.slice(0, 8)}</small></div>
        <button onClick={() => onOpen(row.task.task_id)}>进入项目</button></div>
      <div className={`asset-file ${row.source ? "ready" : "pending"}`}><b>DOCX</b><div>
        <strong>源代码文档</strong><small>{row.source ? `v${row.source.version} · ${row.source.summary.total_pages_expected} 页` : "尚未生成"}</small></div>
        {row.source && <div className="asset-file-actions"><button onClick={() => onPreview(row.task.task_id)}>程序内查看</button>
          <button onClick={() => exported[row.task.task_id]
            ? revealExportedDocument(exported[row.task.task_id]) : exportDoc(row)}>
            {exported[row.task.task_id] ? "在文件夹中显示" : "导出…"}</button></div>}
      </div>
      <div className={`asset-file ${manual ? "ready" : "pending"}`}><b>DOCX</b><div>
        <strong>软件说明书</strong><small>{manual ? `v${manual.version} · ${manual.qa.section_count} 章 · 质量检查通过` : staleManual ?
          `v${staleManual.version} 后内容已有更新，请重新装配` :
          row.manuals.length ? `${row.manuals.length} 个版本，暂无通过质量检查的交付件` : "尚未生成"}</small></div>
        {manual && <div className="asset-file-actions"><button onClick={() => onPreviewManual?.(row.task.task_id)}>程序内查看</button>
          <button onClick={() => exported[`manual:${manual.id}`]
            ? revealExportedDocument(exported[`manual:${manual.id}`]) : exportManual(manual)}>
            {exported[`manual:${manual.id}`] ? "在文件夹中显示" : "导出…"}</button></div>}
        {!manual && staleManual && <div className="asset-file-actions"><button
          onClick={() => onPreviewManual?.(row.task.task_id)}>前往重新装配</button></div>}
      </div>
    </article>;})}</div>
  </section></main>;
}

function safeFilename(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/g, "").trim() || "项目";
}
