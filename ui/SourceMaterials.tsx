import { useEffect, useState } from "react";
import { CodePagePreview, loadCodePagePreview, loadSourceMaterials,
  runSourceMaterialAction, SidecarConnection, SourceMaterialsSnapshot } from "./api";

type Props = { connection: SidecarConnection | null; taskId: string };
type Action = "source-plan" | "code-preview" | "source-docx";

export function SourceMaterials({ connection, taskId }: Props) {
  const [snapshot, setSnapshot] = useState<SourceMaterialsSnapshot | null>(null);
  const [working, setWorking] = useState<Action | null>(null);
  const [message, setMessage] = useState("");
  const [pagePreview, setPagePreview] = useState<CodePagePreview | null>(null);
  const [activePage, setActivePage] = useState(0);
  useEffect(() => {
    setSnapshot(null);
    setPagePreview(null);
    if (!connection || !taskId) return;
    setMessage("正在读取源码材料状态…");
    loadSourceMaterials(connection, taskId).then((value) => {
      setSnapshot(value); setMessage("");
    }).catch((error) => setMessage(error instanceof Error ? error.message : "读取失败"));
  }, [connection, taskId]);

  async function run(action: Action) {
    if (!connection || !taskId) return;
    setWorking(action);
    setMessage({ "source-plan": "正在分析源码并生成 A/B/C 筛选计划…",
      "code-preview": "正在进行 59 页代码分页预检…", "source-docx": "正在生成源代码 DOCX…" }[action]);
    try {
      setSnapshot(await runSourceMaterialAction(connection, taskId, action));
      if (action === "code-preview") setPagePreview(null);
      setMessage("本步已完成，结果已持久化。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setWorking(null); }
  }

  async function openPagePreview() {
    if (!connection || !taskId) return;
    setMessage("正在读取代表性分页…");
    try {
      const value = await loadCodePagePreview(connection, taskId);
      setPagePreview(value); setActivePage(0); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "分页读取失败"); }
  }

  return <main className="source-page">
    <header className="topbar"><div><p className="eyebrow">SOURCE MATERIALS</p><h1>源码材料</h1>
      <p>人工触发每个阶段，先预检页数和阻塞原因，再生成 DOCX。</p></div>
      <span className="task-chip">{taskId ? `任务 ${taskId.slice(0, 8)}…` : "未选择任务"}</span></header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>01</span>
      <h2>请先选择项目</h2><p>到“项目概览”扫描新项目，或从最近任务恢复。</p></section> :
      <section className="source-content">
        {message && <div className={`source-notice ${working ? "working" : ""}`}>{message}</div>}
        <div className="pipeline-grid">
          <Stage index="01" title="源码筛选计划" ready={!!snapshot?.source_plan}
            description="按业务价值分为 A/B/C 级，排除依赖、构建产物和敏感文件。"
            disabled={!snapshot?.actions.source_plan || !!working} onClick={() => run("source-plan")}
            button={snapshot?.source_plan ? "重新生成计划" : "生成筛选计划"} />
          <Stage index="02" title="代码分页预检" ready={!!snapshot?.code_preview}
            description="按可视宽度折行，计算 59 页正文是否足够，不足时不强行凑页。"
            disabled={!snapshot?.actions.code_preview || !!working} onClick={() => run("code-preview")}
            button={snapshot?.code_preview ? "重新分页预检" : "执行分页预检"} />
          <Stage index="03" title="生成源代码 DOCX" ready={!!snapshot?.source_document}
            description="仅当项目信息已确认且代码足够时可生成，文件与摘要写入本地任务目录。"
            disabled={!snapshot?.actions.source_docx || !!working} onClick={() => run("source-docx")}
            button={snapshot?.source_document ? "重新生成 DOCX" : "生成 DOCX"} />
        </div>
        {snapshot && <><div className="material-metrics">
          <Metric label="入选文件" value={snapshot.source_plan?.summary.selected_files ?? "—"} />
          <Metric label="入选代码行" value={snapshot.source_plan?.summary.selected_code_lines ?? "—"} />
          <Metric label="预计正文页" value={snapshot.code_preview?.summary.generated_pages ?? "—"} />
          <Metric label="目标正文页" value={snapshot.code_preview?.summary.target_pages ?? 59} /></div>
          {snapshot.blockers.length > 0 && <div className="blocker-panel"><strong>当前提示</strong>
            {snapshot.blockers.map((item) => <p key={item}>{item}</p>)}</div>}
          {snapshot.code_preview && <div className="page-preview-entry"><div><strong>代码分页 v{snapshot.code_preview.version}</strong>
            <small>已生成 {snapshot.code_preview.summary.generated_pages} 页，可检查首页、中间页和末页的真实内容。</small></div>
            <button onClick={openPagePreview}>{pagePreview ? "刷新分页预览" : "查看分页内容"}</button></div>}
          {pagePreview && <div className="code-preview-panel"><div className="code-preview-head"><div>
            <strong>分页内容预览</strong><small>v{pagePreview.version} · 共 {pagePreview.total_pages} 页</small></div>
            <button onClick={() => setPagePreview(null)}>关闭</button></div>
            {pagePreview.pages.length ? <><div className="page-tabs">{pagePreview.pages.map((page, index) =>
              <button className={activePage === index ? "active" : ""} key={page.page_number}
                onClick={() => setActivePage(index)}>第 {page.page_number} 页</button>)}</div>
              <CodePage page={pagePreview.pages[activePage]} total={pagePreview.total_pages} /></> :
              <div className="no-code-pages">当前预检没有可显示的代码页。</div>}</div>}
          {snapshot.source_plan && <div className="candidate-panel"><div className="section-title">
            <span>入选源码预览</span><em>A {snapshot.source_plan.summary.grades.A} · B {snapshot.source_plan.summary.grades.B} · C {snapshot.source_plan.summary.grades.C}</em></div>
            <div className="candidate-table">{snapshot.source_plan.candidates.map((item) => <div key={item.relative_path}>
              <b className={`grade grade-${item.grade.toLowerCase()}`}>{item.grade}</b>
              <span title={item.relative_path}>{item.relative_path}</span><small>{item.language ?? "未知"}</small>
              <strong>{item.code_lines} 行</strong></div>)}</div></div>}
          {snapshot.source_document && <div className="artifact-panel"><strong>DOCX v{snapshot.source_document.version} 已生成</strong>
            <span>{snapshot.source_document.artifact_relative_path}</span><small>SHA-256 {snapshot.source_document.sha256}</small></div>}
        </>}
      </section>}
  </main>;
}

function Stage(props: { index: string; title: string; description: string; ready: boolean;
  disabled: boolean; button: string; onClick: () => void }) {
  return <article className={`stage-card ${props.ready ? "ready" : ""}`}><span>{props.index}</span>
    <h2>{props.title}</h2><p>{props.description}</p><button disabled={props.disabled}
      onClick={props.onClick}>{props.button}</button></article>;
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return <article><small>{label}</small><strong>{value}</strong></article>;
}
function CodePage({ page, total }: { page: CodePagePreview["pages"][number]; total: number }) {
  return <div className="code-paper"><header><span>源程序代码</span><b>第 {page.page_number} / {total} 页</b></header>
    <div className="code-lines">{page.entries.map((entry, index) => <div
      className={entry.kind === "file_header" ? "file-line" : ""} key={`${index}-${entry.source_line}`}>
      <em>{entry.source_line ?? ""}</em><code>{entry.continuation ? "↳ " : ""}{entry.text || " "}</code></div>)}</div>
    <footer>{page.line_count} 个可视行</footer></div>;
}
