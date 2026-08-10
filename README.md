# Software Copyright Agent

一个完全独立于 Codex、本地优先的软件著作权材料辅助生成工具。目前处于 M1 领域内核开发阶段。

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

## 运行

当前内核没有第三方 Python 依赖，要求 Python 3.9 或更高版本。

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

运行测试：

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -v
```

## 当前限制

- 尚未接入模型协议、代码筛选、DOCX、Draw.io 和桌面 UI。
- 当前 CLI 是验证领域内核的纵向切片，不是最终用户界面。
- `.gitignore` 当前实现常用 glob、目录、锚定和否定规则，不承诺覆盖 Git 的全部边缘语义。
- A/B/C 当前是确定性初筛；后续模型只对未排除候选做语义重排，不能恢复安全规则排除项。

开发计划和每轮进度分别见 `DEVELOPMENT_PLAN.md` 与 `PROGRESS.md`。
