# Software Copyright Agent

一个完全独立于 Codex、本地优先的软件著作权材料生成工具。当前开发版已经贯通 Tauri 桌面端、FastAPI sidecar、SQLite 持久化、模型编排、真实截图证据、专业图表、双 Word 文档生成与逐页质量检查。

面向最终用户的主入口是“快速开始”：一次确认项目、软件名称、版本、截图文件夹、正文/图表/视觉模型、并发数和重试策略后，后台并行生成源代码文档与软件说明书，最后统一装配、质检并交付到“我的资产”。各专业工作台继续保留，用于查看、修订和单节点恢复，而不是主流程的必经操作。

## 当前可运行能力

- “快速开始”无人值守编排：可清空历史运行、从失败节点恢复，同一次运行固定绑定一个项目任务和一个说明书任务，不会在重试时无故创建 v2/v3 整单任务。
- 双文档并行：确认项目事实后，源代码文档线与软件说明书线独立推进；单支失败不会抹去另一支已经形成的产物。
- 可视执行画布：展示共同准备、源码文档线、软件说明书线和汇合交付；节点状态、重试、耗时、上下游关系及产物均可就地查看。
- 快速预览：正文、图表、真实截图、项目研究结果、审阅稿和终稿可在快速开始页以模态框浏览，需要修改时再进入对应工作台。
- 设置页支持正文、图表和已通过真实图片能力测试的默认视觉模型，并允许专业用户配置文档风格与绘图风格提示词。

- 扫描本地项目目录。
- 安全导入 ZIP 项目并在任务目录中隔离解压。
- 跳过常见依赖、构建、版本控制目录和敏感文件。
- 应用项目根和嵌套 `.gitignore` 的核心规则，并记录忽略原因。
- 跳过符号链接，避免越出项目根目录。
- 为文件生成 SHA-256，并建立稳定的项目指纹。
- 识别常见源码语言和二进制文件。
- 检测常见秘密特征，仅报告规则、路径和行号，不保存命中值。
- 使用文件数量、单文件和扫描总字节预算阻断超大输入。
- 将项目、快照、任务、阶段和事件写入 SQLite。
- 将扫描 manifest 原子写入独立任务目录。
- 通过 Repository／UnitOfWork 管理事务边界。
- 使用合法状态转换和 `row_version` 乐观并发控制任务生命周期。
- 扫描失败时持久化安全错误和失败阶段，便于后续恢复或重试。
- 从 `package.json`、`pyproject.toml`、`Cargo.toml` 和 README 确定性提取名称与版本候选。
- 根据语言、依赖清单和源码目录生成技术栈及模块候选。
- 将 Fact、Evidence 和待确认项持久化到 SQLite。
- 使用 `inspect` CLI 查看事实值、置信度和证据关联。
- 将确定性分页预览生成 A4 DOCX：1 页封面 + 59 页源代码正文。
- 对 DOCX 运行版本化记录和 SHA-256 留痕；代码不足时阻断出件。
- 内置并嵌入 Noto Sans CJK SC 字体，中文名称、页眉页脚和中文源码不依赖系统字体。
- 从已确认 Fact/Evidence 生成版本化的软件说明书章节计划、缺失信息清单和图表需求。
- 在受限读取预算内确定性识别数据库技术、SQL 表、HTTP 路由、状态枚举和部署描述文件，并保存文件哈希证据。
- 只保存环境变量名称，并提取显式接口模型、HTTP 错误状态、程序入口和测试框架，不保存配置值或脚本命令正文。
- 从显式状态转换表生成证据化图表语义计划；所有边必须关联 Fact、Evidence、源文件和行号，缺少依赖边时阻断绘图。
- 使用 Python AST 提取项目内部 import 图，并确定性选择最多 12 个核心模块和 20 条核心依赖作为架构图视图；完整依赖事实仍保留在 SQLite。
- 从用户授权的截图文件夹导入 PNG/JPG/WebP，进行真实视觉模型解读、人工/自动审核采用、敏感状态确认、版本管理和第 7 章装配，不把项目图片素材误当成界面证据。
- 说明书按“项目证据研究 → 截图证据 → 正文与专业图表并行 → 统一装配 → 逐页 QA”执行；专业工作台把审阅与人工终稿分开，快速开始则依据启动前的明确授权自动完成终稿。
- 最终说明书移除过程提示、审阅建议和“终稿”字样，统一正文颜色、标题、缩进和截图章节编号；质量门禁可识别只有页眉页脚的正文空白页。
- “我的资产”按项目集中管理源码文档、软件说明书、图表和截图；最近任务只表达执行状态，不承担项目删除与资产管理。

