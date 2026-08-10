# 新会话启动提示词

请完整阅读当前目录的 `PROJECT_HANDOFF.md`，然后检查工作目录现状。

我们要开发一个真正独立运行的“软著 Agent”桌面应用，不依赖 Codex 才能工作，至少支持 macOS 和 Windows。用户选择软件项目、配置模型 Key 后，应用应自动完成项目分析、核心源码筛选、约 60 页源代码 Word、软件说明书 Word、Draw.io 架构图与流程图、可运行项目的页面截图，以及确定性成品质检。

现有 `copyright-doc` 和 `drawio-professional-diagram` 两个 Codex Skill 已验证领域流程和部分确定性脚本，但独立产品需要自行实现 Agent 编排、模型适配、本地工具、浏览器、任务状态、失败恢复和安全边界。已选择“真正独立的 Agent”路线；Codex CLI 只能作为可选高级后端，不能成为必需依赖。

请先和我对齐以下内容，不要立刻大规模编码：

1. 产品定位和 MVP 边界
2. macOS/Windows 跨平台架构
3. Tauri＋本地 FastAPI sidecar 是否合适
4. CLI 引擎优先还是桌面骨架优先
5. ModelProvider、Tool、Artifact、QAResult 和任务状态机接口
6. 现有 Skill 脚本如何迁移并与独立应用共用规则
7. Draw.io、DOCX、LibreOffice、Playwright 和字体的打包方案
8. 第一阶段测试计划和成功标准

关键原则：模型负责理解和写作，分页、格式、路由、截图、渲染、质量门禁和任务恢复尽量由确定性程序负责；不能用测试、mock、依赖、重复或伪造代码凑页数；所有项目事实必须有仓库证据。
