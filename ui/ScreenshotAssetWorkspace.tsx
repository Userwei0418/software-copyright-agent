import { open } from "@tauri-apps/plugin-dialog";
import { DragEvent, useEffect, useMemo, useState } from "react";
import {
  analyzeScreenshotEvidence, captureProjectPage, CaptureLaunchPlan,
  confirmScreenshotsAndUpdateManual, importScreenshotClipboard,
  importScreenshotEvidenceBatch, importScreenshotEvidenceFolder, launchCaptureProject,
  listFormalManualJobs, loadCaptureLaunchPlan, loadScreenshotEvidenceHistory,
  loadScreenshotEvidenceImage,
  loadScreenshotEvidenceWorkspace, ProjectCaptureStatus, ProjectScreenshotAsset,
  readCaptureProjectStatus, reviewScreenshotEvidence, rollbackScreenshotEvidence,
  ScreenshotEvidenceHistory, ScreenshotEvidenceWorkspace,
  ScreenshotInterpretation, replaceScreenshotEvidenceImage, saveScreenshotProjectProfile,
  saveUiEvidenceDecision, setScreenshotEvidenceAdoptionStatus,
  SidecarConnection, stopCaptureProject,
} from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

const EMPTY_INTERPRETATION: ScreenshotInterpretation = {
  page_title: "", page_type: "", purpose: "", target_roles: [], entry_conditions: [],
  visible_regions: [], key_controls: [], workflow_steps: [], success_state: "",
  failure_and_recovery: "", related_backend_actions: [], route_guess: "",
  related_evidence_refs: [], suggested_group: "", suggested_order: 0,
  suggested_caption: "", confidence: 0, warnings: [],
};

const LIST_FIELDS: Array<{ key: keyof ScreenshotInterpretation; label: string }> = [
  { key: "target_roles", label: "目标角色" }, { key: "entry_conditions", label: "进入条件" },
  { key: "visible_regions", label: "可见区域" }, { key: "key_controls", label: "关键控件" },
  { key: "workflow_steps", label: "操作步骤" },
  { key: "related_backend_actions", label: "证据支持的后台动作" },
  { key: "related_evidence_refs", label: "相关证据引用" }, { key: "warnings", label: "警告" },
];

function editableInterpretation(value: ScreenshotInterpretation | null | undefined,
                                asset?: ProjectScreenshotAsset): ScreenshotInterpretation {
  const source = value && typeof value === "object" ? value : {} as ScreenshotInterpretation;
  const stringList = (item: unknown) => Array.isArray(item)
    ? item.map((entry) => String(entry)) : [];
  return {
    ...EMPTY_INTERPRETATION, ...source,
    page_title: String(source.page_title || asset?.title || ""),
    page_type: String(source.page_type || ""), purpose: String(source.purpose || ""),
    target_roles: stringList(source.target_roles), entry_conditions: stringList(source.entry_conditions),
    visible_regions: stringList(source.visible_regions), key_controls: stringList(source.key_controls),
    workflow_steps: stringList(source.workflow_steps),
    success_state: String(source.success_state || ""),
    failure_and_recovery: String(source.failure_and_recovery || ""),
    related_backend_actions: stringList(source.related_backend_actions),
    route_guess: String(source.route_guess || ""),
    related_evidence_refs: stringList(source.related_evidence_refs),
    suggested_group: String(source.suggested_group || asset?.group_title || ""),
    suggested_order: Number(source.suggested_order || asset?.sort_order || 0),
    suggested_caption: String(source.suggested_caption || asset?.title || ""),
    confidence: Number(source.confidence || 0), warnings: stringList(source.warnings),
  };
}

