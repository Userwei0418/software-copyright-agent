import { useEffect, useState } from "react";
import { loadManualWorkspace, ManualWorkspaceSnapshot, runManualAction,
  SidecarConnection } from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

type Action = "manual-plan" | "diagram-plan" | "diagram-artifacts";
export function ManualWorkspace({ connection, taskId, onTaskChange }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (taskId: string) => void }) {
  const [data, setData] = useState<ManualWorkspaceSnapshot | null>(null);
  const [working, setWorking] = useState<Action | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    setData(null);
    if (!connection || !taskId) return;
    setMessage("正在读取说明书规划…");
    loadManualWorkspace(connection, taskId).then((value) => { setData(value); setMessage(""); })
      .catch((error) => setMessage(error instanceof Error ? error.message : "读取失败"));
  }, [connection, taskId]);
  async function run(action: Action) {
    if (!connection) return;
    setWorking(action); setMessage("正在生成并持久化本阶段结果…");
    try { setData(await runManualAction(connection, taskId, action)); setMessage("本阶段已完成。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "执行失败"); }
    finally { setWorking(null); }
  }
  return <main className="manual-page"><header className="topbar"><div>
    <p className="eyebrow">TECHNICAL MANUAL</p><h1>说明书</h1>
    <p>先审阅章节计划和证据缺口，再准备可编辑图表与最终文档。</p></div>
    <ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} /></header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>DOC</span>
      <h2>请先选择项目</h2><p>说明书将复用项目扫描得到的事实与证据。</p></section> :
      <section className="manual-content">{message && <div className="source-notice">{message}</div>}
        <div className="manual-actions">
          <ActionCard number="01" title="章节与证据计划" ready={!!data?.manual_plan}
            detail="规划 9 个技术说明书章节，显式列出每章缺失信息。"
            disabled={!data?.actions.manual_plan || !!working} onClick={() => run("manual-plan")} />
          <ActionCard number="02" title="图表语义计划" ready={!!data?.diagram_plan}
            detail="从项目证据构建架构图和业务流程图的节点与连线。"
            disabled={!data?.actions.diagram_plan || !!working} onClick={() => run("diagram-plan")} />
          <ActionCard number="03" title="可编辑图表产物" ready={!!data?.diagram_artifacts}
            detail="仅在两张图表证据充足时生成 Draw.io 与 SVG。"
            disabled={!data?.actions.diagram_artifacts || !!working} onClick={() => run("diagram-artifacts")} />
        </div>
        {data?.manual_plan && <><div className="manual-summary"><Metric label="章节" value={data.manual_plan.summary.section_count} />
          <Metric label="已就绪" value={data.manual_plan.summary.ready_sections} />
          <Metric label="待补证据" value={data.manual_plan.summary.needs_evidence_sections} />
          <Metric label="缺失信息" value={data.manual_plan.summary.missing_information_count} /></div>
          <div className="chapter-list"><div className="section-title"><span>说明书章节</span><em>plan v{data.manual_plan.version}</em></div>
            {data.manual_plan.sections.map((section, index) => <article key={section.key}>
              <b>{String(index + 1).padStart(2, "0")}</b><div><h3>{section.title}</h3><p>{section.purpose}</p>
                <small>{section.subsections.join(" · ")}</small></div>
              <span className={section.status}>{section.status === "ready" ? "已就绪" : `缺 ${section.missing_information.length} 项`}</span>
            </article>)}</div>
          {data.manual_plan.missing_information.length > 0 && <div className="missing-panel"><strong>待补信息</strong>
            <div>{data.manual_plan.missing_information.map((item) => <span key={item}>{item}</span>)}</div></div>}</>}
      </section>}
  </main>;
}
function ActionCard(props: { number: string; title: string; detail: string; ready: boolean;
  disabled: boolean; onClick: () => void }) { return <article className={`manual-action ${props.ready ? "ready" : ""}`}>
  <b>{props.number}</b><h2>{props.title}</h2><p>{props.detail}</p><button disabled={props.disabled}
    onClick={props.onClick}>{props.ready ? "重新生成" : "开始生成"}</button></article>; }
function Metric({ label, value }: { label: string; value: number }) {
  return <article><small>{label}</small><strong>{value}</strong></article>;
}
