import { open } from "@tauri-apps/plugin-dialog";
import { useState } from "react";
import { ProjectScanResult, scanProject, SidecarConnection } from "./api";

type Props = {
  connection: SidecarConnection | null;
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

export function ProjectOverview({ connection, onTaskCreated }: Props) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectScanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("选择一个本地项目目录或 ZIP 压缩包开始分析。");

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
    if (!connection || !selectedPath) return;
    setBusy(true);
    setMessage("正在扫描项目、过滤依赖并提取确定性事实…");
    try {
      const value = await scanProject(connection, selectedPath);
      setResult(value);
      onTaskCreated(value.task_id);
      setMessage(`扫描完成 · 任务 ${value.task_id.slice(0, 8)}…`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "项目扫描失败");
    } finally {
      setBusy(false);
    }
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
        <button className="scan-button" disabled={!connection || !selectedPath || busy} onClick={scan}>
          {busy ? "正在扫描…" : "开始本地扫描"}
        </button>
        <p className="intake-message">{message}</p>
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
