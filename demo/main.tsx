import { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

function siteUrl(path: string) {
  return `${import.meta.env.BASE_URL}${path.replace(/^\/+/, "")}`;
}

type PageKey = "quick" | "overview" | "source" | "manual" | "screenshots" | "diagrams" | "assets" | "logs" | "settings";
type RunState = "idle" | "running" | "waiting" | "done";
type NodeState = "pending" | "running" | "waiting" | "done";

const stages = [
  { name: "扫描项目", detail: "识别 642 个文件与 8 个业务模块", progress: 10 },
  { name: "确认关键信息", detail: "等待确认软件名称、版本与截图授权", progress: 18 },
  { name: "筛选核心源码", detail: "按 A / B / C 级筛选并执行敏感信息检查", progress: 31 },
  { name: "项目证据研究", detail: "建立模块、接口、数据与运行方式证据索引", progress: 43 },
  { name: "处理界面截图", detail: "采用 3 张已审核的模拟界面证据", progress: 54 },
  { name: "撰写八章正文", detail: "章节并行生成，持续形成可预览产物", progress: 68 },
  { name: "生成专业图表", detail: "生成 4 张可编辑 Draw.io 图表", progress: 80 },
  { name: "装配双文档", detail: "装配源码文档与软件说明书", progress: 90 },
  { name: "逐页质量检查", detail: "渲染 78 页并检查空白页、图表和版式", progress: 97 },
  { name: "交付完成", detail: "双文档与图表资产已准备好", progress: 100 },
];

const navItems: Array<{ key: PageKey; label: string; icon: string }> = [
  { key: "quick", label: "快速开始", icon: "↗" },
  { key: "overview", label: "项目概览", icon: "◇" },
  { key: "source", label: "源码材料", icon: "⌘" },
  { key: "manual", label: "说明书", icon: "▤" },
  { key: "screenshots", label: "界面截图", icon: "▧" },
  { key: "diagrams", label: "图表资产", icon: "⌁" },
  { key: "assets", label: "我的资产", icon: "□" },
  { key: "logs", label: "运行日志", icon: "≡" },
  { key: "settings", label: "设置", icon: "⚙" },
];

function DemoApp() {
  const embedded = new URLSearchParams(window.location.search).get("embed") === "1";
  const [page, setPage] = useState<PageKey>("quick");
  const [stage, setStage] = useState(0);
  const [runState, setRunState] = useState<RunState>("running");
  const [preview, setPreview] = useState<"source" | "manual" | "diagram" | null>(null);
  const [autoStarted, setAutoStarted] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setAutoStarted(true), 500);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!autoStarted || runState !== "running") return;
    if (stage === 1) {
      const waitTimer = window.setTimeout(() => setRunState("waiting"), 900);
      return () => window.clearTimeout(waitTimer);
    }
    if (stage >= stages.length - 1) {
      setRunState("done");
      return;
    }
    const timer = window.setTimeout(() => setStage((value) => value + 1), stage < 3 ? 1150 : 950);
    return () => window.clearTimeout(timer);
  }, [autoStarted, runState, stage]);

  function restart() {
    setPage("quick");
    setStage(0);
    setRunState("running");
    setAutoStarted(true);
    setPreview(null);
  }

  function confirmAndContinue() {
    setStage(2);
    setRunState("running");
  }

  const progress = stages[stage]?.progress ?? 0;
  const stageStatus = (index: number): NodeState => {
    if (stage > index) return "done";
    if (stage < index) return "pending";
    if (runState === "waiting") return "waiting";
    if (runState === "done") return "done";
    return "running";
  };

  return <div className={`demo-app ${embedded ? "is-embedded" : ""}`}>
    {!embedded && <div className="demo-disclosure"><i />交互演示模式 <span>模拟数据</span><b>不连接本地服务 · 不调用模型 · 不产生真实文件</b><a href={siteUrl("landing/")}>返回介绍页 ↗</a></div>}
    <aside className="demo-sidebar">
      <a className="demo-brand" href={embedded ? siteUrl("demo/?embed=1") : siteUrl("landing/")}><span>著</span><div><strong>软著材料助手</strong><small>本地证据化工作台</small></div></a>
      <nav>
        {navItems.map((item) => <button key={item.key} className={page === item.key ? "active" : ""} onClick={() => setPage(item.key)}><i>{item.icon}</i><span>{item.label}</span>{item.key === "manual" && runState === "running" && <em>{progress}%</em>}</button>)}
      </nav>
      <div className="demo-run-card">
        <span>演示任务 · v1</span>
        <strong>{runState === "done" ? "交付完成" : runState === "waiting" ? "等待你的确认" : stages[stage].name}</strong>
        <div><i style={{ width: `${progress}%` }} /></div>
        <small>{progress}% · 点击快速开始查看</small>
      </div>
      <div className="demo-side-foot"><i /><span>演示环境已就绪</span><small>DEMO</small></div>
    </aside>

    <main className="demo-main">
      {page === "quick" ? <QuickWorkspace stage={stage} runState={runState} progress={progress} stageStatus={stageStatus} onConfirm={confirmAndContinue} onRestart={restart} onPreview={setPreview} /> :
        <WorkspacePage page={page} stage={stage} progress={progress} onPreview={setPreview} />}
    </main>

    {preview && <PreviewModal type={preview} onClose={() => setPreview(null)} />}
  </div>;
}

