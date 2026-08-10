# Software Copyright Agent

一个完全独立于 Codex、本地优先的软件著作权材料辅助生成工具。目前正在推进 M3 源代码文档纵向切片。

## 当前可运行能力

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

运行测试：

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -v
```

## 当前限制

- 尚未接入模型协议、Draw.io、说明书生成和桌面 UI。
- 当前 CLI 是验证领域内核的纵向切片，不是最终用户界面。
- `.gitignore` 当前实现常用 glob、目录、锚定和否定规则，不承诺覆盖 Git 的全部边缘语义。
- A/B/C 当前是确定性初筛；后续模型只对未排除候选做语义重排，不能恢复安全规则排除项。
- 自动 QA 能阻断结构、页数、尺寸、全空白页和正文纵向覆盖不足问题，但不能替代最终人工视觉抽检；QA 报告会明确保留 `visual_review_required=true`。
- 当前内置字体覆盖常用简体中文；遇到字体 cmap 不包含的扩展字符时会阻断出件，而不是静默显示方框。
- 结构事实提取只覆盖高置信度静态模式；动态路由、运行时拼接 SQL、厂商自定义 ORM 和业务语义仍需后续模型候选与人工确认。

开发计划和每轮进度分别见 `DEVELOPMENT_PLAN.md` 与 `PROGRESS.md`。
