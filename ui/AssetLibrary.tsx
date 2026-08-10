import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import { exportSourceDocument, listRecentTasks, loadSourceMaterials, RecentTask,
  revealSourceDocument, SidecarConnection, SourceMaterialsSnapshot } from "./api";

type AssetRow = { task: RecentTask; source: SourceMaterialsSnapshot["source_document"] };

export function AssetLibrary({ connection, onOpen, onPreview }: {
  connection: SidecarConnection | null; onOpen: (taskId: string) => void;
  onPreview: (taskId: string) => void;
}) {
  const [rows, setRows] = useState<AssetRow[]>([]);
  const [message, setMessage] = useState("正在汇总本地产物…");
  useEffect(() => {
    if (!connection) return;
    listRecentTasks(connection).then(async (tasks) => {
      const loaded = await Promise.all(tasks.map(async (task) => {
        try { return { task, source: (await loadSourceMaterials(connection, task.task_id)).source_document }; }
        catch { return { task, source: null }; }
      }));
      setRows(loaded); setMessage("");
    }).catch(() => setMessage("资产读取失败，请检查本地服务。"));
  }, [connection]);

  async function exportDoc(row: AssetRow) {
    const destination = await save({ title: "导出源代码文档", defaultPath: `${row.task.display_name}-源代码.docx`,
      filters: [{ name: "Word 文档", extensions: ["docx"] }] });
    if (!destination) return;
    try { await exportSourceDocument(row.task.task_id, destination); setMessage(`已导出到 ${destination}`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "导出失败"); }
  }

  return <main className="assets-page"><header className="topbar"><div>
    <p className="eyebrow">MY ASSETS</p><h1>我的资产</h1><p>按项目集中查看、预览和导出本地生成材料。</p>
  </div></header><section className="assets-content">
    {message && <div className="source-notice">{message}</div>}
    <div className="asset-library-list">{rows.map((row) => <article key={row.task.task_id}>
      <div className="asset-project"><span>项目</span><div><strong>{row.task.display_name}</strong>
        <small>{row.task.updated_at.replace("T", " ").slice(0, 16)} · {row.task.task_id.slice(0, 8)}</small></div>
        <button onClick={() => onOpen(row.task.task_id)}>进入项目</button></div>
      <div className={`asset-file ${row.source ? "ready" : "pending"}`}><b>DOCX</b><div>
        <strong>源代码文档</strong><small>{row.source ? `v${row.source.version} · ${row.source.summary.total_pages_expected} 页` : "尚未生成"}</small></div>
        {row.source && <div className="asset-file-actions"><button onClick={() => onPreview(row.task.task_id)}>程序内查看</button>
          <button onClick={() => exportDoc(row)}>导出…</button><button onClick={() => revealSourceDocument(row.task.task_id)}>文件夹</button></div>}
      </div>
      <div className="asset-file pending"><b>DOCX</b><div><strong>软件说明书</strong><small>尚未生成</small></div></div>
    </article>)}</div>
  </section></main>;
}