function QuickWorkspace({ stage, runState, progress, stageStatus, onConfirm, onRestart, onPreview }: {
  stage: number; runState: RunState; progress: number; stageStatus: (index: number) => NodeState;
  onConfirm: () => void; onRestart: () => void; onPreview: (type: "source" | "manual" | "diagram") => void;
}) {
  const outputCount = stage >= 9 ? 7 : stage >= 7 ? 5 : stage >= 6 ? 4 : stage >= 5 ? 2 : 0;
  return <div className="quick-workspace">
    <header className="workspace-hero">
      <div><span className="workspace-eyebrow">UNATTENDED PIPELINE · INTERACTIVE DEMO</span><h1>{runState === "done" ? "双文档已经准备好" : runState === "waiting" ? "有一项需要你确认" : "自动化流水线正在运行"}</h1><p>{runState === "done" ? "模拟交付物已经形成，可以打开预览或重新演示。" : stages[stage].detail}</p></div>
      <div className={`progress-orbit ${runState}`} style={{ background: `conic-gradient(${runState === "waiting" ? "#d89638" : runState === "done" ? "#36a875" : "#dc8150"} ${progress}%, #dae0dc 0)` }}><strong>{progress}%</strong><span>{runState === "waiting" ? "WAIT" : runState === "done" ? "DONE" : "AUTO"}</span></div>
    </header>

    <section className="flow-canvas">
      <header><span>执行关系画布</span><small>节点状态由前端模拟状态机驱动</small><button onClick={onRestart}>重新演示 ↻</button></header>
      <div className="flow-layout">
        <div className="flow-group input-group"><GroupTitle code="INPUT" name="共同输入" /><FlowNode index="01" title="扫描项目" meta="642 文件 · 8 模块" state={stageStatus(0)} /><Connector /><FlowNode index="02" title="确认关键信息" meta="名称 · 版本 · 截图" state={stageStatus(1)} /></div>
        <div className="branch-arrow"><i /><b>并行</b><i /></div>
        <div className="flow-branches">
          <div className="flow-group"><GroupTitle code="A" name="源码文档线" /><div className="horizontal-nodes"><FlowNode index="03" title="筛选核心源码" meta="A / B / C 分级" state={stageStatus(2)} /><Connector horizontal /><FlowNode index="07" title="源码文档装配" meta="1 + 59 页" state={stage >= 7 ? stageStatus(7) : "pending"} /></div></div>
          <div className="flow-group"><GroupTitle code="B" name="软件说明书线" /><div className="manual-grid"><FlowNode index="04" title="项目证据研究" meta="证据索引" state={stageStatus(3)} /><FlowNode index="05" title="处理界面截图" meta="3 张已审核" state={stageStatus(4)} /><FlowNode index="06A" title="撰写八章正文" meta="章节并行" state={stageStatus(5)} /><FlowNode index="06B" title="生成专业图表" meta="4 张 Draw.io" state={stageStatus(6)} /></div></div>
        </div>
        <div className="branch-arrow merge"><i /><b>汇合</b><i /></div>
        <div className="flow-group finish-group"><GroupTitle code="DELIVERY" name="装配与交付" /><FlowNode index="08" title="装配双文档" meta="DOCX · 图表 · 截图" state={stageStatus(7)} /><Connector /><FlowNode index="09" title="逐页质量检查" meta="78 页真实渲染" state={stageStatus(8)} /><Connector /><FlowNode index="10" title="交付完成" meta="双文档 + 资产" state={stageStatus(9)} /></div>
      </div>
    </section>

    {runState === "waiting" && <section className="decision-panel">
      <div className="decision-icon">?</div><div><span>需要人工确认 · 演示节点</span><h2>是否采用识别到的软件信息？</h2><p>软件名称：霓裳云枢　版本：V1.0　截图：采用 3 张演示图片。确认后继续模拟后续生成，不会启动项目或调用模型。</p></div><button onClick={onConfirm}>采用并继续 <b>→</b></button>
    </section>}

    {runState === "done" && <section className="delivery-panel">
      <div className="delivery-check">✓</div><div className="delivery-copy"><span>模拟交付完成</span><h2>两份 Word 与 7 项辅助资产已准备好</h2><p>这里展示的是前端模拟产物，不会下载或冒充真实申请材料。</p></div><div className="delivery-actions"><button onClick={() => onPreview("source")}>预览源码文档</button><button onClick={() => onPreview("manual")}>预览软件说明书</button></div>
    </section>}

    <section className="execution-section">
      <div className="metrics-row">
        <Metric label="持续时间" value={runState === "done" ? "8 分 42 秒" : `${Math.max(7, stage * 54 + 7)} 秒`} />
        <Metric label="执行节点" value={`${Math.min(stage + 1, 10)} / 10`} />
        <Metric label="已形成产物" value={`${outputCount} / 7`} />
        <Metric label="当前状态" value={runState === "waiting" ? "等待确认" : runState === "done" ? "质量通过" : "模拟处理中"} tone={runState} />
      </div>
      <div className="execution-body">
        <div className="event-column"><header><div><span>执行节点看板</span><small>每一条记录均为模拟数据</small></div><b>{Math.min(stage + 1, 10)} / 10 个节点</b></header><div className="event-list">{stages.slice(0, Math.max(stage + 1, 3)).map((item, index) => <div key={item.name} className={`event-item ${stage === index ? runState : "done"}`}><i>{index < stage || runState === "done" ? "✓" : index === stage && runState === "waiting" ? "!" : index === stage ? "↻" : "·"}</i><div><strong>{item.name}</strong><span>{item.detail}</span></div><small>{index < stage ? "已完成" : index === stage ? runState === "waiting" ? "待确认" : runState === "done" ? "已完成" : "执行中" : "排队中"}</small></div>)}</div></div>
        <aside className="artifact-rail"><header><span>实时产物</span><b>{outputCount}</b></header>{stage < 3 ? <div className="empty-artifacts"><span>◇</span><p>中间产物会随流程逐项出现</p></div> : <div className="artifact-list">{stage >= 3 && <Artifact title="项目证据索引" type="JSON" />}{stage >= 4 && <Artifact title="已审核界面截图" type="3 PNG" />}{stage >= 5 && <Artifact title="八章正文草稿" type="DOC" />}{stage >= 6 && <Artifact title="专业图表资产" type="4 DRAW.IO" onClick={() => onPreview("diagram")} />}{stage >= 7 && <Artifact title="源码文档" type="60 PAGES" onClick={() => onPreview("source")} />}{stage >= 8 && <Artifact title="软件说明书" type="18 PAGES" onClick={() => onPreview("manual")} />}</div>}</aside>
      </div>
    </section>
  </div>;
}