## 运行

要求 Python 3.9 或更高版本，并安装项目依赖（当前 DOCX 生成使用 `python-docx`）。

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  scan /path/to/project --json
```

查看最近任务提取的事实、证据和待确认项：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  inspect --json
```

也可以在 `inspect` 后传入指定的任务 ID。

回答待确认项：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  confirm TASK_ID project.version V1.0 --json
```

用户答案会生成新的确认 Evidence 和 confirmed Fact；旧候选保留为 `superseded`，不会覆盖历史。

生成核心源码 A/B/C 计划：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  source-plan TASK_ID --json
```

计划会确定性排除测试、mock、fixture、vendor、migration、demo、sample、generated、二进制和检测到秘密的源码，并为其余文件保存评分与理由。

生成 59 页代码正文的确定性分页预览：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  code-preview TASK_ID --json
```

预览会重新校验源码哈希，按视觉宽度硬换行，并报告代码是否足够。代码不足时只生成实际页数，不补空行、不重复代码。

生成 60 页源代码 DOCX：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  source-docx TASK_ID --json
```

DOCX 使用最新的完整分页预览；每次执行创建新版本，并在 SQLite 保存模板参数、摘要、路径和文件哈希。

执行源代码 DOCX 自动质量门禁：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  qa-source-docx TASK_ID --json
```

门禁会验证产物哈希、A4 分节、显式分页、页码字段、内嵌字体结构、字体与许可证哈希、所需 Unicode 字形、实际 60 页、页面尺寸一致性、空白页和源码正文纵向覆盖率。可用 `COPYRIGHT_AGENT_SOFFICE=/path/to/soffice` 指定应用自带或外部 LibreOffice；未通过时命令返回退出码 3，渲染器故障返回退出码 2。

生成证据关联的软件说明书章节计划：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  manual-plan TASK_ID --json
```

计划包含 9 个标准章节、Fact/Evidence 引用、缺失信息及系统架构图和核心业务流程图需求。本阶段只规划内容，不生成未经证据支持的正文。

生成图表语义计划：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  diagram-plan TASK_ID --json
```

该命令只生成节点和边的证据化 JSON 契约，不直接绘制 Draw.io。重复节点、悬空边或无证据边会阻断计划落库。

生成可编辑 Draw.io 与 SVG 预览：

```bash
PYTHONPATH=src python3 -m software_copyright_agent \
  --data-dir .software-copyright-agent \
  drawio TASK_ID --json
```

生成器使用未压缩 Draw.io XML、确定性布局和显式正交路由，导出前检查节点重叠、边端点及路径点。SVG 默认由应用内置的标准库渲染器导出，不要求安装 Draw.io Desktop；外部 Draw.io 渲染器仅保留为开发期兼容性对照。每次结果都在 SQLite 中版本化留痕。

图表资产修改采用非破坏覆盖层：移动、缩放、样式、显示名称、隐藏和边路由操作单独版本化，原始语义节点、关系和 Evidence 不被覆盖。项目重新生成后，目标消失或语义改变的操作进入冲突状态，等待用户选择，不会静默套用。

应用服务提供资产工作台快照、revision 列表与读取、重新基线化、回滚及冲突解决。冲突可以逐项放弃、接受当前语义或重新指定目标；每个 revision 同时生成独立 `.drawio` 和内置 SVG 预览，桌面 UI 无需直接读取 SQLite。

运行测试：

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -v
```

启动桌面应用使用的本地 FastAPI sidecar：

