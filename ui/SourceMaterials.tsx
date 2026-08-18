import { useEffect, useMemo, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { CodePagePreview, loadCodePagePreview, loadSourceMaterials,
  exportSourceDocument, loadAppSettings, rescanProject, revealExportedDocument,
  runSourceMaterialAction, SidecarConnection, SourceDocumentPreview,
  SourceMaterialsSnapshot, loadSourceDocumentPreview,
  loadSourceDocumentPreviewPage, loadSourceDocumentQaCapability,
  runSourceDocumentQa, SourceDocumentQaCapability } from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

type Props = { connection: SidecarConnection | null; taskId: string;
  onTaskCreated: (taskId: string) => void; onBackToOverview: () => void;
  previewRequested?: number };
type Action = "source-plan" | "code-preview" | "source-docx";

export function SourceMaterials({ connection, taskId, onTaskCreated, onBackToOverview,
  previewRequested = 0 }: Props) {
  const [snapshot, setSnapshot] = useState<SourceMaterialsSnapshot | null>(null);
  const [working, setWorking] = useState<Action | null>(null);
  const [message, setMessage] = useState("");
  const [pagePreview, setPagePreview] = useState<CodePagePreview | null>(null);
  const [previewPageIndex, setPreviewPageIndex] = useState(0);
  const [strategy, setStrategy] = useState<"standard" | "relaxed" | "maximum">("standard");
  const [documentPreview, setDocumentPreview] = useState<SourceDocumentPreview | null>(null);
  const [documentPageNumber, setDocumentPageNumber] = useState(1);
  const [documentPageUrl, setDocumentPageUrl] = useState<string | null>(null);
  const [documentPageLoading, setDocumentPageLoading] = useState(false);
  const [documentPageError, setDocumentPageError] = useState("");
  const [qaCapability, setQaCapability] = useState<SourceDocumentQaCapability | null>(null);
  const [qaWorking, setQaWorking] = useState(false);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const [autoPreview, setAutoPreview] = useState(true);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [candidateGrade, setCandidateGrade] = useState<"all" | "A" | "B" | "C">("all");
  const [candidateLimit, setCandidateLimit] = useState(30);
  useEffect(() => { if (connection) loadAppSettings(connection).then((settings) => {
    setStrategy(settings.source_strategy); setAutoPreview(settings.auto_preview);
  }).catch(() => undefined); }, [connection]);
  useEffect(() => {
    setSnapshot(null);
    setPagePreview(null);
    setDocumentPreview(null);
    setDocumentPageUrl(null);
    setDocumentPageError("");
    setQaCapability(null);
    setExportedPath(null);
    setCandidateQuery(""); setCandidateGrade("all"); setCandidateLimit(30);
    if (!connection || !taskId) return;
    setMessage("正在读取源码材料状态…");
    loadSourceMaterials(connection, taskId).then((value) => {
      setSnapshot(value); setMessage("");
    }).catch((error) => setMessage(error instanceof Error ? error.message : "读取失败"));
    loadSourceDocumentQaCapability(connection, taskId).then(setQaCapability)
      .catch(() => setQaCapability(null));
  }, [connection, taskId]);

  async function run(action: Action) {
    if (!connection || !taskId) return;
    setWorking(action);
    setMessage({ "source-plan": "正在分析源码并生成 A/B/C 筛选计划…",
      "code-preview": "正在进行 59 页代码分页预检…", "source-docx": "正在生成源代码 DOCX…" }[action]);
    try {
      let value = await runSourceMaterialAction(connection, taskId, action,
        action === "source-plan" ? strategy : undefined);
      setSnapshot(value);
      if (action === "code-preview") setPagePreview(null);
      if (action === "source-docx") {
        if (qaCapability?.available) {
          setQaWorking(true);
          setMessage("DOCX 已生成，正在用 Word 兼容引擎真实渲染 60 页并逐页检查…");
          value = await runSourceDocumentQa(connection, taskId);
          setSnapshot(value);
          if (value.source_document?.quality.status === "passed") {
            setMessage("DOCX 已生成并通过真实逐页质检，可以查看或导出。");
            if (autoPreview) await openDocumentPreview();
          } else {
            setMessage("DOCX 已生成，但逐页质检未通过；请查看真实预览后重新生成。");
          }
        } else {
          setMessage(`DOCX 已生成；${qaCapability?.message || "当前设备无法执行真实逐页质检"}。`);
        }
      } else setMessage("本步已完成，结果已持久化。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setWorking(null); setQaWorking(false); }
  }

  async function runQualityCheck() {
    if (!connection || !taskId || !qaCapability?.available) return;
    setQaWorking(true);
    setMessage("正在真实渲染 DOCX 并逐页检查，通常需要几十秒，请勿退出应用…");
    try {
      const value = await runSourceDocumentQa(connection, taskId);
      setSnapshot(value);
      if (value.source_document?.quality.status === "passed") {
        setMessage("真实逐页质检通过，现在可以查看和导出。");
        if (autoPreview) await openDocumentPreview();
      } else setMessage("逐页质检未通过，请在真实预览中检查问题页后重新生成 DOCX。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "逐页质检失败");
    } finally { setQaWorking(false); }
  }

  async function openDocumentPreview() {
    if (!connection || !taskId) return;
    setMessage("正在载入真实 DOCX 渲染页…");
    try {
      const preview = await loadSourceDocumentPreview(connection, taskId);
      setDocumentPreview(preview);
      await openDocumentPage(preview.pages[0]);
      setMessage("");
    } catch (error) {
      setDocumentPreview(null);
      setMessage(error instanceof Error ? error.message : "真实 DOCX 预览失败");
    }
  }

  async function openDocumentPage(pageNumber: number) {
    if (!connection || !taskId) return;
    setDocumentPageNumber(pageNumber);
    setDocumentPageLoading(true);
    setDocumentPageError("");
    try {
      const url = await loadSourceDocumentPreviewPage(connection, taskId, pageNumber);
      setDocumentPageUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return url; });
    } catch (error) {
      setDocumentPageUrl(null);
      setDocumentPageError(error instanceof Error ? error.message : "真实 DOCX 预览页读取失败");
    } finally { setDocumentPageLoading(false); }
  }

  function closeDocumentPreview() {
    if (documentPageUrl) URL.revokeObjectURL(documentPageUrl);
    setDocumentPreview(null);
    setDocumentPageUrl(null);
    setDocumentPageError("");
  }

  useEffect(() => () => { if (documentPageUrl) URL.revokeObjectURL(documentPageUrl); }, []);

  useEffect(() => { if (previewRequested && connection && taskId) openDocumentPreview(); },
    [previewRequested]);

  async function exportDocument() {
    if (!snapshot?.source_document) return;
    const project = safeFilename(snapshot.project.name || "项目");
    const version = safeFilename(snapshot.project.version || "未标版本");
    const destination = await save({ title: "导出源代码文档",
      defaultPath: `${project}-${version}-源代码文档.docx`,
      filters: [{ name: "Word 文档", extensions: ["docx"] }] });
    if (!destination) return;
    try { await exportSourceDocument(taskId, destination); setExportedPath(destination);
      setMessage(`文档已导出到 ${destination}`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "DOCX 导出失败"); }
  }

  async function showExport() {
    if (!exportedPath) return;
    try { await revealExportedDocument(exportedPath); }
    catch (error) { setMessage(error instanceof Error ? error.message : "导出文件定位失败"); }
  }

  async function openPagePreview() {
    if (!connection || !taskId) return;
    setMessage("正在读取代表性分页…");
    try {
      const value = await loadCodePagePreview(connection, taskId);
      setPagePreview(value); setPreviewPageIndex(0); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "分页读取失败"); }
  }

  async function rescan() {
    if (!connection || !taskId) return;
    setMessage("正在重新扫描当前项目…");
    setWorking("source-plan");
    try {
      const result = await rescanProject(connection, taskId);
      onTaskCreated(result.task_id);
      setMessage(`重新扫描完成 · 新任务 ${result.task_id.slice(0, 8)}…`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "重新扫描失败"); }
    finally { setWorking(null); }
  }

  const filteredCandidates = useMemo(() => {
    const normalized = candidateQuery.trim().toLowerCase();
    return (snapshot?.source_plan?.candidates ?? []).filter((item) =>
      (candidateGrade === "all" || item.grade === candidateGrade) &&
      (!normalized || item.relative_path.toLowerCase().includes(normalized) ||
        (item.language ?? "").toLowerCase().includes(normalized)));
  }, [snapshot?.source_plan?.candidates, candidateGrade, candidateQuery]);
  const visibleCandidates = filteredCandidates.slice(0, candidateLimit);
  const sourceDocumentRetryable = snapshot?.task.status === "failed" &&
    snapshot.task.failure_category === "source_document_error";

  return <main className="source-page">
    <header className="topbar"><div><p className="eyebrow">SOURCE MATERIALS</p><h1>源码材料</h1>
      <p>人工触发每个阶段，先预检页数和阻塞原因，再生成 DOCX。</p></div>
      <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskCreated} /></header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>01</span>
      <h2>请先选择项目</h2><p>到“项目概览”扫描新项目，或从最近任务恢复。</p></section> :
      <section className="source-content">
        {message && <div className={`source-notice ${working ? "working" : ""}`}>{message}</div>}
        {sourceDocumentRetryable && <div className="source-retry-panel"><div>
          <strong>源代码文档生成未完成</strong>
          <span>{snapshot?.task.safe_error_message || "已保留分页结果，可直接重试当前步骤。"}</span>
        </div><button className="primary"
          disabled={!snapshot?.actions.source_docx || !!working || qaWorking}
          onClick={() => run("source-docx")}>
          {working === "source-docx" ? "正在重试…" : "重试生成 DOCX"}
        </button></div>}
        {snapshot?.source_document && <div className="artifact-success"><div><span>✓</span><div>
          <strong>源代码文档已生成</strong><small>DOCX v{snapshot.source_document.version} · {
            snapshot.source_document.quality.status === "passed" ?
              `逐页质量检查通过（${snapshot.source_document.quality.summary?.rendered_pages || 60} 页）` :
            snapshot.source_document.quality.status === "failed" ? "逐页质量检查未通过，请重新生成" :
            snapshot.source_document.quality.status === "outdated" ? "生成规则或质检标准已升级，请重新生成" :
              "尚未执行逐页质量检查"}</small>
        </div></div><div>{snapshot.source_document.quality.status === "not_checked" && <button
          disabled={!qaCapability?.available || qaWorking} onClick={runQualityCheck}>
          {qaWorking ? "正在逐页质检…" : qaCapability?.available ? "执行逐页质检" : "缺少渲染组件"}</button>}
          <button disabled={snapshot.source_document.quality.status === "not_checked" || qaWorking}
            onClick={openDocumentPreview}>程序内查看</button>
          <button className="primary" disabled={snapshot.source_document.quality.status !== "passed" || qaWorking}
            onClick={exportedPath ? showExport : exportDocument}>
            {snapshot.source_document.quality.status === "failed" ? "质检未通过" :
              snapshot.source_document.quality.status === "outdated" ? "按新标准重新生成" :
              snapshot.source_document.quality.status === "not_checked" ? "质检后导出" :
              exportedPath ? "在文件夹中显示" : "导出…"}</button></div></div>}
        {snapshot?.source_document?.quality.status === "outdated" && <div className="manual-stale-notice">
          当前文件来自旧版 Word 生成器或旧版质检标准，只作为历史版本保留。请点击“生成源代码 DOCX”生成新版本，
          系统随后会自动按当前标准逐页检查。</div>}
        {snapshot?.source_document?.quality.status === "not_checked" && qaCapability &&
          <div className={`qa-capability-note ${qaCapability.available ? "available" : "unavailable"}`}>
            <strong>{qaCapability.available ? "可执行真实逐页质检" : "尚不能执行真实逐页质检"}</strong>
            <span>{qaCapability.message}。未质检版本不会被标记为可交付，也不能导出。</span></div>}
        <div className="pipeline-grid">
          <Stage index="01" title="源码筛选计划" ready={!!snapshot?.source_plan}
            description="按业务价值分为 A/B/C 级，排除依赖、构建产物和敏感文件。"
            disabled={!snapshot?.actions.source_plan || !!working || qaWorking} onClick={() => run("source-plan")}
            button={snapshot?.source_plan ? "重新生成计划" : "生成筛选计划"} />
          <Stage index="02" title="代码分页预检" ready={!!snapshot?.code_preview}
            description="按前端页面、组件、接口、后端服务与数据层轮转取样，再按可视宽度分成 59 页。"
            disabled={!snapshot?.actions.code_preview || !!working || qaWorking} onClick={() => run("code-preview")}
            button={snapshot?.code_preview ? "重新分页预检" : "执行分页预检"} />
          <Stage index="03" title="生成源代码 DOCX" ready={!!snapshot?.source_document}
            description="仅当项目信息已确认且代码足够时可生成，文件与摘要写入本地任务目录。"
            disabled={!snapshot?.actions.source_docx || !!working || qaWorking} onClick={() => run("source-docx")}
            button={sourceDocumentRetryable ? "重试生成 DOCX" :
              snapshot?.source_document ? "重新生成 DOCX" : "生成 DOCX"} />
        </div>
        <div className="strategy-panel"><div><strong>源码筛选严格度</strong>
          <small>这是确定性取样：项目快照、严格度与分页规则都未变化时，重新生成会得到相同的源码正文，便于复核；源码有变动需先重新扫描项目。</small></div>
          <div>{(["standard", "relaxed", "maximum"] as const).map((value) => <button
            className={strategy === value ? "active" : ""} onClick={() => setStrategy(value)} key={value}>
            {{ standard: "标准", relaxed: "宽松", maximum: "最大覆盖" }[value]}</button>)}</div></div>
        {snapshot && <><div className="material-metrics">
          <Metric label="入选文件" value={snapshot.source_plan?.summary.selected_files ?? "—"} />
          <Metric label="实际取样文件" value={snapshot.code_preview?.summary.included_files ?? "—"} />
          <Metric label="覆盖工程层次" value={snapshot.code_preview?.summary.included_buckets?.length ?? "—"} />
          <Metric label="覆盖语言" value={snapshot.code_preview?.summary.included_languages?.length ?? "—"} />
          <Metric label="预计正文页" value={snapshot.code_preview?.summary.generated_pages ?? "—"} />
          <Metric label="目标正文页" value={snapshot.code_preview?.summary.target_pages ?? 59} /></div>
          {snapshot.blockers.length > 0 && <div className="blocker-panel"><strong>当前提示</strong>
            {snapshot.blockers.map((item) => <p key={item}>{item}</p>)}
            {snapshot.code_preview && !snapshot.code_preview.summary.sufficient &&
              <div className="blocker-actions"><button disabled={!!working} onClick={rescan}>
                重新扫描当前项目</button><button className="secondary" onClick={onBackToOverview}>
                返回项目概览</button></div>}</div>}
          {snapshot.code_preview && !snapshot.code_preview.summary.sufficient &&
            snapshot.source_plan?.summary.strategy === "maximum" && <div className="eligibility-warning">
              <strong>三档策略仍不足</strong><p>当前项目在排除敏感、二进制和生成代码后仍无法满足 59 页，暂不建议继续生成软著材料。</p></div>}
          {snapshot.code_preview && <div className="page-preview-entry"><div><strong>代码分页 v{snapshot.code_preview.version}</strong>
            <small>已生成 {snapshot.code_preview.summary.generated_pages} 页 · 取样 {snapshot.code_preview.summary.included_files} 个文件
              {snapshot.code_preview.summary.included_buckets?.length ? ` · ${snapshot.code_preview.summary.included_buckets.length} 个工程层次` : ""}
              {snapshot.code_preview.summary.included_languages?.length ? ` · ${snapshot.code_preview.summary.included_languages.join(" / ")}` : ""}。</small></div>
            <button onClick={openPagePreview}>{pagePreview ? "刷新分页预览" : "查看分页内容"}</button></div>}
          {pagePreview && <div className="code-preview-panel"><div className="code-preview-head"><div>
            <strong>分页内容预览</strong><small>v{pagePreview.version} · 共 {pagePreview.total_pages} 页</small></div>
            <button onClick={() => setPagePreview(null)}>关闭</button></div>
            {pagePreview.pages.length ? <><div className="page-tabs">{pagePreview.pages.map((page, index) =>
              <button className={previewPageIndex === index ? "active" : ""} key={page.page_number}
                onClick={() => setPreviewPageIndex(index)}>第 {page.page_number} 页</button>)}</div>
              <CodePage page={pagePreview.pages[previewPageIndex]} total={pagePreview.total_pages} /></> :
              <div className="no-code-pages">当前预检没有可显示的代码页。</div>}</div>}
          {snapshot.source_plan && <div className="candidate-panel"><div className="section-title">
            <span>入选源码</span><em>共 {snapshot.source_plan.candidates.length} 个 · A {snapshot.source_plan.summary.grades.A} · B {snapshot.source_plan.summary.grades.B} · C {snapshot.source_plan.summary.grades.C}</em></div>
            <div className="candidate-tools"><input value={candidateQuery}
              onChange={(event) => { setCandidateQuery(event.target.value); setCandidateLimit(30); }}
              placeholder="搜索文件路径或语言" aria-label="搜索入选源码" />
              <div>{(["all", "A", "B", "C"] as const).map((grade) => <button key={grade}
                className={candidateGrade === grade ? "active" : ""}
                onClick={() => { setCandidateGrade(grade); setCandidateLimit(30); }}>
                {grade === "all" ? "全部" : `${grade} 级`}</button>)}</div></div>
            <div className="candidate-table">{visibleCandidates.map((item) => <div key={item.relative_path}>
              <b className={`grade grade-${item.grade.toLowerCase()}`}>{item.grade}</b>
              <span title={item.relative_path}>{item.relative_path}</span><small>{item.language ?? "未知"}</small>
              <strong>{item.code_lines} 行</strong></div>)}</div>
            {filteredCandidates.length === 0 && <div className="candidate-empty">没有匹配的源码文件。</div>}
            {candidateLimit < filteredCandidates.length && <button className="candidate-more"
              onClick={() => setCandidateLimit((value) => value + 30)}>
              再显示 30 个（剩余 {filteredCandidates.length - candidateLimit}）</button>}</div>}
        </>}
      </section>}
    {documentPreview && <div className="document-viewer" role="dialog" aria-modal="true">
      <div className="document-viewer-shell"><header><div><strong>源代码文档预览</strong>
        <small>DOCX v{documentPreview.version} · 真实渲染 {documentPreview.total_pages} 页 · {
          documentPreview.quality_status === "passed" ? "逐页质检通过" :
          documentPreview.quality_status === "outdated" ? "历史标准曾通过，当前标准已升级" : "逐页质检未通过"}</small></div><div>
          <button disabled={documentPreview.quality_status !== "passed"}
            onClick={exportedPath ? showExport : exportDocument}>
            {documentPreview.quality_status === "outdated" ? "重新生成后导出" :
              exportedPath ? "在文件夹中显示" : "导出 DOCX…"}</button><button onClick={closeDocumentPreview}>关闭</button>
        </div></header><div className="document-viewer-body"><aside className="source-page-nav">
          <label>跳转页码<input aria-label="源代码文档跳转页码" type="number" min={1}
            max={documentPreview.total_pages} value={documentPageNumber}
            onChange={(event) => { const page = Number(event.target.value);
              if (page >= 1 && page <= documentPreview.total_pages) openDocumentPage(page); }} /></label>
          <small>快速页码</small><div>{nearbyPages(documentPageNumber, documentPreview.total_pages).map((page, index) =>
            page === null ? <span key={`gap-${index}`}>…</span> : <button
              className={documentPageNumber === page ? "active" : ""}
              onClick={() => openDocumentPage(page)} key={page}>{page}</button>)}</div>
          <p>可用页码框直接跳转，不必滚动 60 个按钮。</p>
        </aside><section className="source-docx-preview"><div className="source-docx-pager">
            <button disabled={documentPageNumber <= documentPreview.pages[0] || documentPageLoading}
              onClick={() => openDocumentPage(documentPageNumber - 1)}>上一页</button>
            <strong>第 {documentPageNumber} / {documentPreview.total_pages} 页</strong>
            <button disabled={documentPageNumber >= documentPreview.pages[documentPreview.pages.length - 1] || documentPageLoading}
              onClick={() => openDocumentPage(documentPageNumber + 1)}>下一页</button></div>
          {documentPageLoading && <div className="source-docx-loading">正在读取真实渲染页…</div>}
          {!documentPageLoading && documentPageError && <div className="source-docx-error"><strong>本页加载失败</strong>
            <p>{documentPageError}</p><button onClick={() => openDocumentPage(documentPageNumber)}>重新加载本页</button></div>}
          {!documentPageLoading && documentPageUrl && <img src={documentPageUrl}
            alt={`源代码文档第 ${documentPageNumber} 页真实渲染`} />}</section>
        </div></div></div>}
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
function safeFilename(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/g, "").trim() || "项目";
}

function nearbyPages(current: number, total: number): Array<number | null> {
  const pages = new Set([1, total, current - 2, current - 1, current, current + 1, current + 2]);
  const valid = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result: Array<number | null> = [];
  valid.forEach((page, index) => {
    if (index > 0 && page - valid[index - 1] > 1) result.push(null);
    result.push(page);
  });
  return result;
}
