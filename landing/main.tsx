import { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

type IconName = "scan" | "code" | "book" | "diagram" | "camera" | "shield" | "database" | "spark";

const chapters = [
  { time: 0, label: "导入项目" },
  { time: 12, label: "自动化运行" },
  { time: 52, label: "研究与截图" },
  { time: 100, label: "说明书正文" },
  { time: 132, label: "装配与质检" },
  { time: 205, label: "源码材料" },
  { time: 250, label: "WPS 成品" },
];

const releaseUrl = "https://github.com/Userwei0418/software-copyright-agent/releases/tag/v0.1.0";
const macDownloadUrl = "https://github.com/Userwei0418/software-copyright-agent/releases/download/v0.1.0/Software-Copyright-Agent_0.1.0_macOS-arm64.dmg";
const windowsDownloadUrl = "https://github.com/Userwei0418/software-copyright-agent/releases/download/v0.1.0/Software-Copyright-Agent_0.1.0_Windows-x64-setup.exe";
const demoVideoUrl = "https://github.com/Userwei0418/software-copyright-agent/releases/download/v0.1.0/software-copyright-agent-demo-4m53.mov";

function siteUrl(path: string) {
  return `${import.meta.env.BASE_URL}${path.replace(/^\/+/, "")}`;
}

const capabilities: Array<{ icon: IconName; eyebrow: string; title: string; text: string; wide?: boolean }> = [
  { icon: "scan", eyebrow: "项目扫描", title: "先读懂项目，再动笔", text: "从目录或 ZIP 识别技术栈、模块、路由、运行方式与证据来源；不凭文件名虚构功能。", wide: true },
  { icon: "code", eyebrow: "源码材料", title: "确定性排出 60 页源码文档", text: "代码分级、敏感信息过滤、视觉宽度换行与固定分页协同，避免 Word 自动换行导致页数失控。" },
  { icon: "book", eyebrow: "软件说明书", title: "八章正文按真实证据生成", text: "正文、表格、图注和界面说明围绕项目事实展开；缺证据时明确提示，不把推断包装成事实。" },
  { icon: "diagram", eyebrow: "专业图表", title: "Draw.io 可编辑，不只给一张图", text: "架构图与流程图同时交付 Draw.io、SVG 和 PNG；布局、标签与连线路由进入自动检查。" },
  { icon: "camera", eyebrow: "真实截图", title: "截图先审核，再进入说明书", text: "支持候选截图、视觉解读、页面分组与人工采用。未授权启动或未经审核的图片不会静默写入文档。" },
  { icon: "database", eyebrow: "本地优先", title: "任务、版本和资产都能恢复", text: "SQLite 持久化任务与版本；页面切换不丢长任务，失败节点可定位、重试，已完成产物继续保留。" },
  { icon: "spark", eyebrow: "模型接入", title: "多供应商、多模型、多协议", text: "正文、图表和视觉模型可分别配置。图片能力必须经过真实请求验证，外部模型调用由用户明确授权。", wide: true },
];

function EmbeddedPipelineShowcase() {
  const showcaseRef = useRef<HTMLDivElement>(null);
  const [started, setStarted] = useState(false);
  const [replayKey, setReplayKey] = useState(0);

  useEffect(() => {
    const node = showcaseRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setStarted(true);
        observer.disconnect();
      }
    }, { threshold: 0.18 });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return <div id="pipeline-demo" ref={showcaseRef} className="embedded-showcase reveal">
    <div className="embedded-showcase-heading">
      <div><span>真实交互界面 · DEMO MOCK 数据</span><h3>从导入项目，到拿到双文档</h3><p>这里直接运行全尺寸 Demo 的同一套界面和状态机，不再重新仿造一套展示 UI。进入视口后开始，遇到人工判断时可直接在界面内继续。</p></div>
      <a href={siteUrl("demo/")}>打开全尺寸体验 <Arrow /></a>
    </div>
    <div className="embedded-app-window">
      <div className="embedded-window-chrome"><span /><span /><span /><b>软著材料助手 · 交互演示</b><em>MOCK DATA</em></div>
      <div className="embedded-frame-wrap">
        {started ? <iframe key={replayKey} src={siteUrl("demo/?embed=1")} title="软著材料助手全尺寸交互 Demo 嵌入视图" /> : <div className="embedded-loading"><i /><span>交互 Demo 进入视口后开始运行</span></div>}
      </div>
    </div>
    <div className="embedded-showcase-actions"><span><i />同一套 Demo 界面与流程状态机</span><button onClick={() => { setStarted(true); setReplayKey((value) => value + 1); }}>重新播放流程</button><a href={siteUrl("demo/")}>打开全尺寸体验</a></div>
  </div>;
}