function GroupTitle({ code, name }: { code: string; name: string }) { return <div className="group-title"><span>{code}</span><b>{name}</b></div>; }
function Connector({ horizontal = false }: { horizontal?: boolean }) { return <div className={horizontal ? "connector horizontal" : "connector"}><i /></div>; }

function FlowNode({ index, title, meta, state }: { index: string; title: string; meta: string; state: NodeState }) {
  return <article className={`flow-node ${state}`}><span className="node-index">{state === "done" ? "✓" : index}</span><div><strong>{title}</strong><small>{meta}</small></div><i className="node-state">{state === "waiting" ? "待确认" : state === "running" ? "执行中" : state === "done" ? "已完成" : "排队中"}</i></article>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: RunState }) { return <div className={`metric ${tone || ""}`}><span>{label}</span><strong>{value}</strong></div>; }
function Artifact({ title, type, onClick }: { title: string; type: string; onClick?: () => void }) { return <button className="artifact" onClick={onClick} disabled={!onClick}><i>□</i><span><strong>{title}</strong><small>{type} · 模拟产物</small></span>{onClick && <b>预览</b>}</button>; }

function WorkspacePage({ page, stage, progress, onPreview }: { page: PageKey; stage: number; progress: number; onPreview: (type: "source" | "manual" | "diagram") => void }) {
  const map = {
    overview: ["PROJECT INTELLIGENCE", "项目概览", "查看模拟项目的识别结果、技术栈和执行边界。"],
    source: ["SOURCE MATERIALS", "源码材料", "模拟展示代码筛选、分页预检与源码文档。"],
    manual: ["FORMAL MANUAL", "软件说明书", "模拟展示章节、图表、截图与文档版本。"],
    screenshots: ["SCREENSHOT EVIDENCE", "界面截图", "三张演示图片及其审核状态，不代表真实项目截图。"],
    diagrams: ["DRAW.IO WORKBENCH", "图表资产", "模拟展示可编辑图表的版本和质量状态。"],
    assets: ["LOCAL ASSETS", "我的资产", "模拟展示本次任务形成的双文档与辅助资产。"],
    logs: ["RUN LOGS", "运行日志", "只显示前端生成的演示日志，不读取真实任务。"],
    settings: ["MODEL SETTINGS", "设置", "这里的连接与模型全部是不可提交的演示配置。"],
    quick: ["", "", ""],
  } as const;
  const [eyebrow, title, desc] = map[page];
  return <div className="generic-workspace"><header className="generic-header"><div><span>{eyebrow}</span><h1>{title}</h1><p>{desc}</p></div><div className="mock-pill"><i />模拟工作区</div></header><div className="workspace-content">{renderWorkspace(page, stage, progress, onPreview)}</div></div>;
}

