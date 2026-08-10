import { useEffect, useState } from "react";
import { loadManualWorkspace, ManualWorkspaceSnapshot, runManualAction,
  listModelConfigs, loadAppSettings, ModelConfig, SidecarConnection } from "./api";
import { ProjectSwitcher } from "./ProjectSwitcher";

type Action = "manual-plan" | "diagram-plan" | "diagram-artifacts";
export function ManualWorkspace({ connection, taskId, onTaskChange, onOpenDiagrams }: {
  connection: SidecarConnection | null; taskId: string; onTaskChange: (taskId: string) => void;
  onOpenDiagrams: () => void }) {
  const [data, setData] = useState<ManualWorkspaceSnapshot | null>(null);
  const [working, setWorking] = useState<Action | null>(null);
  const [message, setMessage] = useState("");
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [modelId, setModelId] = useState("");
  useEffect(() => { if (!connection) return; Promise.all([
    listModelConfigs(connection), loadAppSettings(connection),
  ]).then(([items, settings]) => {
    const available = items.filter((item) => item.enabled); setModels(available);
    const preferred = available.some((item) => item.id === settings.manual_model_id)
      ? settings.manual_model_id : available[0]?.id;
    setModelId(preferred || "");
  }).catch(() => setModels([])); }, [connection]);
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
    <p>先完成项目证据预检，再由已配置的 AI 生成说明书与图表草稿。</p></div>
    <div className="manual-selectors"><ProjectSwitcher connection={connection} taskId={taskId} onChange={onTaskChange} />
      <label className="model-switcher"><small>生成模型</small><select value={modelId}
        onChange={(event) => setModelId(event.target.value)}><option value="">尚无可用模型</option>
        {models.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
    </div></header>
    {!taskId ? <section className="overview-placeholder source-empty"><span>DOC</span>
      <h2>请先选择项目</h2><p>说明书将复用项目扫描得到的事实与证据。</p></section> :
      <section className="manual-content">{message && <div className="source-notice">{message}</div>}
        <div className="generation-boundary"><strong>当前阶段：项目证据预检</strong>
          <p>下面三步由本地规则执行，不调用 AI，也不代表说明书正文已经生成。AI 正文生成将在模型配置接入后单独显示模型、耗时和调用记录。</p></div>
        <div className="manual-actions">
          <ActionCard number="01" title="章节证据预检（规则）" ready={!!data?.manual_plan}
            detail="本地规则规划章节并列出缺失证据，不生成正文。"
            disabled={!data?.actions.manual_plan || !!working} onClick={() => run("manual-plan")} />
          <ActionCard number="02" title="图表证据预检（规则）" ready={!!data?.diagram_plan}
            detail="检查源码中可支撑架构图和流程图的节点、连线与缺口。"
            disabled={!data?.actions.diagram_plan || !!working} onClick={() => run("diagram-plan")} />
          <ActionCard number="03" title="渲染可编辑图表（规则）" ready={!!data?.diagram_artifacts}
            detail="证据完整时把语义计划渲染为 Draw.io 与 SVG；不调用图片生成模型。"
            disabled={!data?.actions.diagram_artifacts || !!working} onClick={() => run("diagram-artifacts")} />
        </div>
        <div className="ai-generation-card"><div><span>AI</span><div><strong>生成说明书正文与图表语义</strong>
          <p>将调用用户选择的模型，并采用内置软著文档与专业 Draw.io 技能约束输出。</p></div></div>
          <button disabled>{modelId ? "正文生成下一步接入" : "请先在设置中添加模型"}</button></div>
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
        {data?.diagram_plan && <div className="diagram-readiness"><div className="section-title">
          <span>图表预检结果</span><em>plan v{data.diagram_plan.version}</em></div>
          {data.diagram_plan.diagrams.map((diagram) => <article key={diagram.key}><div>
            <strong>{diagram.title}</strong><small>{diagram.node_count} 个节点 · {diagram.edge_count} 条连线</small></div>
            <span className={diagram.status}>{diagram.status === "ready" ? "证据已就绪" :
              `缺：${diagram.missing_information.join("、")}`}</span></article>)}
          {data.diagram_artifacts ? <button onClick={onOpenDiagrams}>进入图表资产查看与修改</button> :
            <p>图表尚未生成：先补充上方缺失信息，或等待 AI 根据项目证据生成可审阅的图表语义草稿。</p>}
        </div>}
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