function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [activeChapter, setActiveChapter] = useState(0);
  const [introVisible, setIntroVisible] = useState(true);
  const [claimIndex, setClaimIndex] = useState(0);
  const claims = ["可审阅", "可追溯", "可交付"];

  useEffect(() => {
    const introTimer = window.setTimeout(() => setIntroVisible(false), 1450);
    const claimTimer = window.setInterval(() => setClaimIndex((value) => (value + 1) % claims.length), 2200);
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => entry.isIntersecting && entry.target.classList.add("is-visible"));
    }, { threshold: 0.12 });
    document.querySelectorAll(".reveal").forEach((node) => observer.observe(node));
    return () => {
      observer.disconnect();
      window.clearTimeout(introTimer);
      window.clearInterval(claimTimer);
    };
  }, []);

  function openDemo(time = 0) {
    const video = videoRef.current;
    document.getElementById("demo")?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (!video) return;
    video.currentTime = time;
    window.setTimeout(() => void video.play(), 520);
  }

  function jumpToChapter(index: number) {
    setActiveChapter(index);
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = chapters[index].time;
    void video.play();
  }

  return <div className="landing-shell">
    <div className={`intro-screen ${introVisible ? "visible" : "hidden"}`} aria-hidden={!introVisible}>
      <div className="intro-seal">著</div>
      <p>软著材料助手</p>
      <div className="intro-progress"><i /></div>
      <small>SOFTWARE EVIDENCE STUDIO</small>
    </div>
    <header className="topbar">
      <a className="wordmark" href="#top" aria-label="软著材料助手首页">
        <span className="wordmark-icon">著</span>
        <span><strong>软著材料助手</strong><small>本地证据化工作台</small></span>
      </a>
      <nav aria-label="页面导航">
        <a href="#output">成品</a>
        <a href="#demo">演示</a>
        <a href="#pipeline">流程</a>
        <a href="#quality">质检</a>
      </nav>
      <a className="nav-cta" href="#download">下载安装 <Arrow /></a>
    </header>

    <main id="top">
      <section className="hero section-wrap">
        <div className="hero-orbit orbit-one" /><div className="hero-orbit orbit-two" />
        <div className="hero-copy reveal is-visible">
          <div className="status-pill"><i /> SOFTWARE EVIDENCE STUDIO <span /> v0.1.0</div>
          <h1>软著材料助手</h1>
          <p className="hero-role-line">把一个软件项目，变成一套 <span key={claimIndex}>{claims[claimIndex]}</span> 的材料</p>
          <p className="hero-description">代码扫描、证据研究、源码筛选、说明书撰写、专业绘图、界面截图与逐页质检，在一条本地工作流里完成。</p>
          <div className="hero-actions">
            <a className="primary-button" href={siteUrl("demo/")}><Icon name="spark" />打开交互 Demo</a>
            <button className="secondary-button" onClick={() => openDemo(0)}><Play />观看 4:53 完整录屏</button>
          </div>
          <p className="release-note">本地优先 · 人工确认 · 证据可追溯</p>
          <div className="scroll-indicator"><span>滚动探索</span><i><b /></i></div>
        </div>

        <div className="hero-stage reveal" aria-label="软著材料助手快速开始界面">
          <div className="window-chrome"><span /><span /><span /><b>软著材料助手 · 快速开始</b><em>LOCAL</em></div>
          <img src={siteUrl("landing/assets/quick-start-hero-hd.png")} alt="软著材料助手快速开始高清真实界面" />
          <div className="floating-note note-a"><i className="pulse" /><span><b>一次配置</b><small>两条文档线自动推进</small></span></div>
          <div className="floating-note note-b"><Icon name="shield" /><span><b>明确授权</b><small>确认后才启动外部处理</small></span></div>
        </div>

        <div className="proof-strip reveal">
          <div><strong>60</strong><span>页源码文档<small>本次演示结果</small></span></div>
          <div><strong>18</strong><span>页软件说明书<small>本次演示结果</small></span></div>
          <div><strong>按需</strong><span>图表与截图<small>图表按章节 · 截图按用户素材</small></span></div>
          <div><strong>183</strong><span>项 Python 回归<small>定稿候选基线</small></span></div>
        </div>
      </section>

      <section id="output" className="section-wrap output-section">
        <SectionHeading eyebrow="真实交付结果" title={<>先看最后拿到什么，<span>再看材料如何形成</span></>} text="下面展示的是本次完整演示中的真实产品界面与 WPS 成品，不是重新绘制的概念稿。" />
        <div className="output-grid">
          <article className="output-card output-large reveal">
            <div className="card-copy"><span className="tag">双文档交付</span><h3>源码材料与软件说明书，分别成册</h3><p>源码文档严格控制页数和代码密度；说明书包含项目架构、模块设计、数据交互、界面截图、运行部署与质量检查。</p><ul><li>真实 DOCX，可继续编辑</li><li>页眉、页码、目录、表格与图注统一</li><li>Word / WPS 打开复核</li></ul></div>
            <figure><img src={siteUrl("landing/assets/wps-result.png")} alt="WPS 中打开的 18 页软件说明书成品" /><figcaption><i /> WPS 中打开的 18 页软件说明书</figcaption></figure>
          </article>
          <article className="output-card reveal">
            <div className="card-copy"><span className="tag cyan">版本化交付</span><h3>不是“生成完就消失”</h3><p>正文、审阅稿、终稿和 QA 结果在同一任务中留存。失败节点单独重试，不覆盖已经完成的资产。</p></div>
            <figure><img src={siteUrl("landing/assets/manual-delivery.png")} alt="软件说明书生成与版本交付界面" /><figcaption><i /> 说明书装配与版本记录</figcaption></figure>
          </article>
          <article className="output-card reveal">
            <div className="card-copy"><span className="tag cyan">一键工作流</span><h3>一次配置，两条材料线并行</h3><p>项目、模型、并发、截图和授权边界集中确认，随后由任务画布持续呈现真实进度。</p></div>
            <figure><img src={siteUrl("landing/assets/quick-start.png")} alt="快速开始配置界面" /><figcaption><i /> 快速开始 · 人机判断前置</figcaption></figure>
          </article>
          <article className="output-card output-wide reveal">
            <div className="card-copy"><span className="tag">可编辑图表</span><h3>Draw.io、SVG、PNG 一起交付</h3><p>模型理解结构，本地生成专业图表；候选可人工编辑、AI 辅助调整和版本恢复。</p></div>
            <figure><img src={siteUrl("landing/assets/drawio-workbench.png")} alt="内嵌 Draw.io 专业图表工作台" /><figcaption><i /> Draw.io 工作台 · 确认后装配</figcaption></figure>
          </article>
        </div>
      </section>

      <section id="demo" className="demo-section">
        <div className="section-wrap">
          <div className="demo-heading-row">
            <SectionHeading eyebrow="完整产品演示" title={<>4 分 53 秒，看完<span>从项目到 Word</span></>} text="这段视频连续记录一次真实流程：导入项目、运行自动化、查看研究与截图、生成材料，并最终在 WPS 中打开成品。" />
            <a className="demo-full-link" href={siteUrl("demo/")}>打开全尺寸交互体验 ↗<small>模拟数据 · 无需配置服务</small></a>
          </div>
          <div className="video-frame reveal">
            <div className="video-topline"><span><i /> PRODUCT WALKTHROUGH</span><b>4:53 · 原始清晰度 · 完整录屏</b></div>
            <video ref={videoRef} controls preload="metadata" poster={siteUrl("landing/assets/quick-start.png")} onTimeUpdate={(event) => {
              const current = event.currentTarget.currentTime;
              let next = 0;
              chapters.forEach((chapter, index) => { if (current >= chapter.time) next = index; });
              if (next !== activeChapter) setActiveChapter(next);
            }}>
              <source src={demoVideoUrl} type="video/quicktime" />
              你的浏览器暂不支持视频播放。
            </video>
            <div className="video-chapters" aria-label="视频章节">
              {chapters.map((chapter, index) => <button key={chapter.time} className={index === activeChapter ? "active" : ""} onClick={() => jumpToChapter(index)}><span>{String(index + 1).padStart(2, "0")}</span>{chapter.label}</button>)}
            </div>
          </div>
        </div>
      </section>

      <section id="pipeline" className="section-wrap pipeline-section">
        <SectionHeading eyebrow="自动化执行链" title={<>一份输入，两条材料线，<span>最后统一交付</span></>} text="源码文档和软件说明书共享项目事实，但使用各自的生成与检查链路。所有长任务持续落盘，页面切换不会中止执行。" />
        <div className="pipeline-board reveal">
          <div className="pipeline-start"><b>共同输入</b><span><Icon name="scan" />扫描项目</span><i /><span><Icon name="shield" />确认关键信息</span></div>
          <div className="pipeline-lanes">
            <div className="lane lane-a"><header><em>A</em><span><b>源码文档线</b><small>确定性选择、换行与分页</small></span></header><div><span>筛选核心代码</span><i /><span>代码分页预检</span><i /><span>生成源码文档</span></div></div>
            <div className="lane lane-b"><header><em>B</em><span><b>软件说明书线</b><small>研究、正文、图表与截图证据</small></span></header><div><span>项目证据研究</span><i /><span>截图理解与审核</span><i /><span>正文 + 图表并行</span></div></div>
          </div>
          <div className="pipeline-finish"><span><Icon name="book" /><b>装配双文档</b></span><i /><span><Icon name="shield" /><b>逐页质量门禁</b></span><i /><span className="success"><Check /><b>交付完成</b></span></div>
        </div>
        <EmbeddedPipelineShowcase />
      </section>

      <section className="workbench-section">
        <div className="section-wrap split-feature">
          <div className="split-copy reveal"><span className="section-kicker">DRAW.IO WORKBENCH</span><h2>图不是附件，<br /><span>是可以继续工作的资产</span></h2><p>模型负责理解语义，本地程序负责构建 Draw.io、SVG 和 PNG。你可以在内嵌画布中人工微调，也可以让 AI 对当前图做受控修改。</p><ul><li><Check /> XML 源文件保留可编辑结构</li><li><Check /> 连线、标签、越界与碰撞自动检查</li><li><Check /> 确认后的 PNG 才装配进 Word</li></ul><button className="text-button" onClick={() => openDemo(132)}>观看材料生成与装配片段 <Arrow /></button></div>
          <div className="workbench-card reveal"><img src={siteUrl("landing/assets/drawio-workbench.png")} alt="内嵌 Draw.io 图表工作台真实界面" /><div className="workbench-label"><span><i /> 可编辑 Draw.io</span><span>AI 候选 + 人工确认</span></div></div>
        </div>
      </section>

      <section className="section-wrap capability-section">
        <SectionHeading eyebrow="核心能力" title={<>不是写两份文档，<span>而是管理整条证据链</span></>} text="模型智能、Agent 编排、本地工具与确定性质检各司其职；任何一个环节都不是只靠提示词“碰运气”。" />
        <div className="capability-grid">
          {capabilities.map((item) => <article key={item.title} className={`capability-card reveal ${item.wide ? "wide" : ""}`}><div className="capability-icon"><Icon name={item.icon} /></div><span>{item.eyebrow}</span><h3>{item.title}</h3><p>{item.text}</p></article>)}
        </div>
      </section>

      <section id="quality" className="quality-section">
        <div className="section-wrap">
          <SectionHeading eyebrow="交付前质量门禁" title={<>不是“生成成功”，而是<span>先确认能不能交</span></>} text="说明书会真实渲染并逐页检查；关键问题未通过时不会误报完成，而是定位到页面、资产或执行节点。" />
          <div className="quality-grid">
            <div className="quality-shot reveal"><img src={siteUrl("landing/assets/page-qa.png")} alt="18 页软件说明书逐页预览与质量检查" /><div className="page-counter"><b>17 / 18</b><span>逐页真实渲染</span></div></div>
            <div className="gate-list reveal">
              <article className="gate danger"><i /><div><span>阻断交付</span><h3>空白页、敏感信息、关键证据缺失</h3><p>正文区域空白、文件损坏、截图真实性或安全项失败时，必须处理后才能通过。</p></div></article>
              <article className="gate warning"><i /><div><span>等待确认</span><h3>项目事实、截图采用、模型外发</h3><p>无法安全推断的信息交给人确认；截图与外部模型调用不做隐式授权。</p></div></article>
              <article className="gate success"><i /><div><span>检查通过</span><h3>分页、图表、截图、目录与版式一致</h3><p>DOCX 落盘、Draw.io 专业校验、图片引用、表格与页眉页脚共同纳入检查。</p></div></article>
            </div>
          </div>
        </div>
      </section>

      <section className="section-wrap boundary-section">
        <div className="boundary-card reveal">
          <div className="boundary-copy"><span className="section-kicker">LOCAL-FIRST BOUNDARY</span><h2>文件留在电脑，<br /><span>需要外发的内容单独说明</span></h2><p>原始项目、SQLite 数据、文档、Draw.io 和截图资产存放本机。只有在你授权模型任务后，对应文本或图片才会发送到你配置的模型服务；不经过软著材料助手自有服务器。</p></div>
          <div className="boundary-diagram">
            <div className="local-zone"><span>你的电脑</span><div><Icon name="database" /><b>项目与任务库</b><small>SQLite · 本地文件</small></div><div><Icon name="book" /><b>DOCX / Draw.io</b><small>版本与 QA 留痕</small></div></div>
            <div className="permission-line"><i /><span>你确认后</span><i /></div>
            <div className="model-zone"><Icon name="spark" /><b>你绑定的模型服务</b><small>仅处理获授权的文本或图片</small></div>
          </div>
        </div>
      </section>

      <section id="faq" className="section-wrap faq-section">
        <SectionHeading eyebrow="常见问题" title={<>开始之前，<span>先把边界说清楚</span></>} />
        <div className="faq-list">
          <Faq question="它会自动提交软件著作权申请吗？" answer="不会。产品负责生成、检查和导出申请材料，不登录政务系统，也不替用户提交。最终信息确认与正式申报仍由用户完成。" />
          <Faq question="没有模型配置，可以直接使用吗？" answer="可以先导入和扫描项目、管理已有资产，但项目研究、正文、图表语义或截图理解等模型任务需要配置可用模型，并在执行前获得相应授权。" />
          <Faq question="能保证任何项目都生成固定 60 页源码吗？" answer="不会用依赖、测试、Mock 或重复代码凑页。核心代码充足时按 1 页封面 + 59 页正文生成；代码不足时会如实说明，而不是伪造内容。" />
          <Faq question="界面截图会自动启动我的项目吗？" answer="不会无条件启动。系统只识别受控的本地启动脚本，并要求显式授权；也可以直接导入真实截图，经过分析、人工审核和采用后再进入说明书。" />
          <Faq question="当前可以下载安装吗？" answer="可以。v0.1.0 已提供 Apple Silicon Mac 安装包与 Windows x64 安装包；其他系统架构暂未提供，请从下方下载区或 GitHub Release 获取。" />
        </div>
      </section>

      <section id="download" className="section-wrap download-section reveal">
        <div className="download-copy"><span className="section-kicker">DOWNLOAD · v0.1.0</span><h2>下载安装到本地，<br /><span>从真实项目开始</span></h2><p>选择与你的电脑匹配的安装包。当前版本提供 Apple Silicon Mac 与 Windows x64 两种构建。</p><a href={releaseUrl} target="_blank" rel="noreferrer">查看 GitHub Release 与版本说明 <Arrow /></a></div>
        <div className="download-cards">
          <a className="download-card" href={macDownloadUrl} target="_blank" rel="noreferrer">
            <i>⌘</i><span><small>macOS · ARM64</small><h3>Apple Silicon</h3><p>适用于 M 系列芯片 Mac · DMG</p></span><strong>下载 <Arrow /></strong>
          </a>
          <a className="download-card" href={windowsDownloadUrl} target="_blank" rel="noreferrer">
            <i>⊞</i><span><small>WINDOWS · X64</small><h3>Windows 版</h3><p>适用于 64 位 Windows · EXE</p></span><strong>下载 <Arrow /></strong>
          </a>
        </div>
      </section>

      <section className="section-wrap final-cta reveal">
        <div><span>完整演示 · 4:53</span><h2>从选择项目，到打开两份 Word 成品</h2><p>先看真实流程，再决定它是否适合你的软著材料工作。</p></div>
        <div className="final-actions"><a className="primary-button" href={siteUrl("demo/")}><Icon name="spark" />打开交互 Demo</a><button className="secondary-button" onClick={() => openDemo(0)}><Play />播放录屏</button></div>
      </section>
    </main>

    <footer><div className="section-wrap"><a className="wordmark" href="#top"><span className="wordmark-icon">著</span><span><strong>软著材料助手</strong><small>本地证据化工作台</small></span></a><p>本地运行 · 证据可追溯</p><span>© 2026 商汤-生态渠道-FDE-周玮</span></div></footer>
  </div>;
}