function renderWorkspace(page: PageKey, stage: number, progress: number, onPreview: (type: "source" | "manual" | "diagram") => void) {
  if (page === "overview") return <><div className="overview-grid"><article className="project-card"><span>演示项目</span><h2>霓裳云枢</h2><p>传统服饰文化沉浸式传承平台</p><div><b>V1.0</b><b>642 个文件</b><b>8 个模块</b></div></article><article className="stack-card"><span>识别到的技术栈</span><div><b>Vue 3</b><b>TypeScript</b><b>Spring Boot</b><b>MySQL</b><b>Redis</b><b>对象存储</b></div></article></div><div className="module-table"><header><span>核心模块</span><span>仓库证据</span><span>演示状态</span></header>{["时空衣橱与服饰浏览","AI 智能试衣","3D 模型与 AR 展示","服饰文化社区"].map((x,i)=><div key={x}><strong>{x}</strong><span>src/modules/{["wardrobe","try-on","model","community"][i]}</span><b>已识别</b></div>)}</div></>;
  if (page === "source") return <div className="document-workspace"><aside><span>源码文档计划</span><strong>60 页</strong><p>1 页封面 + 59 页正文</p><div className="mini-progress"><i style={{width:`${Math.max(progress,31)}%`}} /></div><button onClick={()=>onPreview("source")}>打开模拟预览</button></aside><CodePreview /></div>;
  if (page === "manual") return <div className="manual-workspace"><div className="chapter-list"><span>说明书章节</span>{["系统概述与业务边界","总体设计","功能与模块设计","数据与接口设计","运行部署与恢复","安全性与可靠性","用户界面与操作说明","测试与质量检查"].map((x,i)=><button key={x} className={i===2?"active":""}><i>{i+1}</i><span>{x}</span><b>{stage>5?"完成":"模拟"}</b></button>)}</div><div className="manual-preview"><span>正文预览快照</span><h2>3　功能与模块设计</h2><h3>3.1　时空衣橱与服饰浏览模块</h3><p>系统通过分类、搜索和详情页组织传统服饰内容。用户选择服饰后，前端依据路由参数读取服饰详情、关联文化资料与三维模型索引，并将结果呈现在统一的内容结构中。</p><div className="fake-table"><b>功能入口</b><b>处理逻辑</b><b>结果状态</b><span>分类导航</span><span>按服饰类型读取数据</span><span>返回列表与筛选条件</span><span>详情页面</span><span>聚合图文与模型资产</span><span>呈现完整服饰档案</span></div><button onClick={()=>onPreview("manual")}>打开模拟文档预览</button></div></div>;
  if (page === "screenshots") return <div className="shot-gallery">{[["快速开始",siteUrl("landing/assets/quick-start.png")],["自动化执行",siteUrl("landing/assets/orchestration.png")],["逐页质量检查",siteUrl("landing/assets/page-qa.png")]].map(([name,src],i)=><article key={name}><img src={src} alt={name}/><div><span><i />已审核 · 已采用</span><h3>{name}</h3><p>演示截图 {i+1} · 仅用于交互 Demo</p></div></article>)}</div>;
  if (page === "diagrams") return <div className="diagram-workspace"><div className="diagram-list"><span>说明书图表</span>{["系统总体架构拓扑图","核心业务流程图","数据与接口关系图","部署与恢复流程图"].map((x,i)=><button key={x} className={i===0?"active":""}><i>图 {i+1}</i><span>{x}</span><b>✓</b></button>)}</div><div className="diagram-stage"><img src={siteUrl("landing/assets/drawio-workbench.png")} alt="模拟 Draw.io 图表工作台"/><button onClick={()=>onPreview("diagram")}>查看图表模拟预览</button></div></div>;
  if (page === "assets") return <div className="asset-grid"><AssetCard name="霓裳云枢-V1.0-源代码文档.docx" meta="60 页 · 质量检查通过" type="source" onPreview={onPreview}/><AssetCard name="霓裳云枢-V1.0-软件说明书.docx" meta="18 页 · 4 图表 · 3 截图" type="manual" onPreview={onPreview}/><AssetCard name="专业图表资产包" meta="4 Draw.io · 4 SVG · 4 PNG" type="diagram" onPreview={onPreview}/></div>;
  if (page === "logs") return <div className="logs-panel"><header><span>时间</span><span>节点</span><span>演示日志</span></header>{stages.slice(0,stage+1).map((item,i)=><div key={item.name}><time>10:{String(12+i).padStart(2,"0")}:0{i}</time><b>{item.name}</b><span>{item.detail}</span></div>)}</div>;
  if (page === "settings") return <div className="settings-grid"><article><span>正文模型</span><h2>Demo · Text Model</h2><p>仅作为界面占位，不会发送请求。</p><button disabled>测试连接（演示禁用）</button></article><article><span>图表模型</span><h2>Demo · Diagram Model</h2><p>模拟图表语义生成，不调用外部服务。</p><button disabled>测试连接（演示禁用）</button></article><article><span>视觉模型</span><h2>Demo · Vision Model</h2><p>图片能力显示为模拟状态，不上传图片。</p><button disabled>图片测试（演示禁用）</button></article></div>;
  return null;
}