```bash
COPYRIGHT_AGENT_SESSION_TOKEN="至少32字符的每次启动随机令牌" \
  copyright-agent-sidecar --data-dir .software-copyright-agent
```

sidecar 只绑定 `127.0.0.1` 随机端口，并在标准输出写入单行 JSON 握手。FastAPI、Pydantic 和 Uvicorn 是正式运行依赖，会随应用 sidecar 一起打包；它们不属于需要用户额外安装的外部程序。

## 桌面安装包构建

先安装锁定依赖并冻结当前平台 Sidecar，再调用 Tauri：

```bash
python -m pip install -e ".[test,packaging]"
pnpm install --frozen-lockfile
python scripts/build_sidecar.py
pnpm tauri build
```

正式前端构建由 `scripts/build_frontend.mjs` 直接调用仓库内 TypeScript 与 Vite，打包阶段不会再次联网切换包管理器。macOS 与 Windows 的原生构建、安装包类型、Sidecar 生命周期和签名边界见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。

开发资产工作台前端：

```bash
pnpm install
pnpm dev
```

生产前端可用 `pnpm build` 构建；在离线环境中也可直接运行 `./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build`。Tauri 开发运行还需要 Rust 工具链，并需要先把 Python sidecar 构建为 `src-tauri/binaries/copyright-agent-sidecar-<target-triple>`。

构建当前平台的 sidecar external binary：

```bash
python3 -m pip install -e '.[packaging]'
python3 scripts/build_sidecar.py
```

脚本优先读取 `rustc -vV` 的 host triple；未安装 Rust 时会根据受支持的平台和 CPU 推断。产物严格使用 Tauri external binary 命名，并生成独立 SHA-256 manifest。跨平台发行应分别在对应操作系统构建，不能在单一机器上伪造其他平台可执行文件。

检查并运行 Tauri 桌面端：

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo test --manifest-path src-tauri/Cargo.toml
pnpm tauri:dev:stable
```

`tauri:dev:stable` 保留前端热更新，但关闭 Rust/Sidecar 文件监视重启，适合长任务与截图采集验收；窗口标题会明确显示“开发版”，避免与 `/Applications` 中的旧安装版混淆。需要调试 Rust 热重载时仍可单独使用 `pnpm tauri dev`。

Rust、Node 和 Python 只属于源码构建环境；发行给最终用户的是包含 WebView 桌面壳和冻结 sidecar 的预编译安装包，不要求用户另装这些开发工具。

## 当前限制

- 当前为开发验收版；macOS 开发运行已完成端到端验证，但公开分发仍需 Apple Developer ID、公证和 Windows 实机/签名门禁。
- 快速开始要求用户事先提供真实界面截图文件夹并确认敏感信息与自动采用授权；系统不会为了“全自动”而擅自启动项目、登录账号或制造截图。
- 内置逐页预览是与 DOCX 同源的确定性 companion render，不冒充 Microsoft Word 原生排版；正式发布仍应保留 Word/WPS 人工抽检。
- CLI 保留用于领域内核诊断和自动化测试，最终用户应使用桌面界面。
- `.gitignore` 当前实现常用 glob、目录、锚定和否定规则，不承诺覆盖 Git 的全部边缘语义。
- A/B/C 源码筛选以确定性安全规则为底线；模型和策略不能恢复秘密命中、二进制、vendor 或生成文件等硬排除项。
- 自动 QA 能阻断结构、页数、尺寸、全空白页和正文纵向覆盖不足问题，但不能替代最终人工视觉抽检；QA 报告会明确保留 `visual_review_required=true`。
- 当前内置字体覆盖常用简体中文；遇到字体 cmap 不包含的扩展字符时会阻断出件，而不是静默显示方框。
- 结构事实提取只覆盖高置信度静态模式；动态路由、运行时拼接 SQL、厂商自定义 ORM 和业务语义仍需后续模型候选与人工确认。

开发计划、每轮进度和本地资产 API 分别见 `DEVELOPMENT_PLAN.md`、`PROGRESS.md` 与 `LOCAL_API.md`。
