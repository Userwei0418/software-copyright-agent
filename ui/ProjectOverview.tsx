import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import {
  answerConfirmation, deleteTask, listRecentTasks, loadInspection, ProjectScanResult, RecentTask,
  scanProject, SidecarConnection,
} from "./api";

type Props = {
  connection: SidecarConnection | null;
  ensureConnection: () => Promise<SidecarConnection>;
  onTaskCreated: (taskId: string) => void;
};

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 0);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ProjectOverview({ connection, ensureConnection, onTaskCreated }: Props) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectScanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("选择一个本地项目目录或 ZIP 压缩包开始分析。");
  const [recent, setRecent] = useState<RecentTask[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!connection) return;
    listRecentTasks(connection).then(setRecent).catch(() => setRecent([]));
  }, [connection]);

  async function choose(kind: "directory" | "zip") {
    try {
      const path = await open(kind === "directory" ? {
        directory: true, multiple: false, title: "选择软件项目目录",
      } : {
        directory: false, multiple: false, title: "选择软件项目 ZIP",
        filters: [{ name: "ZIP 项目", extensions: ["zip"] }],
      });
      if (typeof path === "string") {
        setSelectedPath(path);
        setResult(null);
        setMessage("路径已由系统文件选择器授权，可以开始扫描。");
      }
    } catch {
      setMessage("当前不是 Tauri 桌面环境，无法调用系统文件选择器。");
    }
  }

  async function scan() {
    if (!selectedPath) return;
    setBusy(true);
    setMessage(connection ? "正在扫描项目、过滤依赖并提取确定性事实…" :
      "正在重新连接本地服务…");
    try {
      const activeConnection = connection ?? await ensureConnection();
      setMessage("正在扫描项目、过滤依赖并提取确定性事实…");
      const value = await scanProject(activeConnection, selectedPath);
      setResult(value);
      setRecent(await listRecentTasks(activeConnection));
      onTaskCreated(value.task_id);
      setMessage(`扫描完成 · 任务 ${value.task_id.slice(0, 8)}…`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "项目扫描失败");
    } finally {
      setBusy(false);
    }
  }

  async function openRecent(task: RecentTask) {
    if (!connection || !task.summary || !task.snapshot_id) return;
    setBusy(true);
    setMessage(`正在打开 ${task.display_name}…`);
    try {
      const inspection = await loadInspection(connection, task.task_id);
      setResult({
        task_id: task.task_id, snapshot_id: task.snapshot_id,
        summary: task.summary, inspection,
      });
      onTaskCreated(task.task_id);
      setSelectedPath(null);
      setMessage(`已恢复任务 ${task.task_id.slice(0, 8)}…`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "最近任务打开失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirm(fieldKey: string) {
    const value = answers[fieldKey]?.trim();
    if (!connection || !result || !value) return;
    setBusy(true);
    setMessage(`正在确认 ${fieldKey}…`);
    try {
      const response = await answerConfirmation(connection, result.task_id, fieldKey, value);
      setResult({ ...result, inspection: response.inspection });
      setAnswers((current) => ({ ...current, [fieldKey]: "" }));
      setRecent(await listRecentTasks(connection));
      setMessage(response.remaining_required
        ? `已确认，仍有 ${response.remaining_required} 项必填信息`
        : "全部必填信息已确认，项目任务已完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认提交失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeTask(task: RecentTask) {
    if (!connection || !window.confirm(`删除任务“${task.display_name}”及其应用内产物？\n原项目文件不会被删除。`)) return;
    setBusy(true);
    try {
      await deleteTask(connection, task.task_id);
      setRecent((items) => items.filter((item) => item.task_id !== task.task_id));
      if (result?.task_id === task.task_id) { setResult(null); onTaskCreated(""); }
      setMessage("任务及其应用内产物已删除，原项目未受影响。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "任务删除失败"); }
    finally { setBusy(false); }
  }

  async function clearTasks() {
    if (!connection || !recent.length || !window.confirm(
      `清理列表中 ${recent.length} 个任务及应用内产物？\n原项目文件不会被删除。`)) return;
    setBusy(true);
    try {
      for (const task of recent) await deleteTask(connection, task.task_id);
      setRecent([]); setResult(null); onTaskCreated("");
      setMessage("最近任务已清理，原项目未受影响。");
    } catch (error) {
      setRecent(await listRecentTasks(connection));
      setMessage(error instanceof Error ? error.message : "批量清理未完成");
    } finally { setBusy(false); }
  }

  const pending = result?.inspection.confirmations.filter((item) => item.status === "pending") ?? [];
  return <main className="overview-page">
    <header className="topbar overview-topbar"><div><p className="eyebrow">PROJECT INTAKE</p>
      <h1>项目概览</h1><p>在本地读取项目，提取可追溯事实，不上传源码。</p></div>
      {result && <span className={`task-state ${result.inspection.task.status}`}>
        {result.inspection.task.status === "waiting_for_user" ? "等待确认" : "扫描完成"}
      </span>}
    </header>
    <section className="overview-content">
      <div className="intake-card">
        <div><span className="step-mark">01</span><h2>选择项目</h2>
          <p>目录将原地只读扫描；ZIP 会隔离解压到应用任务目录。</p></div>
        <div className="picker-actions"><button onClick={() => choose("directory")}>选择项目目录</button>
          <button onClick={() => choose("zip")}>选择 ZIP</button></div>
        <div className="selected-path"><small>已授权路径</small>
          <strong>{selectedPath ?? "尚未选择"}</strong></div>
        <button className="scan-button" disabled={!selectedPath || busy} onClick={scan}>
          {busy ? (connection ? "正在扫描…" : "正在连接…") :
            connection ? "开始本地扫描" : "连接本地服务并扫描"}
        </button>
        <p className="intake-message">{message}</p>
        {recent.length > 0 && <div className="recent-projects"><div className="section-title">
          <span>最近任务</span><button onClick={clearTasks} disabled={busy}>清理全部</button></div>
          {recent.slice(0, 6).map((task) => <div className="recent-row" key={task.task_id}><button
            onClick={() => openRecent(task)} disabled={busy || !task.snapshot_id}>
            <span><strong>{task.display_name}</strong><small>{task.source_kind === "zip" ? "ZIP" : "目录"}</small></span>
            <b>{task.status === "waiting_for_user" ? "待确认" : "已完成"}</b>
          </button><button className="delete-task" disabled={busy} onClick={() => removeTask(task)}
            aria-label={`删除 ${task.display_name}`}>×</button></div>)}
        </div>}
      </div>

      {result ? <div className="overview-results">
        <div className="metric-grid">
          <article><small>有效文件</small><strong>{result.summary.file_count}</strong></article>
          <article><small>已过滤</small><strong>{result.summary.ignored_count}</strong></article>
          <article><small>读取体积</small><strong>{formatBytes(result.summary.total_bytes)}</strong></article>
          <article className={result.summary.secret_finding_count ? "metric-warning" : ""}>
            <small>敏感特征</small><strong>{result.summary.secret_finding_count}</strong></article>
        </div>
        <div className="overview-columns">
          <section className="fact-panel"><div className="section-title"><span>已提取事实</span>
            <em>{result.inspection.facts.length}</em></div>
            {result.inspection.facts.slice(0, 10).map((fact) => <div className="fact-row" key={fact.key}>
              <span>{fact.key}</span><strong>{displayValue(fact.value)}</strong>
              <small>{Math.round(fact.confidence * 100)}%</small></div>)}
          </section>
          <section className="confirmation-panel"><div className="section-title"><span>待确认项</span>
            <em>{pending.length}</em></div>
            {pending.length ? pending.map((item) => <div className="confirmation-row" key={item.field_key}>
              <strong>{item.question}</strong><small>{item.field_key}</small>
              {item.candidates.length > 0 && <div className="candidate-list">{item.candidates.map(
                (candidate) => <button key={String(candidate)} onClick={() => setAnswers(
                  (current) => ({ ...current, [item.field_key]: String(candidate) })
                )}>{String(candidate)}</button>)}</div>}
              <div className="confirmation-form"><input value={answers[item.field_key] ?? ""}
                onChange={(event) => setAnswers((current) => ({
                  ...current, [item.field_key]: event.target.value,
                }))} placeholder="输入确认值" />
                <button disabled={busy || !answers[item.field_key]?.trim()}
                  onClick={() => confirm(item.field_key)}>确认</button></div>
            </div>) : <div className="all-clear">无需人工补充，项目元数据已确定。</div>}
          </section>
        </div>
        <div className="language-strip"><strong>识别技术语言</strong>
          <div>{result.summary.languages.map((language) => <span key={language}>{language}</span>)}</div>
        </div>
      </div> : <div className="overview-placeholder"><span>本地</span><h2>源码不会离开设备</h2>
        <p>扫描器只记录文件哈希、结构事实和必要证据定位；命中的敏感值不会持久化。</p></div>}
    </section>
  </main>;
}
