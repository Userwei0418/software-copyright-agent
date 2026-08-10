import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import {
  archiveFormalScreenshot, editFormalScreenshot, FormalManualScreenshot,
  FormalScreenshotRevision, importFormalScreenshot, listFormalManualJobs,
  listFormalManualSections, listFormalScreenshotRevisions, listFormalScreenshots,
  loadFormalScreenshotImage, replaceFormalScreenshot, rollbackFormalScreenshot,
  ScreenshotDescription, SidecarConnection,
} from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

const DESCRIPTION_FIELDS: Array<{ key: keyof ScreenshotDescription; label: string; hint: string }> = [
  { key: "page_purpose", label: "页面用途", hint: "该页面解决什么问题，面向谁使用。" },
  { key: "entry_conditions", label: "进入条件", hint: "从哪里进入，前置数据或状态是什么。" },
  { key: "visible_regions", label: "可见区域与控件", hint: "说明导航、表单、操作区、状态区等真实界面组成。" },
  { key: "typical_workflow", label: "典型操作流程", hint: "描述用户从开始到完成的操作及完成条件。" },
  { key: "backend_interactions", label: "后台与数据交互", hint: "说明关联接口、本地数据或状态持久化行为。" },
  { key: "result_validation_recovery", label: "结果、校验与恢复", hint: "说明成功结果、错误提示、校验和恢复路径。" },
];

const EMPTY_DESCRIPTION: ScreenshotDescription = {
  page_purpose: "", entry_conditions: "", visible_regions: "", typical_workflow: "",
  backend_interactions: "", result_validation_recovery: "",
};