function SectionHeading({ eyebrow, title, text }: { eyebrow: string; title: React.ReactNode; text?: string }) {
  return <header className="section-heading reveal"><span className="section-kicker">{eyebrow}</span><h2>{title}</h2>{text && <p>{text}</p>}</header>;
}

function Faq({ question, answer }: { question: string; answer: string }) {
  return <details className="reveal"><summary>{question}<span /></summary><p>{answer}</p></details>;
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    scan: <><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="12" r="3"/></>,
    code: <><path d="m8 9-3 3 3 3M16 9l3 3-3 3M14 5l-4 14"/></>,
    book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/><path d="M8 7h8M8 11h6"/></>,
    diagram: <><rect x="3" y="3" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><rect x="15" y="3" width="6" height="5" rx="1"/><path d="M9 5.5h6M18 8v8M9 5.5v13H15"/></>,
    camera: <><path d="M14.5 4 16 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3l1.5-3h5Z"/><circle cx="12" cy="13" r="3"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></>,
    spark: <><path d="m12 3-1.4 4.2a5 5 0 0 1-3.2 3.2L3 12l4.4 1.6a5 5 0 0 1 3.2 3.2L12 21l1.4-4.2a5 5 0 0 1 3.2-3.2L21 12l-4.4-1.6a5 5 0 0 1-3.2-3.2L12 3Z"/></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function Arrow() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M11 6l4 4-4 4" /></svg>; }
function Play() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 5 8 5-8 5V5Z" /></svg>; }
function Check() { return <svg className="check-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg>; }

ReactDOM.createRoot(document.getElementById("landing-root")!).render(<App />);