export function ScreenshotAssetWorkspace({ connection, taskId, onTaskChange, onOpenManual, onOpenSettings }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (value: string) => void;
  onOpenManual: () => void; onOpenSettings: () => void;
}) {
  const [workspace, setWorkspace] = useState<ScreenshotEvidenceWorkspace | null>(null);
  const [jobId, setJobId] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [interpretation, setInterpretation] = useState<ScreenshotInterpretation>(EMPTY_INTERPRETATION);
  const [groupTitle, setGroupTitle] = useState("");
  const [sortOrder, setSortOrder] = useState(0);
  const [sensitiveStatus, setSensitiveStatus] = useState<ProjectScreenshotAsset["sensitive_status"]>("unreviewed");
  const [zoom, setZoom] = useState(100);
  const [recursive, setRecursive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileText, setProfileText] = useState("");
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [launchPlan, setLaunchPlan] = useState<CaptureLaunchPlan | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [candidateId, setCandidateId] = useState("");
  const [captureUrl, setCaptureUrl] = useState("");
  const [captureStatus, setCaptureStatus] = useState<ProjectCaptureStatus | null>(null);
  const [captureAuthorized, setCaptureAuthorized] = useState(false);
  const [uiDecision, setUiDecision] = useState<ScreenshotEvidenceWorkspace["ui_evidence_decision"]["decision"]>("waiting_for_screenshots");
  const [uiDecisionReason, setUiDecisionReason] = useState("");
  const [history, setHistory] = useState<ScreenshotEvidenceHistory | null>(null);
  const [updateState, setUpdateState] = useState<"idle" | "queued" | "running" | "done" | "failed">("idle");

  const assets = workspace?.assets || [];
  const selected = assets.find((item) => item.id === selectedId) || null;
  const groups = useMemo(() => {
    const values = new Map<string, ProjectScreenshotAsset[]>();
    assets.forEach((item) => {
      const key = displayGroup(item);
      values.set(key, [...(values.get(key) || []), item]);
    });
    return [...values.entries()];
  }, [assets]);

  async function refresh(preferred = selectedId) {
    if (!connection || !taskId) return;
    const value = await loadScreenshotEvidenceWorkspace(connection, taskId);
    setWorkspace(value);
    setUiDecision(value.ui_evidence_decision.decision);
    setUiDecisionReason(value.ui_evidence_decision.reason);
    setProfileText(JSON.stringify(value.profile.profile, null, 2));
    setModelId((current) => current || value.vision_models[0]?.id || "");
    const next = value.assets.some((item) => item.id === preferred) ? preferred : value.assets[0]?.id || "";
    setSelectedId(next);
    setSelectedIds((current) => current.filter((id) => value.assets.some((item) => item.id === id)));
  }

  useEffect(() => {
    setWorkspace(null); setJobId(""); setSelectedId(""); setSelectedIds([]); setMessage("");
    releaseUrl(imageUrl); setImageUrl(""); setLaunchPlan(null); setCaptureStatus(null);
    if (!connection || !taskId) return;
    setMessage("正在读取项目概要与截图证据…");
    Promise.all([loadScreenshotEvidenceWorkspace(connection, taskId),
      listFormalManualJobs(connection, taskId)]).then(([value, jobs]) => {
        setWorkspace(value); setJobId(jobs[0]?.id || "");
        setUiDecision(value.ui_evidence_decision.decision);
        setUiDecisionReason(value.ui_evidence_decision.reason);
        setProfileText(JSON.stringify(value.profile.profile, null, 2));
        setModelId(value.vision_models[0]?.id || ""); setSelectedId(value.assets[0]?.id || "");
        setMessage(value.assets.length ? "截图证据已载入。" : "可在生成说明书前先导入真实截图。");
        if (jobs[0]?.id) loadCaptureLaunchPlan(connection, jobs[0].id).then((plan) => {
          setLaunchPlan(plan); setCandidateId(plan.candidates[0]?.id || "");
          setCaptureUrl(plan.candidates[0]?.default_url || "");
        }).catch(() => undefined);
      }).catch((error) => setMessage(error instanceof Error ? error.message : "截图工作台读取失败"));
  }, [connection, taskId]);

  useEffect(() => {
    releaseUrl(imageUrl); setImageUrl("");
    if (!connection || !taskId || !selected) return;
    let active = true;
    loadScreenshotEvidenceImage(connection, taskId, selected.id).then((url) => {
      if (active) setImageUrl(url); else releaseUrl(url);
    }).catch((error) => setMessage(error instanceof Error ? error.message : "截图预览失败"));
    return () => { active = false; };
  }, [connection, taskId, selected?.id, selected?.version]);

  useEffect(() => {
    if (!selected) return;
    setHistory(null);
    setInterpretation(editableInterpretation(selected.interpretation, selected));
    setGroupTitle(displayGroup(selected)); setSortOrder(selected.sort_order);
    setSensitiveStatus(selected.sensitive_status);
  }, [selected?.id, selected?.interpretation_version]);

  useEffect(() => {
    const running = assets.some((item) => ["queued", "running"].includes(item.analysis_status));
    if (!running || !connection || !taskId) return;
    const timer = window.setInterval(() => refresh(selectedId).catch(() => undefined), 1500);
    return () => window.clearInterval(timer);
  }, [connection, taskId, selectedId, assets.map((item) => item.analysis_status).join(",")]);

  useEffect(() => {
    if (!jobId || !captureStatus || !["starting", "running", "partial_failure"].includes(captureStatus.status)) return;
    const timer = window.setInterval(() => readCaptureProjectStatus(jobId).then(setCaptureStatus)
      .catch(() => undefined), 1500);
    return () => window.clearInterval(timer);
  }, [jobId, captureStatus?.status]);

  async function chooseImages() {
    const result = await open({ multiple: true, directory: false,
      filters: [{ name: "真实界面截图", extensions: ["png", "jpg", "jpeg", "webp"] }] });
    const paths = Array.isArray(result) ? result : result ? [result] : [];
    if (paths.length) await runImport(paths, "user");
  }

  async function chooseFolder() {
    const result = await open({ multiple: false, directory: true });
    if (typeof result !== "string" || !connection) return;
    setBusy(true); setMessage("正在预检文件夹、自然排序、去重并逐张导入…");
    try {
      const batch = await importScreenshotEvidenceFolder(connection, taskId, result, recursive, jobId);
      await refresh(); setMessage(batchMessage(batch));
    } catch (error) { setMessage(error instanceof Error ? error.message : "文件夹导入失败"); }
    finally { setBusy(false); }
  }

  async function runImport(paths: string[], source: "user" | "folder" | "automated") {
    if (!connection) return;
    setBusy(true); setMessage(`正在预检 ${paths.length} 张图片；单张失败不会中断批次…`);
    try {
      const batch = await importScreenshotEvidenceBatch(connection, taskId, paths, source, jobId);
      await refresh(); setMessage(batchMessage(batch));
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图批量导入失败"); }
    finally { setBusy(false); }
  }

  async function pasteImage() {
    if (!connection || !navigator.clipboard?.read) {
      setMessage("当前系统未开放剪贴板图片读取，请使用多选图片导入。"); return;
    }
    setBusy(true); setMessage("正在读取系统剪贴板图片…");
    try {
      const entries = await navigator.clipboard.read();
      const type = entries.flatMap((item) => item.types).find((item) => item.startsWith("image/"));
      const entry = entries.find((item) => type && item.types.includes(type));
      if (!entry || !type) throw new Error("剪贴板中没有图片");
      const blob = await entry.getType(type); const data = await blobBase64(blob);
      const batch = await importScreenshotClipboard(connection, taskId, data,
        `clipboard.${type.split("/")[1] || "png"}`, jobId);
      await refresh(); setMessage(batchMessage(batch));
    } catch (error) { setMessage(error instanceof Error ? error.message : "剪贴板图片导入失败"); }
    finally { setBusy(false); }
  }

  async function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const files = [...event.dataTransfer.files] as Array<File & { path?: string }>;
    const paths = files.map((file) => file.path || "").filter(Boolean);
    if (paths.length) { await runImport(paths, "user"); return; }
    if (!connection || !files.length) return;
    setBusy(true);
    try {
      for (const file of files.filter((item) => item.type.startsWith("image/"))) {
        await importScreenshotClipboard(connection, taskId, await blobBase64(file), file.name, jobId);
      }
      await refresh(); setMessage(`已从拖放内容导入 ${files.length} 个候选文件。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "拖放导入失败"); }
    finally { setBusy(false); }
  }

  async function analyze(ids = selectedIds.length ? selectedIds : selected ? [selected.id] : []) {
    if (!connection || !modelId || !ids.length) return;
    if (!privacyAccepted) {
      const accepted = window.confirm(workspace?.privacy_notice || "截图将发送给所选视觉模型，是否继续？");
      if (!accepted) { setMessage("未发送任何截图。"); return; }
      setPrivacyAccepted(true);
    }
    setBusy(true); setMessage(`正在将 ${ids.length} 张截图加入有限并发分析队列…`);
    try {
      await analyzeScreenshotEvidence(connection, taskId, ids, modelId, jobId);
      await refresh(); setMessage("截图已排队；页面将显示每张图的模型、耗时、尝试次数与失败原因。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图分析失败"); }
    finally { setBusy(false); }
  }

  async function saveReview(adopted = selected?.adoption_status === "adopted",
                           confirmSafe = false) {
    if (!connection || !selected) return;
    const reviewedSensitiveStatus = confirmSafe && sensitiveStatus === "unreviewed"
      ? "confirmed_safe" : sensitiveStatus;
    setBusy(true); setMessage("正在保存人工审核的新解读版本…");
    try {
      await reviewScreenshotEvidence(connection, taskId, selected.id, interpretation,
        adopted, groupTitle, sortOrder, reviewedSensitiveStatus);
      setSensitiveStatus(reviewedSensitiveStatus);
      await refresh(selected.id); setMessage(adopted
        ? "当前截图已完成审核并加入采用集；第 7 章与 Word 候选稿更新已自动排队，可继续审核下一张。"
        : "截图解读已保存为审核版本，暂未加入采用集。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图审核保存失败"); }
    finally { setBusy(false); }
  }

  async function replaceSelectedImage() {
    if (!connection || !selected) return;
    const path = await open({ multiple: false, directory: false,
      filters: [{ name: "真实界面截图", extensions: ["png", "jpg", "jpeg", "webp"] }] });
    if (typeof path !== "string") return;
    setBusy(true); setMessage("正在创建新的图片版本，并使旧解读和第 7 章采用集过期…");
    try {
      await replaceScreenshotEvidenceImage(connection, taskId, selected.id, path);
      await refresh(selected.id);
      setMessage("图片已替换并保留旧版本；请重新分析、审核后再采用。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图图片替换失败"); }
    finally { setBusy(false); }
  }

  async function excludeSelected() {
    if (!connection || !selected) return;
    setBusy(true);
    try {
      await setScreenshotEvidenceAdoptionStatus(connection, taskId, [selected.id], "excluded");
      await refresh(selected.id); setMessage("该截图已排除；既有候选稿与历史版本仍保留。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图排除失败"); }
    finally { setBusy(false); }
  }

  async function withdrawSelected() {
    if (!connection || !selected) return;
    setBusy(true);
    try {
      await setScreenshotEvidenceAdoptionStatus(connection, taskId, [selected.id], "pending");
      await refresh(selected.id);
      setMessage("已采用截图已撤回为待审核；历史解读版本仍保留，第 7 章已标记需要更新。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图撤回失败"); }
    finally { setBusy(false); }
  }

  async function openHistory() {
    if (!connection || !selected) return;
    setBusy(true);
    try { setHistory(await loadScreenshotEvidenceHistory(connection, taskId, selected.id)); }
    catch (error) { setMessage(error instanceof Error ? error.message : "截图历史读取失败"); }
    finally { setBusy(false); }
  }

  async function restoreHistory(imageVersion?: number, interpretationVersion?: number) {
    if (!connection || !selected) return;
    setBusy(true); setMessage("正在以新版本恢复所选历史内容…");
    try {
      await rollbackScreenshotEvidence(connection, taskId, selected.id,
        imageVersion, interpretationVersion);
      await refresh(selected.id); setHistory(await loadScreenshotEvidenceHistory(
        connection, taskId, selected.id));
      setMessage("历史内容已恢复为新的当前版本；旧版本和既有文档候选稿均保留。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "历史版本恢复失败"); }
    finally { setBusy(false); }
  }

  async function confirmAndUpdate() {
    if (!connection) return;
    setBusy(true); setUpdateState("queued");
    setMessage(jobId ? "正在提交：保存采用集 → 生成第 7 章 → 装配 Word → 一致性 QA…" : "正在保存截图采用集…");
    try {
      if (!jobId) {
        await refresh(); setMessage("已保存项目截图采用集；创建说明书任务后会直接用于第 7 章。"); return;
      }
      const beforeJobs = await listFormalManualJobs(connection, taskId);
      const previousQaUpdated = beforeJobs.find((item) => item.id === jobId)?.nodes
        .find((item) => item.key === "ui_screenshot_qa")?.updated_at;
      const queued = await confirmScreenshotsAndUpdateManual(connection, jobId);
      setUpdateState("running"); setMessage(queued.message + "；请留在当前页，状态会自动更新。");
      for (let count = 0; count < 120; count += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const jobs = await listFormalManualJobs(connection, taskId);
        const current = jobs.find((item) => item.id === jobId);
        const nodes = current?.nodes || [];
        const qa = nodes.find((item) => item.key === "ui_screenshot_qa");
        const assemble = nodes.find((item) => item.key === "ui_document_reassemble");
        const chapter = nodes.find((item) => item.key === "ui_section_update");
        const qaChanged = Boolean(qa && qa.updated_at !== previousQaUpdated);
        const active = [qa, assemble, chapter].find((item) => item &&
          ["queued", "running", "retrying", "failed"].includes(item.status))
          || (qaChanged ? qa : undefined);
        if (active?.status === "failed") throw new Error(active.safe_error_message || `${active.title}失败`);
        if (qaChanged && ["completed", "completed_with_warnings"].includes(qa?.status || "")) {
          setUpdateState("done"); await refresh(selectedId);
          setMessage(qa?.status === "completed" ? "第 7 章、Word 新候选稿和一致性 QA 已完成。"
            : "第 7 章和 Word 新候选稿已完成；一致性 QA 有待审阅项。"); return;
        }
        setMessage(active ? `${active.title}：${nodeStatusLabel(active.status)}…` : "正在创建第 7 章更新节点…");
      }
      throw new Error("更新仍在后台执行，请稍后在说明书任务驾驶舱查看进度");
    } catch (error) { setUpdateState("failed");
      setMessage(error instanceof Error ? error.message : "确认采用并更新说明书失败"); }
    finally { setBusy(false); }
  }

  async function saveProfile() {
    if (!connection) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(profileText) as Record<string, unknown>;
      await saveScreenshotProjectProfile(connection, taskId, parsed);
      await refresh(); setProfileOpen(false); setMessage("项目概要已保存；相关截图解读已标记为需要重新分析。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "项目概要格式或保存失败"); }
    finally { setBusy(false); }
  }

  async function saveDecision() {
    if (!connection) return;
    setBusy(true);
    try {
      await saveUiEvidenceDecision(connection, taskId, uiDecision, uiDecisionReason);
      await refresh();
      setMessage(uiDecision === "waiting_for_screenshots"
        ? "已标记为等待真实截图；不会用源码推断冒充界面证据。"
        : uiDecision === "source_inferred"
          ? "已记录用户主动选择源码推断版；文档和 QA 会明确标记该限制。"
          : "已记录项目不适用截图及原因；其他章节可继续生成。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "界面证据决策保存失败"); }
    finally { setBusy(false); }
  }

  async function startCapture() {
    if (!jobId || !candidateId || !captureAuthorized) return;
    setBusy(true);
    try { setCaptureStatus(await launchCaptureProject(jobId, candidateId, captureUrl));
      setMessage("实验性项目启动已执行；不会绕过普通截图审核流程。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "实验性项目启动失败，不影响正文"); }
    finally { setBusy(false); }
  }

  async function capturePage() {
    if (!jobId || !connection || captureStatus?.status !== "running") return;
    setBusy(true);
    try {
      const captured = await captureProjectPage(jobId, captureUrl);
      await runImport([captured.path], "automated");
      setMessage("自动采集图片已进入普通导入、AI 解读与人工审核流程。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "实验性采集失败，不影响正文"); }
    finally { setBusy(false); }
  }

  async function stopCapture() {
    if (!jobId) return;
    try { setCaptureStatus(await stopCaptureProject(jobId)); setMessage("实验性项目进程已停止。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "项目停止失败"); }
  }

  function toggleSelection(id: string) {
    setSelectedIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  const selectedIndex = assets.findIndex((item) => item.id === selectedId);
  const reviewQueue = assets.filter((item) => item.analysis_status === "completed" &&
    (item.adoption_status === "pending" || (item.adoption_status === "adopted" &&
      item.sensitive_status !== "confirmed_safe")));
  const adoptedCount = assets.filter((item) => item.adoption_status === "adopted" &&
    item.sensitive_status === "confirmed_safe").length;
  const analyzedCount = assets.filter((item) => item.analysis_status === "completed").length;
  const failedAssets = assets.filter((item) => item.analysis_status === "failed");
  const selectedNeedsReview = !!selected && reviewQueue.some((item) => item.id === selected.id);
  function focusNextReview() {
    const next = selectedNeedsReview ? selected : reviewQueue[0];
    if (next && next.id !== selectedId) setSelectedId(next.id);
    window.requestAnimationFrame(() => document.querySelector(".review-adopt")?.scrollIntoView({
      behavior: "smooth", block: "center",
    }));
  }
  return <main className="screenshot-page" onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
    <header className="topbar"><div><p className="eyebrow">SCREENSHOT EVIDENCE</p>
      <h1>真实截图驱动的用户界面说明</h1>
      <p>真实截图是项目证据。先导入、逐图解读和审核，再生成或更新第 7 章。</p></div>
      <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} />
    </header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>截</span><h2>请先选择项目</h2></section> :
    <section className="screenshot-evidence-content">
      {message && <div className="source-notice" role="status" aria-live="polite">{message}</div>}
      <div className="screenshot-evidence-toolbar">
        <button onClick={() => setProfileOpen(true)}>项目概要 v{workspace?.profile.version || "-"}</button>
        <label>视觉模型<select value={modelId} onChange={(event) => { setModelId(event.target.value); setPrivacyAccepted(false); }}>
          <option value="">没有已确认的视觉模型</option>{workspace?.vision_models.map((item) =>
            <option value={item.id} key={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
        {!workspace?.vision_models.length && <button className="vision-model-cta" onClick={onOpenSettings}>
          去设置视觉模型</button>}
        <button onClick={chooseImages} disabled={busy}>多选图片</button>
        <button onClick={chooseFolder} disabled={busy}>选择文件夹</button>
        <label className="compact-check"><input type="checkbox" checked={recursive}
          onChange={(event) => setRecursive(event.target.checked)} />扫描子文件夹</label>
        <button onClick={pasteImage} disabled={busy}>粘贴截图</button>
        <button onClick={() => analyze()} disabled={busy || !modelId || (!selectedIds.length && !selected)}>批量分析</button>
      </div>
      <section className="screenshot-flow-action"><div><strong>截图处理进度</strong>
        <p>完成解读后逐张审核采用；采用集变化会自动更新第 7 章并装配新的 Word 候选稿。</p></div>
        <ol><li className={assets.length > 0 && analyzedCount === assets.length ? "done" : "active"}><b>1</b><span>AI 解读<small>{analyzedCount}/{assets.length} 张</small></span></li>
          <li className={reviewQueue.length ? "active" : adoptedCount ? "done" : ""}><b>2</b><span>审核采用<small>{adoptedCount} 张已采用</small></span></li>
          <li className={!reviewQueue.length && adoptedCount ? "active" : ""}><b>3</b><span>自动更新说明书<small>去重装配</small></span></li></ol>
        {failedAssets.length ? <button className="primary retry-primary" onClick={() => analyze(
          selected?.analysis_status === "failed" ? [selected.id] : failedAssets.map((item) => item.id)
        )} disabled={busy || !modelId}>
          {failedAssets.length === 1 ? "重试失败截图" : `重试 ${failedAssets.length} 张失败截图`}</button>
          : reviewQueue.length ? <button className="primary" onClick={focusNextReview} disabled={busy}>
          {selectedNeedsReview ? `前往审核当前截图（剩 ${reviewQueue.length} 张）` : `审核下一张（剩 ${reviewQueue.length} 张）`}</button>
          : <button className="primary" onClick={confirmAndUpdate} disabled={busy || !adoptedCount}>
            {updateState === "queued" ? "正在提交更新…" : updateState === "running" ? "正在生成并装配…"
              : updateState === "done" ? "已生成新的说明书候选稿"
              : jobId ? `立即同步 ${adoptedCount} 张截图` : `保存 ${adoptedCount} 张截图采用集`}</button>}
      </section>
      {!assets.length && <div className="ui-evidence-decision">
        <strong>没有可采用真实截图时</strong>
        <select value={uiDecision} onChange={(event) => setUiDecision(event.target.value as typeof uiDecision)}>
          <option value="waiting_for_screenshots">等待真实截图</option>
          <option value="source_inferred">用户主动选择源码推断版</option>
          <option value="not_applicable">项目不适用截图</option>
        </select>
        <input value={uiDecisionReason} onChange={(event) => setUiDecisionReason(event.target.value)}
          placeholder={uiDecision === "waiting_for_screenshots" ? "可选备注" : "必须填写选择原因"} />
        <button onClick={saveDecision} disabled={busy}>保存决策</button>
        <small>该选择只作为应用内任务与质量记录，不会把内部处理说明写进交付文档。</small>
      </div>}
      <div className="screenshot-evidence-grid">
        <aside className="evidence-assets"><header><strong>页面组与截图</strong><small>{assets.length} 张 · {adoptedCount} 已采用</small></header>
          <button className="select-all" onClick={() => setSelectedIds(selectedIds.length === assets.length ? [] : assets.map((item) => item.id))}>
            {selectedIds.length === assets.length ? "取消全选" : "全选"}</button>
          {groups.map(([group, items]) => <section key={group}><h3>{group}<span>{items.length}</span></h3>
            {items.map((item) => <article key={item.id} className={item.id === selectedId ? "selected" : ""}
              draggable onDragStart={(event) => event.dataTransfer.setData("text/asset-id", item.id)}
              onDrop={(event) => { const id = event.dataTransfer.getData("text/asset-id");
                const moving = assets.find((value) => value.id === id); if (moving?.interpretation) {
                  reviewScreenshotEvidence(connection!, taskId, id, moving.interpretation,
                    moving.adoption_status === "adopted", item.group_title, item.sort_order,
                    moving.sensitive_status).then(() => refresh(id)); } }}>
              <input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleSelection(item.id)} />
              <button onClick={() => setSelectedId(item.id)}><span className="asset-thumb">图</span><div><strong>{item.title}</strong>
                <small>{statusLabel(item.analysis_status)} · {reviewLabel(item.review_status)} · {item.adoption_status === "adopted"
                  ? item.sensitive_status === "confirmed_safe" ? "已采用" : "待确认安全"
                  : "未采用"}</small></div></button>
              {item.analysis_status === "failed" && <button className="retry" onClick={() => analyze([item.id])}>重试</button>}
            </article>)}</section>)}
          {!assets.length && <div className="asset-empty"><strong>拖放多张图片到这里</strong><p>支持 PNG、JPG、JPEG、WebP；无需先填写六项长说明。</p></div>}
        </aside>
        <section className="evidence-preview"><header><div><strong>{selected?.title || "大图预览"}</strong>
          <small>{selected ? `${selected.width} × ${selected.height} · ${selected.source} · v${selected.version}` : ""}</small></div>
          <div><button disabled={selectedIndex <= 0} onClick={() => setSelectedId(assets[selectedIndex - 1].id)}>上一张</button>
            <button disabled={selectedIndex < 0 || selectedIndex >= assets.length - 1} onClick={() => setSelectedId(assets[selectedIndex + 1].id)}>下一张</button>
            <input type="range" min="40" max="180" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /><span>{zoom}%</span></div></header>
          <div className="evidence-image-stage">{imageUrl ? <img src={imageUrl} alt={selected?.title || "截图"}
            style={{ width: `${zoom}%` }} /> : <p>请选择或导入截图</p>}</div>
          {selected && <footer><span>{selected.group_title} · 顺序 {selected.sort_order}</span>
            <span>{selected.interpretation_model || "尚未分析"}{selected.interpretation_elapsed_ms ? ` · ${selected.interpretation_elapsed_ms}ms` : ""}
              {selected.interpretation_attempts ? ` · ${selected.interpretation_attempts} 次尝试` : ""}</span></footer>}
        </section>
        <aside className="evidence-inspector"><header><strong>AI 结构化解读</strong><small>证据、置信度和警告均可人工修订并保存版本。</small></header>
          {selected ? <><label>页面名称<input value={interpretation.page_title}
            onChange={(event) => setInterpretation({ ...interpretation, page_title: event.target.value })} /></label>
            <div className="two-fields"><label>页面类型<input value={interpretation.page_type}
              onChange={(event) => setInterpretation({ ...interpretation, page_type: event.target.value })} /></label>
              <label>路由推测<input value={interpretation.route_guess}
                onChange={(event) => setInterpretation({ ...interpretation, route_guess: event.target.value })} /></label></div>
            <label>页面用途<textarea value={interpretation.purpose}
              onChange={(event) => setInterpretation({ ...interpretation, purpose: event.target.value })} /></label>
            {LIST_FIELDS.map((field) => <label key={field.key}>{field.label}<textarea
              value={(interpretation[field.key] as string[]).join("\n")}
              onChange={(event) => setInterpretation({ ...interpretation,
                [field.key]: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} /></label>)}
            <label>成功状态<textarea value={interpretation.success_state}
              onChange={(event) => setInterpretation({ ...interpretation, success_state: event.target.value })} /></label>
            <label>失败与恢复<textarea value={interpretation.failure_and_recovery}
              onChange={(event) => setInterpretation({ ...interpretation, failure_and_recovery: event.target.value })} /></label>
            <label>图注<input value={interpretation.suggested_caption}
              onChange={(event) => setInterpretation({ ...interpretation, suggested_caption: event.target.value })} /></label>
            <div className="two-fields"><label>页面组<input value={groupTitle}
              onChange={(event) => setGroupTitle(event.target.value)} /></label><label>顺序<input type="number" min="0" value={sortOrder}
              onChange={(event) => setSortOrder(Number(event.target.value))} /></label></div>
            <div className="two-fields"><label>置信度<input type="number" min="0" max="1" step="0.01"
              value={interpretation.confidence} onChange={(event) => setInterpretation({ ...interpretation,
                confidence: Number(event.target.value) })} /></label><label>敏感信息<select value={sensitiveStatus}
                onChange={(event) => setSensitiveStatus(event.target.value as typeof sensitiveStatus)}>
                <option value="unreviewed">尚未确认</option><option value="confirmed_safe">已确认安全</option>
                <option value="contains_sensitive">含敏感信息</option></select></label></div>
            {selected.failure_reason && <div className="analysis-error"><strong>解读失败</strong><p>{selected.failure_reason}</p>
              <button onClick={() => analyze([selected.id])} disabled={busy || !modelId}>立即重试当前截图</button></div>}
            {history && <details className="evidence-history" open><summary>版本历史</summary>
              <strong>图片版本</strong>{history.image_revisions.map((item) => <div key={item.id}>
                <span>v{item.version} · {item.edit_source} · {item.width}×{item.height}</span>
                <button onClick={() => restoreHistory(item.version, undefined)} disabled={busy || item.version === selected.version}>恢复图片</button></div>)}
              <strong>解读版本</strong>{history.interpretation_revisions.map((item) => <div key={item.id}>
                <span>v{item.version} · {item.model_name} · {item.status}{item.reviewed ? " · 已审核" : ""}</span>
                <button onClick={() => restoreHistory(undefined, item.version)} disabled={busy || item.status !== "completed" || item.version === selected.interpretation_version}>恢复解读</button></div>)}
            </details>}
            {selectedNeedsReview && <p className="adoption-safety-note">
              点击采用即确认当前图片无需脱敏；如包含账号、手机号或密钥，请先选择“含敏感信息”。</p>}
            <div className="inspector-actions"><div className="inspector-secondary-actions">
                <button onClick={openHistory} disabled={busy}>版本历史</button>
                <button onClick={replaceSelectedImage} disabled={busy}>替换图片</button>
                {selected.adoption_status === "adopted" && <button onClick={withdrawSelected} disabled={busy}>撤回待审核</button>}
                <button onClick={excludeSelected} disabled={busy}>排除</button>
                <button onClick={() => analyze([selected.id])} disabled={busy || !modelId}>重新解读</button>
              </div>
              <button onClick={() => saveReview(false)} disabled={busy}>仅保存修改</button>
              <button className="primary review-adopt" onClick={() => saveReview(true, true)}
                disabled={busy || sensitiveStatus === "contains_sensitive"}>
                {selectedNeedsReview ? "审核并采用当前截图" : "保存修改"}</button></div>
          </> : <p>选择一张截图后查看结构化解读。</p>}
        </aside>
      </div>
      <details className="experimental-capture" open={captureOpen} onToggle={(event) => setCaptureOpen(event.currentTarget.open)}>
        <summary><strong>实验功能：启动项目自动采集</strong><span>开发测试中 · 不稳定 · 不推荐</span></summary>
        <div><p className="experimental-warning">开发测试中 · 不稳定 · 不推荐。复杂项目可能依赖数据库、中间件、测试账号及业务数据，建议优先导入已经准备好的真实截图。</p>
          {!jobId ? <p>创建说明书任务后才可显式授权实验性启动；当前截图导入不受影响。</p> : <>
            <div className="capture-controls"><select value={candidateId} onChange={(event) => { const id = event.target.value;
              const item = launchPlan?.candidates.find((candidate) => candidate.id === id); setCandidateId(id);
              setCaptureUrl(item?.default_url || ""); setCaptureAuthorized(false); }}>
              {launchPlan?.candidates.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select>
              <input value={captureUrl} onChange={(event) => { setCaptureUrl(event.target.value); setCaptureAuthorized(false); }} />
              <label><input type="checkbox" checked={captureAuthorized}
                onChange={(event) => setCaptureAuthorized(event.target.checked)} />我明确授权运行上述项目脚本</label>
              {captureStatus?.status === "running" ? <><button onClick={capturePage}>采集当前地址</button>
                <button onClick={stopCapture}>停止项目</button></> : <button disabled={!captureAuthorized || !candidateId}
                  onClick={startCapture}>授权并启动</button>}</div>
            {captureStatus && <pre>{captureStatus.logTail || captureStatus.status}</pre>}
          </>}</div>
      </details>
      <footer className="screenshot-page-footer"><button onClick={onOpenManual}>返回任务驾驶舱</button></footer>
    </section>}
    {profileOpen && <div className="profile-dialog"><section><header><div><strong>截图理解项目概要</strong>
      <small>复用项目研究结果，控制每张图片的上下文长度；保存后生成新版本。</small></div><button onClick={() => setProfileOpen(false)}>×</button></header>
      <textarea value={profileText} onChange={(event) => setProfileText(event.target.value)} spellCheck={false} />
      <footer><button onClick={() => setProfileOpen(false)}>取消</button><button className="primary" onClick={saveProfile} disabled={busy}>保存新版本</button></footer></section></div>}
  </main>;
}

function releaseUrl(value: string) { if (value.startsWith("blob:")) URL.revokeObjectURL(value); }
function statusLabel(value: ProjectScreenshotAsset["analysis_status"]) {
  return ({ pending: "待分析", queued: "排队", running: "分析中", completed: "已解读",
    failed: "失败", outdated: "已过期" })[value];
}
function nodeStatusLabel(value: string) {
  return ({ queued: "已排队", running: "正在执行", completed: "已完成",
    completed_with_warnings: "已完成，有待审阅项", failed: "失败" } as Record<string, string>)[value]
    || value;
}
function reviewLabel(value: ProjectScreenshotAsset["review_status"]) {
  return ({ pending: "待审核", reviewed: "已审核", rejected: "已排除" })[value];
}
function displayGroup(item: ProjectScreenshotAsset) {
  const normalized = (item.group_title || "").replace(/[\s_-]+/g, "").toLowerCase();
  const generic = new Set(["", "截屏", "截图", "界面截图", "页面截图", "screenshot",
    "screenshots", "未分组", "未分组页面"]);
  return generic.has(normalized)
    ? item.interpretation?.suggested_group || "待确认页面组"
    : item.group_title;
}
function batchMessage(value: { input_count?: number; imported_count: number; warning_count: number; failure_count: number }) {
  return `预检 ${value.input_count ?? value.imported_count + value.warning_count + value.failure_count} 个文件：导入 ${value.imported_count}，警告 ${value.warning_count}，失败 ${value.failure_count}。`;
}
async function blobBase64(blob: Blob) {
  return new Promise<string>((resolve, reject) => { const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = reject;
    reader.readAsDataURL(blob); });
}
