import { useEffect, useRef, useState } from "react";
import {
  checkSidecarHealth, connectSidecar, FormalManualJob, listFormalManualJobs, listRecentTasks,
  SidecarConnection,
} from "./api";
import { ProjectOverview } from "./ProjectOverview";
import { SourceMaterials } from "./SourceMaterials";
import { ManualWorkspace } from "./ManualWorkspace";
import { AssetLibrary } from "./AssetLibrary";
import { Settings } from "./Settings";
import { FormalDiagramWorkspace } from "./FormalDiagramWorkspace";
import { ScreenshotAssetWorkspace } from "./ScreenshotAssetWorkspace";
import { QuickStart } from "./QuickStart";
import { RunLogs } from "./RunLogs";

export type AppPage = "quick" | "overview" | "source" | "manual" | "screenshots" |
  "diagrams" | "assets" | "logs" | "settings";

export function App() {
  const [connection, setConnection] = useState<SidecarConnection | null>(null);
  const [online, setOnline] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [message, setMessage] = useState("正在启动本地服务…");
  const [page, setPage] = useState<AppPage>("quick");
  const [workspacesOpen, setWorkspacesOpen] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [previewRequest, setPreviewRequest] = useState<{ taskId: string; sequence: number } | null>(null);
  const [manualJob, setManualJob] = useState<FormalManualJob | null>(null);
  const connectionAttempt = useRef<Promise<SidecarConnection> | null>(null);

  async function ensureConnection(): Promise<SidecarConnection> {
    if (connection && online) return connection;
    if (connectionAttempt.current) return connectionAttempt.current;
    setConnecting(true);
    setMessage("正在连接本地服务…");
    connectionAttempt.current = (async () => { try {
      const value = await connectSidecar();
      if (!await checkSidecarHealth(value)) throw new Error("本地服务尚未响应，请稍后重连");
      // Keep the same connection identity across reconnects. Workspace effects
      // use it to select data; replacing it would discard unsaved chapter/XML edits.
      const current = connection ? Object.assign(connection, value) : value;
      setConnection(current);
      setOnline(true);
      setMessage(`本地服务已连接 · v${value.version}`);
      listRecentTasks(value).then((recent) => {
        if (recent.length) setTaskId((current) => current || recent[0].task_id);
      }).catch(() => setMessage("本地服务已连接，但最近项目读取失败"));
      return current;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setOnline(false);
      setMessage(`本地服务连接失败 · ${detail}`);
      throw new Error(`本地服务连接失败：${detail}`);
    } finally { connectionAttempt.current = null; setConnecting(false); } })();
    return connectionAttempt.current;
  }

  useEffect(() => {
    let disposed = false;
    let retryTimer: number | undefined;
    const connect = async (attempt: number) => {
      try {
        await ensureConnection();
      } catch {
        if (disposed || attempt >= 2) return;
        setMessage("本地服务正在完成冷启动，稍后自动重连…");
        retryTimer = window.setTimeout(() => void connect(attempt + 1), 1400);
      }
    };
    void connect(0);
    return () => {
      disposed = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    if (!connection) return;
    let disposed = false, checking = false, failures = 0;
    const check = async () => {
      if (disposed || checking) return;
      checking = true;
      const healthy = await checkSidecarHealth(connection);
      checking = false;
      if (disposed) return;
      if (healthy && failures >= 2) setMessage(`本地服务已连接 · v${connection.version}`);
      failures = healthy ? 0 : failures + 1;
      if (healthy) setOnline(true);
      if (failures < 2) return;
      setOnline(false);
      setMessage("本地服务连接已中断。重新连接后可继续查看原任务。");
    };
    const timer = window.setInterval(check, 8000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [connection]);

  useEffect(() => {
    setManualJob(null);
    if (!connection || !taskId) return;
    let disposed = false;
    let refreshing = false;
    const refresh = async () => {
      if (disposed || refreshing) return;
      refreshing = true;
      try {
        const jobs = await listFormalManualJobs(connection, taskId);
        if (!disposed) setManualJob(
          jobs.find((job) => ["queued", "running"].includes(job.status)) || jobs[0] || null
        );
      } catch {
        // Detailed errors remain in the active workspace. A transient global
        // poll must not replace the user's useful connection status.
      } finally { refreshing = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1800);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [connection, taskId]);

  const workspaces: Array<{ page: AppPage; label: string }> = [
    { page: "overview", label: "项目概览" }, { page: "source", label: "源码材料" },
    { page: "manual", label: "说明书" }, { page: "screenshots", label: "界面截图" },
    { page: "diagrams", label: "图表资产" },
  ];
  const isWorkspace = workspaces.some((item) => item.page === page);
  const navigate = (next: AppPage) => {
    if (workspaces.some((item) => item.page === next)) setWorkspacesOpen(true);
    setPage(next);
  };

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">著</span><div>
        <strong>软著材料助手</strong><small>从项目到申请材料</small>
      </div></div>
      <nav aria-label="主要导航">
        <button className={`nav-item quick-nav ${page === "quick" ? "active" : ""}`}
          aria-current={page === "quick" ? "page" : undefined}
          onClick={() => navigate("quick")}><span>快速开始</span></button>
        <button className={`nav-item ${page === "assets" ? "active" : ""}`}
          aria-current={page === "assets" ? "page" : undefined}
          onClick={() => navigate("assets")}>我的资产</button>
        <p className="nav-help">制作与继续 → 快速开始<br />查看与导出 → 我的资产</p>
        <details className="workspace-navigation" open={workspacesOpen || isWorkspace}
          onToggle={(event) => setWorkspacesOpen(event.currentTarget.open)}>
          <summary>专业工作台 <span>修订与检查</span></summary>
          <div>{workspaces.map((item) => <button key={item.page}
            className={`nav-item ${page === item.page ? "active" : ""}`}
            aria-current={page === item.page ? "page" : undefined}
            onClick={() => navigate(item.page)}>{item.label}
            {item.page === "manual" && manualJob && ["queued", "running"].includes(manualJob.status)
              && <small className="nav-progress-badge">{manualJob.progress.percent}%</small>}
          </button>)}</div>
        </details>
      </nav>
      <nav aria-label="应用设置" className="utility-navigation">
        <button className={`nav-item ${page === "logs" ? "active" : ""}`}
          aria-current={page === "logs" ? "page" : undefined}
          onClick={() => navigate("logs")}>运行日志</button>
        <button className={`nav-item ${page === "settings" ? "active" : ""}`}
          aria-current={page === "settings" ? "page" : undefined}
          onClick={() => navigate("settings")}>设置</button>
      </nav>
      {manualJob && ["queued", "running"].includes(manualJob.status) &&
        <button className="global-manual-progress" onClick={() => navigate("manual")}>
          <span><b>说明书 v{manualJob.version}</b><em>{globalStepLabel(
            manualJob.current_step)} · {manualJob.progress.percent}%</em></span>
          <i><b style={{ width: `${manualJob.progress.percent}%` }} /></i>
          <small>后台持续生成 · 点击查看详情</small>
        </button>}
      <div className="side-status" role="status"><i className={online ? "online" : "offline"} />
        <span>{message}</span></div>
      {!online && <button className="reconnect-button" disabled={connecting}
        onClick={() => void ensureConnection().catch(() => {})}>
        {connecting ? "正在连接…" : "重新连接本地服务"}</button>}
    </aside>

    {page === "quick" ? <QuickStart connection={connection} ensureConnection={ensureConnection}
      onTaskChange={setTaskId} onOpenAssets={() => setPage("assets")}
      onOpenSettings={() => navigate("settings")} onNavigate={navigate} /> : page === "overview" ? <ProjectOverview connection={connection}
      ensureConnection={ensureConnection} onTaskCreated={(value) => setTaskId(value)} /> : page === "source" ?
      <SourceMaterials key={taskId || "empty"} connection={connection} taskId={taskId}
        onTaskCreated={setTaskId} onBackToOverview={() => setPage("overview")}
        previewRequested={previewRequest?.taskId === taskId ? previewRequest.sequence : 0}
        onPreviewConsumed={() => setPreviewRequest(null)} /> : page === "manual" ?
      <ManualWorkspace key={taskId || "empty"} connection={connection} taskId={taskId} onTaskChange={setTaskId}
        trackedJob={manualJob}
        onOpenDiagrams={() => navigate("diagrams")}
        onOpenScreenshots={() => navigate("screenshots")} /> : page === "assets" ?
      <AssetLibrary connection={connection} onOpen={(value) => { setTaskId(value); setPage("source"); }}
        onPreview={(value) => { setTaskId(value); setPreviewRequest({ taskId: value, sequence: Date.now() }); setPage("source"); }}
        onPreviewManual={(value) => { setTaskId(value); setPage("manual"); }}
        onDeleted={(value) => { if (taskId === value) { setTaskId(""); setManualJob(null); } }} /> :
      page === "logs" ? <RunLogs connection={connection} /> :
      page === "settings" ? <Settings connection={connection} /> : page === "diagrams" ?
      <FormalDiagramWorkspace key={taskId || "empty"} connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenManual={() => setPage("manual")} /> : page === "screenshots" ?
      <ScreenshotAssetWorkspace key={taskId || "empty"} connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenManual={() => setPage("manual")} onOpenSettings={() => setPage("settings")} /> : null}
  </div>;
}

function globalStepLabel(key: string) {
  return ({ research: "研究项目", draft: "撰写正文", diagrams: "生成图表",
    draft_sections: "撰写正文", render_figures: "生成图表",
    screenshots: "处理截图", screenshot_decisions: "处理截图",
    assemble_docx: "装配 Word", render_qa: "逐页质检" } as
    Record<string, string>)[key] || key;
}