export function ScreenshotAssetWorkspace({ connection, taskId, onTaskChange, onOpenManual }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (value: string) => void;
  onOpenManual: () => void;
}) {
  const [jobId, setJobId] = useState("");
  const [sections, setSections] = useState<Array<{ section_key: string; title: string; ordinal: number }>>([]);
  const [screenshots, setScreenshots] = useState<FormalManualScreenshot[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [revisions, setRevisions] = useState<FormalScreenshotRevision[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [sourcePath, setSourcePath] = useState("");
  const [title, setTitle] = useState("");
  const [sectionKey, setSectionKey] = useState("");
  const [description, setDescription] = useState<ScreenshotDescription>(EMPTY_DESCRIPTION);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const selected = screenshots.find((item) => item.screenshot_key === selectedKey) || null;
  const activeCount = screenshots.filter((item) => !item.archived).length;
  const formValid = !!title.trim() && !!sectionKey && DESCRIPTION_FIELDS.every(
    (item) => description[item.key].trim().length >= 12);

  useEffect(() => {
    setJobId(""); setSections([]); setScreenshots([]); setSelectedKey(""); setCreating(false);
    releaseUrl(imageUrl); setImageUrl(""); setRevisions([]);
    if (!connection || !taskId) return;
    setMessage("正在读取说明书截图资产…");
    (async () => {
      const jobs = await listFormalManualJobs(connection, taskId);
      if (!jobs.length) { setMessage("当前项目还没有正式说明书任务，请先生成说明书。"); return; }
      for (const job of jobs) {
        const items = await listFormalManualSections(connection, job.id);
        if (items.length) {
          setJobId(job.id); setSections(items);
          const assets = await listFormalScreenshots(connection, job.id, true);
          setScreenshots(assets); setSelectedKey(assets[0]?.screenshot_key || "");
          setMessage(assets.length ? "" : "当前说明书还没有界面截图，可以导入真实项目截图。");
          return;
        }
      }
      setMessage("正式说明书正文尚未生成，暂时无法绑定截图章节。");
    })().catch((error) => setMessage(error instanceof Error ? error.message : "截图资产读取失败"));
  }, [connection, taskId]);

  useEffect(() => {
    if (!selected || creating) return;
    setTitle(selected.title); setSectionKey(selected.section_key);
    setDescription({ ...selected.description });
  }, [selected?.screenshot_key, selected?.version, creating]);

  useEffect(() => {
    releaseUrl(imageUrl); setImageUrl(""); setRevisions([]); setHistoryOpen(false);
    if (!connection || !jobId || !selected) return;
    let active = true;
    Promise.all([
      loadFormalScreenshotImage(connection, jobId, selected.screenshot_key),
      listFormalScreenshotRevisions(connection, jobId, selected.screenshot_key),
    ]).then(([url, history]) => {
      if (active) { setImageUrl(url); setRevisions(history); } else releaseUrl(url);
    }).catch((error) => setMessage(error instanceof Error ? error.message : "截图预览读取失败"));
    return () => { active = false; };
  }, [connection, jobId, selected?.screenshot_key, selected?.version]);

  async function refresh(key = selectedKey) {
    if (!connection || !jobId) return;
    const items = await listFormalScreenshots(connection, jobId, true);
    setScreenshots(items);
    const nextKey = items.some((item) => item.screenshot_key === key) ? key : items[0]?.screenshot_key || "";
    setSelectedKey(nextKey);
    if (nextKey) setRevisions(await listFormalScreenshotRevisions(connection, jobId, nextKey));
  }

  async function chooseImage() {
    const value = await open({ multiple: false, directory: false,
      filters: [{ name: "界面截图", extensions: ["png", "jpg", "jpeg", "webp"] }] });
    return typeof value === "string" ? value : "";
  }

  async function beginImport() {
    const path = await chooseImage();
    if (!path) return;
    setCreating(true); setSelectedKey(""); setSourcePath(path); setTitle("");
    setSectionKey(sections.find((item) => item.section_key === "ui_operations")?.section_key ||
      sections[0]?.section_key || "");
    setDescription({ ...EMPTY_DESCRIPTION }); setMessage("请补全六项截图说明后保存。");
  }

  async function saveForm() {
    if (!connection || !jobId || !formValid) return;
    setBusy(true); setMessage(creating ? "正在净化图片并创建截图资产…" : "正在保存截图说明新版本…");
    try {
      const result = creating ? await importFormalScreenshot(
        connection, jobId, sourcePath, sectionKey, title.trim(), description) :
        await editFormalScreenshot(connection, jobId, selected!.screenshot_key,
          sectionKey, title.trim(), description);
      setCreating(false); setSourcePath(""); await refresh(result.screenshot_key);
      setSelectedKey(result.screenshot_key);
      setMessage(`截图 v${result.version} 已保存；请返回说明书重新装配后再导出。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图保存失败"); }
    finally { setBusy(false); }
  }

  async function replaceImage() {
    if (!connection || !jobId || !selected) return;
    const path = await chooseImage();
    if (!path) return;
    setBusy(true); setMessage("正在净化并替换截图，旧图片仍保留在历史版本中…");
    try {
      const result = await replaceFormalScreenshot(connection, jobId, selected.screenshot_key, path);
      await refresh(); setMessage(`图片已替换并创建 v${result.version}。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图替换失败"); }
    finally { setBusy(false); }
  }

  async function setArchive(archived: boolean) {
    if (!connection || !jobId || !selected) return;
    if (archived && !window.confirm("归档后截图不会进入新说明书，但历史图片不会删除。确定继续吗？")) return;
    setBusy(true); setMessage(archived ? "正在归档截图…" : "正在恢复截图…");
    try {
      const result = await archiveFormalScreenshot(
        connection, jobId, selected.screenshot_key, archived);
      await refresh(selected.screenshot_key);
      setMessage(archived ? `截图已归档为 v${result.version}，不会进入下一版说明书。` :
        `截图已恢复为 v${result.version}，重新装配后会进入说明书。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图状态修改失败"); }
    finally { setBusy(false); }
  }

  async function rollback(version: number) {
    if (!connection || !jobId || !selected) return;
    setBusy(true); setMessage(`正在从 v${version} 创建新的恢复版本…`);
    try {
      const result = await rollbackFormalScreenshot(
        connection, jobId, selected.screenshot_key, version);
      await refresh(); setMessage(`历史内容已恢复并创建 v${result.version}。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "截图恢复失败"); }
    finally { setBusy(false); }
  }

  function cancelCreate() {
    setCreating(false); setSourcePath(""); setSelectedKey(screenshots[0]?.screenshot_key || "");
    setMessage("");
  }

  const visibleScreenshots = screenshots.filter((item) => showArchived || !item.archived);
  return <main className="screenshot-page"><header className="topbar"><div>
    <p className="eyebrow">SCREENSHOT ASSETS</p><h1>界面截图</h1>
    <p>管理实际进入说明书的界面图片、章节位置与六维操作说明，所有修改均保留版本。</p></div>
    <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} />
  </header>
  {!taskId || !jobId ? <section className="overview-placeholder source-empty"><span>截</span>
    <h2>{message || "请先选择项目"}</h2><p>截图只能绑定已经生成的正式说明书章节。</p>
    {taskId && <button onClick={onOpenManual}>返回说明书</button>}</section> :
  <section className="screenshot-content">{message && <div className="source-notice">{message}</div>}
    <div className="screenshot-workbench"><aside className="screenshot-list"><header><div>
      <strong>截图资产</strong><small>{activeCount} 张使用中</small></div><button onClick={beginImport}>+ 导入</button></header>
      <label className="archived-toggle"><input type="checkbox" checked={showArchived}
        onChange={(event) => setShowArchived(event.target.checked)} />显示已归档</label>
      {visibleScreenshots.map((item) => <button key={item.screenshot_key}
        className={`${selected?.screenshot_key === item.screenshot_key ? "selected" : ""} ${item.archived ? "archived" : ""}`}
        onClick={() => { setCreating(false); setSelectedKey(item.screenshot_key); }}><span>截</span><div>
          <strong>{item.title}</strong><small>{item.section_key} · v{item.version}</small></div>
          <em>{item.archived ? "已归档" : "使用中"}</em></button>)}
      {!visibleScreenshots.length && <p className="screenshot-list-empty">暂无截图，点击“导入”添加真实项目界面。</p>}
    </aside>
    <section className="screenshot-preview"><header><div><strong>{creating ? "导入新截图" : selected?.title || "截图预览"}</strong>
      <small>{creating ? sourcePath.split(/[\\/]/).pop() : selected ? `${selected.width} × ${selected.height} · v${selected.version}` : ""}</small></div>
      {!creating && selected && <div><button onClick={replaceImage} disabled={busy || selected.archived}>替换图片</button>
        <button onClick={() => setHistoryOpen(!historyOpen)}>历史版本</button>
        <button className={selected.archived ? "restore" : "danger"} onClick={() => setArchive(!selected.archived)}
          disabled={busy}>{selected.archived ? "恢复使用" : "归档"}</button></div>}</header>
      <div className="screenshot-image-stage">{creating ? <div className="new-screenshot-placeholder"><span>待导入</span>
        <strong>{sourcePath.split(/[\\/]/).pop()}</strong><small>保存后会移除元数据并统一转为 PNG</small></div> :
        imageUrl ? <img src={imageUrl} alt={selected?.title || "界面截图"} /> :
        <div className="empty-state"><h2>请选择截图</h2></div>}</div>
      <footer><span>真实项目截图</span><span>图片完整性校验</span><button onClick={onOpenManual}>返回说明书重新装配</button></footer>
      {historyOpen && selected && <div className="screenshot-history"><header><div><strong>历史版本</strong>
        <small>恢复会创建新版本，不覆盖历史图片</small></div><button onClick={() => setHistoryOpen(false)}>×</button></header>
        <div>{revisions.map((revision) => <article key={revision.revision_id}><div><b>v{revision.version}</b>
          <strong>{revision.title}</strong><small>{revisionLabel(revision.edit_source)} · {
            revision.created_at.replace("T", " ").slice(0, 16)}</small></div><button disabled={busy || revision.version === selected.version}
            onClick={() => rollback(revision.version)}>{revision.version === selected.version ? "当前" : "恢复"}</button></article>)}</div></div>}
    </section>
    <aside className="screenshot-inspector"><header><strong>{creating ? "截图信息" : "章节与说明"}</strong>
      <small>每项至少 12 个字符，避免生成只有图片没有实质说明的空洞页面。</small></header>
      <label>截图标题<input value={title} disabled={busy || !!selected?.archived}
        onChange={(event) => setTitle(event.target.value)} /></label>
      <label>插入章节<select value={sectionKey} disabled={busy || !!selected?.archived}
        onChange={(event) => setSectionKey(event.target.value)}>{sections.map((section) => <option
          key={section.section_key} value={section.section_key}>{section.ordinal}. {section.title}</option>)}</select></label>
      {DESCRIPTION_FIELDS.map((item) => <label key={item.key}>{item.label}<small>{item.hint}</small>
        <textarea value={description[item.key]} disabled={busy || !!selected?.archived}
          onChange={(event) => setDescription((current) => ({ ...current, [item.key]: event.target.value }))} />
        <em className={description[item.key].trim().length >= 12 ? "valid" : ""}>{
          description[item.key].trim().length} / 12+</em></label>)}
      <div className="screenshot-form-actions">{creating && <button className="secondary" onClick={cancelCreate}>取消</button>}
        <button className="primary" disabled={busy || !formValid || !!selected?.archived}
          onClick={saveForm}>{busy ? "正在保存…" : creating ? "保存截图资产" : "保存为新版本"}</button></div>
    </aside></div>
  </section>}
  </main>;
}

function releaseUrl(value: string) {
  if (value.startsWith("blob:")) URL.revokeObjectURL(value);
}

function revisionLabel(value: FormalScreenshotRevision["edit_source"]) {
  return ({ import: "首次导入", manual: "说明修订", replacement: "替换图片",
    rollback: "历史恢复", archive: "归档", restore: "恢复使用" })[value];
}
