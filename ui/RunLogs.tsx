import { save } from "@tauri-apps/plugin-dialog";
import { useCallback, useEffect, useState } from "react";
import {
  exportRunDiagnostics, loadRunDiagnostics, RunDiagnosticsBundle,
  RunDiagnosticsItem, RunDiagnosticsStage, SidecarConnection,
} from "./api";
import "./run-logs.css";

type Props = { connection: SidecarConnection | null };

export function RunLogs({ connection }: Props) {
  const [limit, setLimit] = useState(5);
  const [bundle, setBundle] = useState<RunDiagnosticsBundle | null>(null);
  const [notice, setNotice] = useState("正在读取最近运行记录…");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!connection) return;
    try {
      const result = await loadRunDiagnostics(connection, limit);
      setBundle(result);
      setNotice(result.run_count ? `已同步最近 ${result.run_count} 次快速任务` : "还没有快速任务日志");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "运行日志读取失败");
    }
  }, [connection, limit]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function exportLogs() {
    const destination = await save({
      title: "导出运行诊断日志",
      defaultPath: `软著材料助手-最近${limit}次运行日志.json`,
      filters: [{ name: "JSON", extensions: ["json"] }],
    });
    if (!destination) return;
    setBusy(true);
    try {
      const result = await exportRunDiagnostics(limit, destination);
      setNotice(`诊断包已导出 · ${formatBytes(result.sizeBytes)}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "运行日志导出失败");
    } finally { setBusy(false); }
  }

  const runs = bundle?.runs || [];
  const completed = runs.filter((run) => run.status === "completed").length;
  const unresolved = runs.filter((run) => run.status === "failed" || run.status === "waiting_for_user").length;
  const recovered = runs.filter((run) => run.status === "completed" && hadRecovery(run)).length;

  return <main className="content run-logs-page">
    <header className="run-logs-header">
      <div className="run-logs-title">
        <span className="eyebrow">RUN DIAGNOSTICS</span>
        <h1>运行日志</h1>
        <p>查看任务最终状态、恢复轨迹与阻塞原因；需要协助时可导出脱敏诊断包。</p>
      </div>
      <div className="run-log-actions">
        <label><span>范围</span><select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
          {[3, 5, 10, 20].map((value) => <option key={value} value={value}>最近 {value} 次</option>)}
        </select></label>
        <button onClick={() => void refresh()}>刷新</button>
        <button className="primary" disabled={busy || !bundle?.run_count}
          onClick={() => void exportLogs()}>{busy ? "正在导出…" : "导出诊断包"}</button>
      </div>
    </header>

    <section className="run-log-overview" aria-label="日志概览">
      <p className="run-log-notice"><i />{notice}</p>
      <div className="run-log-overview-stats">
        <span><b>{runs.length}</b><small>已载入</small></span>
        <span className="ok"><b>{completed}</b><small>已交付</small></span>
        <span className={unresolved ? "warning" : ""}><b>{unresolved}</b><small>待处理</small></span>
        <span><b>{recovered}</b><small>自动恢复</small></span>
      </div>
    </section>

    <section className="run-log-list">
      {runs.map((run, index) => <RunCard key={run.id} run={run} latest={index === 0} />)}
      {!runs.length && bundle && <div className="run-log-empty">
        <strong>暂无快速任务日志</strong><span>启动一次快速任务后，这里会显示可追溯的执行记录。</span>
      </div>}
    </section>
  </main>;
}

function RunCard({ run, latest }: { run: RunDiagnosticsItem; latest: boolean }) {
  const [expanded, setExpanded] = useState(latest);
  const failed = run.stages.find((stage) => stage.status === "failed");
  const failure = failed?.output?.failure as Record<string, unknown> | undefined;
  const recovered = run.status === "completed" && hadRecovery(run);
  const lastFailure = latestFailure(run);
  const finalQaPassed = latestQaPassed(run.document_qa);
  const retryAttempts = run.stages.reduce((total, stage) => total + Math.max(0, stage.attempt - 1), 0);

  return <article className={`run-log-card ${run.status}`}>
    <button className="run-log-summary" onClick={() => setExpanded((value) => !value)}>
      <span className={`run-state ${run.status}`} />
      <div className="run-log-identity">
        <span className="run-log-name-row"><strong>{String(run.config.software_name || "未命名项目")}</strong>
          {latest && <em>最新</em>}</span>
        <small>{formatTime(run.created_at)} · #{shortId(run.id)}</small>
      </div>
      <div className="run-log-badges">
        {recovered && <span className="recovered">曾阻塞，已恢复</span>}
        <span className={run.status}>{statusLabel(run.status)}</span>
      </div>
      <div className="run-log-current">
        <b>{failed ? failed.title : stageTitle(run)}</b>
        <small>{failed ? "需要查看阻塞原因" : currentMessage(run)}</small>
      </div>
      <span className="run-log-chevron" aria-hidden="true">{expanded ? "⌃" : "⌄"}</span>
    </button>

    {expanded && <div className="run-log-detail">
      {(run.safe_error_message || failed) && <div className="run-root-cause">
        <span className="run-log-callout-icon">!</span>
        <div><small>当前阻塞</small><strong>{run.safe_error_message || failed?.message}</strong></div>
        {failure && <details><summary>查看结构化错误</summary><pre>{JSON.stringify(failure, null, 2)}</pre></details>}
      </div>}

      {recovered && <div className="run-recovery-note">
        <span className="run-log-callout-icon">✓</span>
        <div><strong>最终已恢复并完成交付</strong>
          <small>{lastFailure ? `最近一次阻塞：${lastFailure}` : "历史阻塞已被检查点恢复机制处理。"}</small></div>
        {retryAttempts > 0 && <em>历史重试 {retryAttempts} 次</em>}
      </div>}

      <div className="run-stage-heading">
        <div><strong>流程轨迹</strong><small>以最终状态为主；历史尝试次数仅用于诊断。</small></div>
        <span>{completedStages(run)}/{run.stages.length} 个阶段完成</span>
      </div>
      <div className="run-stage-flow">
        {run.stages.map((stage) => <StageStep key={stage.key} stage={stage} />)}
      </div>

      <div className="run-log-metrics">
        <Metric label="任务跨度" value={duration(run)} hint="包含等待与自动恢复时间" />
        <Metric label="执行节点" value={`${run.manual_nodes.length} 个`} hint="本次说明书执行节点" />
        <Metric label="最终说明书质检" value={finalQaPassed === true ? "通过" : finalQaPassed === false ? "未通过" : "无记录"}
          tone={finalQaPassed === true ? "success" : finalQaPassed === false ? "warning" : ""} />
        <Metric label="历史质检记录" value={`${run.document_qa.length + run.source_qa.length} 条`}
          hint={`说明书 ${run.document_qa.length} · 源码 ${run.source_qa.length}`} />
      </div>

      <details className="run-log-technical"><summary>技术详情：最近事件与质检失败项</summary>
        <pre>{JSON.stringify({ task_events: run.task_events.slice(-30),
          document_qa: run.document_qa, source_qa: run.source_qa }, null, 2)}</pre>
      </details>
    </div>}
  </article>;
}

function StageStep({ stage }: { stage: RunDiagnosticsStage }) {
  return <div className={`run-stage-step ${stage.status} ${stage.attempt > 1 ? "retried" : ""}`}
    title={stage.description || stage.message}>
    <i>{stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : stage.status === "running" ? "•" : ""}</i>
    <b>{stage.title}</b>
    <small>{statusLabel(stage.status)}</small>
    {stage.attempt > 1 && <em>{stage.attempt} 次尝试</em>}
  </div>;
}

function Metric({ label, value, hint, tone = "" }: { label: string; value: string; hint?: string; tone?: string }) {
  return <div className={tone}><small>{label}</small><b>{value}</b>{hint && <em>{hint}</em>}</div>;
}

function statusLabel(status: string) {
  return ({ completed: "已完成", failed: "已停止", running: "运行中", queued: "排队中",
    pending: "等待中", waiting_for_user: "等待处理" } as Record<string, string>)[status] || status;
}
function stageTitle(run: RunDiagnosticsItem) {
  return run.stages.find((item) => item.key === run.current_stage)?.title || run.current_stage || "未开始";
}
function currentMessage(run: RunDiagnosticsItem) {
  if (run.status === "completed") return "双文档已完成交付";
  const stage = run.stages.find((item) => item.key === run.current_stage);
  return stage?.message || statusLabel(run.status);
}
function completedStages(run: RunDiagnosticsItem) { return run.stages.filter((stage) => stage.status === "completed").length; }
function hadRecovery(run: RunDiagnosticsItem) {
  return run.stages.some((stage) => stage.attempt > 1 || (stage.events || []).some((event) =>
    String((event as unknown as Record<string, unknown>).status || "") === "failed"));
}
function latestFailure(run: RunDiagnosticsItem) {
  for (const stage of [...run.stages].reverse()) {
    const event = [...(stage.events || [])].reverse().find((item) => {
      const value = item as unknown as Record<string, unknown>;
      return String(value.status || value.event_type || "").includes("fail");
    }) as unknown as Record<string, unknown> | undefined;
    if (event) return String(event.message || stage.message || stage.title);
  }
  return run.safe_error_message || "";
}
function latestQaPassed(rows: Array<Record<string, unknown>>) {
  if (!rows.length) return null;
  return Boolean(rows[0].passed);
}
function shortId(value: string) { return value.slice(0, 8); }
function formatTime(value: string) { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
function duration(run: RunDiagnosticsItem) {
  if (!run.started_at) return "0 秒";
  const seconds = Math.max(0, Math.round((new Date(run.finished_at || run.updated_at).getTime()
    - new Date(run.started_at).getTime()) / 1000));
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}
function formatBytes(value: number) { return value > 1024 * 1024
  ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.max(1, Math.round(value / 1024))} KiB`; }