function CodePreview() { const lines=["export async function createTryOnTask(input: TryOnInput) {","  const validated = validateInput(input)","  const task = await repository.create({","    userId: validated.userId,","    status: 'queued',","    sourceImage: validated.sourceImage,","  })","  await queue.enqueue(task.id)","  return task","}"]; return <div className="code-preview"><header><span>src/services/try-on-task.ts</span><b>模拟代码预览</b></header><pre>{lines.map((line,i)=><code key={i}><i>{String(i+1).padStart(2,"0")}</i>{line}</code>)}</pre></div>; }
function AssetCard({name,meta,type,onPreview}:{name:string;meta:string;type:"source"|"manual"|"diagram";onPreview:(type:"source"|"manual"|"diagram")=>void}){return <article className="asset-card"><i>DOCX</i><div><span>模拟资产</span><h3>{name}</h3><p>{meta}</p></div><button onClick={()=>onPreview(type)}>预览</button></article>}

function PreviewModal({ type, onClose }: { type: "source" | "manual" | "diagram"; onClose: () => void }) {
  const title = type === "source" ? "源代码文档模拟预览" : type === "manual" ? "软件说明书模拟预览" : "专业图表模拟预览";
  return <div className="preview-backdrop" onMouseDown={(event)=>{if(event.target===event.currentTarget)onClose()}}><section className="preview-modal"><header><div><span>DEMO PREVIEW</span><h2>{title}</h2></div><button onClick={onClose}>×</button></header>{type === "diagram" ? <div className="modal-diagram"><img src={siteUrl("landing/assets/drawio-workbench.png")} alt="模拟图表预览" /></div> : <div className="paper-preview"><div className="paper-page"><span>霓裳云枢 V1.0</span><h1>{type === "source" ? "源代码文档" : "软件说明书"}</h1><p>本页面为交互 Demo 生成的模拟预览，不是实际软著申请材料。</p><i>{type === "source" ? "共 60 页" : "共 18 页"}</i></div><div className="paper-page body-page">{type === "source" ? <CodePreview /> : <><h2>1　系统概述与业务边界</h2><p>霓裳云枢面向传统服饰文化展示、内容浏览与智能试衣等场景，系统以模块化方式组织前端交互、后端服务和数据资产。</p><h3>1.1　软件目标</h3><p>通过数字化方式组织服饰文化内容，并为用户提供可检索、可浏览、可交互的统一体验。</p></>}</div></div>}<footer><span>模拟内容 · 未连接真实服务</span><button onClick={onClose}>关闭预览</button></footer></section></div>;
}

ReactDOM.createRoot(document.getElementById("demo-root")!).render(<DemoApp />);
