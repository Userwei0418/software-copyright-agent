import { useEffect, useRef, useState } from "react";
import {
  connectSidecar, FormalManualJob, listFormalManualJobs, listRecentTasks,
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

export function App() {
  const [connection, setConnection] = useState<SidecarConnection | null>(null);
  const [taskId, setTaskId] = useState("");
  const [message, setMessage] = useState("正在启动本地服务…");
  const [page, setPage] = useState<"quick" | "overview" | "source" | "manual" | "screenshots" | "diagrams" | "assets" | "settings">("overview");
  const [previewRequested, setPreviewRequested] = useState(0);
  const [manualJob, setManualJob] = useState<FormalManualJob | null>(null);
  const connectionAttempt = useRef<Promise<SidecarConnection> | null>(null);

  async function ensureConnection(): Promise<SidecarConnection> {
    if (connection) return connection;
    if (connectionAttempt.current) return connectionAttempt.current;
    setMessage("正在连接本地服务…");
    connectionAttempt.current = (async () => { try {
      const value = await connectSidecar();
      setConnection(value);
      setMessage(`本地服务已连接 · v${value.version}`);
      listRecentTasks(value).then((recent) => {
        if (recent.length) setTaskId((current) => current || recent[0].task_id);
      }).catch(() => setMessage("本地服务已连接，但最近项目读取失败"));
      return value;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setMessage(`本地服务连接失败 · ${detail}`);
      throw new Error(`本地服务连接失败：${detail}`);
    } finally { connectionAttempt.current = null; } })();
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
    setManualJob(null);
    if (!connection || !taskId) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const jobs = await listFormalManualJobs(connection, taskId);
        if (!disposed) setManualJob(
          jobs.find((job) => ["queued", "running"].includes(job.status)) || jobs[0] || null
        );
      } catch {
        // Detailed errors remain in the active workspace. A transient global
        // poll must not replace the user's useful connection status.
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1800);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [connection, taskId]);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">著</span><div>
        <strong>软著材料助手</strong><small>本地证据化工作台</small>
      </div></div>
      <nav>
        <button className={`nav-item quick-nav ${page === "quick" ? "active" : ""}`}
          onClick={() => setPage("quick")}><span>快速开始</span><small>AUTO</small></button>
        <button className={`nav-item ${page === "overview" ? "active" : ""}`}
          onClick={() => setPage("overview")}>项目概览</button>
        <button className={`nav-item ${page === "source" ? "active" : ""}`}
          onClick={() => setPage("source")}>源码材料</button>
        <button className={`nav-item ${page === "manual" ? "active" : ""}`}
          onClick={() => setPage("manual")}>说明书{manualJob && ["queued", "running"].includes(
            manualJob.status) && <small className="nav-progress-badge">{manualJob.progress.percent}%</small>}</button>
        <button className={`nav-item ${page === "screenshots" ? "active" : ""}`}
          onClick={() => setPage("screenshots")}>界面截图</button>
        <button className={`nav-item ${page === "diagrams" ? "active" : ""}`}
          onClick={() => setPage("diagrams")}>图表资产</button>
        <button className={`nav-item ${page === "assets" ? "active" : ""}`}
          onClick={() => setPage("assets")}>我的资产</button>
        <button className={`nav-item ${page === "settings" ? "active" : ""}`}
          onClick={() => setPage("settings")}>设置</button>
      </nav>
      {manualJob && ["queued", "running"].includes(manualJob.status) &&
        <button className="global-manual-progress" onClick={() => setPage("manual")}>
          <span><b>说明书 v{manualJob.version}</b><em>{globalStepLabel(
            manualJob.current_step)} · {manualJob.progress.percent}%</em></span>
          <i><b style={{ width: `${manualJob.progress.percent}%` }} /></i>
          <small>后台持续生成 · 点击查看详情</small>
        </button>}
      <div className="side-status"><i className={connection ? "online" : "offline"} />
        <span>{message}</span></div>
    </aside>

    {page === "quick" ? <QuickStart connection={connection} ensureConnection={ensureConnection}
      onTaskChange={setTaskId} onOpenAssets={() => setPage("assets")}
      onOpenSettings={() => setPage("settings")} onNavigate={setPage} /> : page === "overview" ? <ProjectOverview connection={connection}
      ensureConnection={ensureConnection} onTaskCreated={(value) => setTaskId(value)} /> : page === "source" ?
      <SourceMaterials connection={connection} taskId={taskId}
        onTaskCreated={setTaskId} onBackToOverview={() => setPage("overview")}
        previewRequested={previewRequested} /> : page === "manual" ?
      <ManualWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
        trackedJob={manualJob}
        onOpenDiagrams={() => setPage("diagrams")}
        onOpenScreenshots={() => setPage("screenshots")} /> : page === "assets" ?
      <AssetLibrary connection={connection} onOpen={(value) => { setTaskId(value); setPage("source"); }}
        onPreview={(value) => { setTaskId(value); setPreviewRequested((count) => count + 1); setPage("source"); }}
        onPreviewManual={(value) => { setTaskId(value); setPage("manual"); }}
        onDeleted={(value) => { if (taskId === value) { setTaskId(""); setManualJob(null); } }} /> :
      page === "settings" ? <Settings connection={connection} /> : page === "diagrams" ?
      <FormalDiagramWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
        onOpenManual={() => setPage("manual")} /> : page === "screenshots" ?
      <ScreenshotAssetWorkspace connection={connection} taskId={taskId} onTaskChange={setTaskId}
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
