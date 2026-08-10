# 本地资产工作台 API v1

本契约由 `DiagramAssetApi` 实现，领域路由与传输框架解耦；正式 sidecar 使用 FastAPI、Pydantic 和 Uvicorn 解析 HTTP、限制请求体、验证 Schema、注入会话令牌并序列化 `ApiResponse`。

## 安全边界

- 只允许 sidecar 监听 `127.0.0.1` 随机端口。
- 每次桌面应用启动生成至少 32 字符的随机会话令牌。
- 所有 `/api/v1` 请求必须携带该令牌；路由分发前使用常量时间比较验证。
- 不启用通配 CORS；只精确放行 `tauri://localhost`、Windows 使用的 `http://tauri.localhost` 和桌面开发窗口 `http://127.0.0.1:1420`，不接受其他网页来源。
- SVG 预览路径来自已持久化 revision，并再次校验必须位于对应任务目录内。
- 单次保存最多 500 个白名单 overlay 操作。
- FastAPI 传输层限制请求体为 1 MiB，禁用 Swagger/ReDoc 公开页面且不配置通配 CORS。

## 启动握手

Tauri 通过 `COPYRIGHT_AGENT_SESSION_TOKEN` 向 sidecar 传入短生命周期令牌并启动：

```bash
copyright-agent-sidecar --data-dir /path/to/application-data
```

sidecar 只绑定 `127.0.0.1:0`，随后向标准输出写入一行 JSON：

```json
{"event":"sidecar.ready","host":"127.0.0.1","port":52246,"protocol_version":1,"version":"0.1.0","pid":62478}
```

父进程校验 host、协议版本、sidecar 版本、PID 和带令牌的 `/api/v1/health` 后才允许 UI 发起业务请求。

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/projects/scan` | 扫描系统选择器授权的目录或 ZIP，并返回事实摘要 |
| GET | `/api/v1/tasks?limit=20` | 列出最近持久化任务，不返回原始项目路径 |
| POST | `/api/v1/tasks/{task_id}/rescan` | 使用已持久化的项目来源创建新扫描任务 |
| DELETE | `/api/v1/tasks/{task_id}` | 删除任务、SQLite 关联记录与应用内产物，不删除原项目 |
| GET | `/api/v1/tasks/{task_id}/inspection` | 读取任务状态、事实、证据和待确认项 |
| POST | `/api/v1/tasks/{task_id}/confirmations/{field_key}` | 回答待确认项并返回刷新后的任务检查结果 |
| GET | `/api/v1/tasks/{task_id}/source-materials` | 读取源码筛选、分页预检、DOCX 状态和阻塞原因 |
| POST | `/api/v1/tasks/{task_id}/source-materials/source-plan` | 人工触发 A/B/C 源码筛选计划 |
| POST | `/api/v1/tasks/{task_id}/source-materials/code-preview` | 人工触发 59 页代码分页预检 |
| GET | `/api/v1/tasks/{task_id}/source-materials/code-preview/pages` | 读取最新分页产物的首页、中间页和末页样本 |
| POST | `/api/v1/tasks/{task_id}/source-materials/source-docx` | 预检通过后人工触发源代码 DOCX 生成 |
| GET | `/api/v1/model-configs` | 列出不含密钥的本地模型配置与验证状态 |
| POST | `/api/v1/model-configs` | 新增或更新模型连接元数据；API Key 不进入此接口 |
| POST | `/api/v1/model-configs/{config_id}/verified` | 连通性验证通过后记录验证时间 |
| DELETE | `/api/v1/model-configs/{config_id}` | 删除模型配置元数据 |
| GET | `/api/v1/settings` | 读取默认模型、生成参数与文档行为设置 |
| POST | `/api/v1/settings` | 保存并校验应用通用设置 |
| GET | `/api/v1/tasks/{task_id}/manual-workspace` | 读取说明书章节、图表计划和产物状态 |
| POST | `/api/v1/tasks/{task_id}/manual-workspace/manual-plan` | 人工触发说明书章节与证据计划 |
| POST | `/api/v1/tasks/{task_id}/manual-workspace/diagram-plan` | 人工触发图表语义计划 |
| POST | `/api/v1/tasks/{task_id}/manual-workspace/diagram-artifacts` | 两张图就绪后生成 Draw.io 与 SVG 产物 |
| GET | `/api/v1/tasks/{task_id}/diagram-assets` | 读取资产工作台快照 |
| GET | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions` | 列出版本 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions` | 保存人工或 AI 覆盖 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rebase` | 将最新覆盖重放到最新语义图 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rollback` | 将历史操作复制为最新版本 |
| GET | `/api/v1/diagram-revisions/{revision_id}` | 读取 revision 与编辑图数据 |
| POST | `/api/v1/diagram-revisions/{revision_id}/resolve` | 逐项解决 rebase 冲突 |
| GET | `/api/v1/diagram-revisions/{revision_id}/preview.svg` | 获取内置 SVG 预览 |

`diagram_key` 首版只允许 `system_architecture` 和 `core_business_flow`。

扫描请求为 `{"path":"/user-approved/project-or.zip"}`。目录采用原地只读扫描，ZIP 隔离解压到应用数据目录；桌面页面不提供自由路径文本框，只接受系统文件选择器返回值。
重新扫描端点不接受新路径，只从 task ID 反查已授权的持久化项目来源；它创建新任务和快照，不覆盖旧材料，并继承原任务中与新待确认项匹配的标量 confirmed Fact。

源码材料三个 POST 端点均是显式用户操作，不会因读取状态自动执行。代码不足 59 页时预检会持久化为警告，并禁止生成灌水 DOCX。
`source-plan` 支持 `strategy=standard|relaxed|maximum`：标准策略优先 A/B 业务代码，宽松策略补充项目通用支持代码，最大覆盖策略还可纳入测试、Mock、迁移和示例代码。三档均不纳入敏感命中、二进制、vendor、生成文件和压缩代码。
分页样本端点不接受文件路径或页码输入，只从 SQLite 定位当前任务最新产物，并校验产物仍位于该任务数据目录内。
已生成的源代码 DOCX 在快照中附带 `integrity.status`：sidecar 重新计算 SHA-256 并与 SQLite 登记值比对，只有 `verified` 才允许桌面端定位文件。桌面 `reveal_source_document` 命令只接受 task ID，不接受前端文件路径。

## 保存请求

```json
{
  "edit_source": "manual",
  "operations": [
    {
      "action": "node.move",
      "target": "module-service",
      "payload": {"x": 140, "y": 90}
    }
  ]
}
```

`edit_source` 只能是 `manual` 或 `ai`。AI 操作仍经过同一白名单、目标指纹和冲突检查，不能获得额外权限。

## 冲突解决请求

```json
{
  "resolutions": [
    {"operation_index": 0, "resolution": "accept_current"},
    {"operation_index": 2, "resolution": "retarget", "target": "module-new-service"},
    {"operation_index": 3, "resolution": "drop"}
  ]
}
```

冲突必须全部且仅处理一次。解决结果始终创建新 revision。

## 错误结构

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Request schema is invalid"
  }
}
```

认证失败返回 401，资源不存在返回 404，方法不允许返回 405，Schema 或业务约束失败返回 400。
源代码 DOCX 生成阶段失败时，任务会以 `source_document_error` 保留为可重试状态；修复运行环境后可对同一任务再次调用生成接口，无需重新扫描或分页。

## AI 说明书草稿

`POST /api/v1/tasks/{task_id}/manual-workspace/generate`

```json
{"model_config_id":"已启用的模型配置 ID"}
```

该接口读取本地加密凭据，并按模型已识别的 `messages`、`chat_completions`、`responses` 或 `ollama_chat` 协议调用。规则缺失项作为“待确认/禁止编造”约束进入提示词，不作为生成门禁。成功结果版本化写入 `artifacts/manual/manual-draft.vN.md`，工作台快照在 `manual_draft` 返回内容、模型、接口模式、耗时和字符数。

> 上述接口保留为 legacy 草稿验证链路。正式说明书使用下面的版本化任务 API，Markdown 不再作为最终产物。

## 正式说明书生成任务

- `POST /api/v1/tasks/{task_id}/manual-jobs`：创建一个版本化说明书任务。
- `GET /api/v1/tasks/{task_id}/manual-jobs`：列出项目的说明书版本。
- `GET /api/v1/manual-jobs/{job_id}`：读取任务和各阶段状态。
- `POST /api/v1/manual-jobs/{job_id}/research`：执行或重试项目研究阶段。
- `GET /api/v1/manual-jobs/{job_id}/research`：读取最新结构化研究成果。
- `POST /api/v1/manual-jobs/{job_id}/draft`：从研究成果生成全部适用章节。
- `GET /api/v1/manual-jobs/{job_id}/sections`：读取当前章节内容块。
- `POST /api/v1/manual-jobs/{job_id}/sections/{section_key}/regenerate`：独立重新生成一章。
- `PUT /api/v1/manual-jobs/{job_id}/sections/{section_key}`：保存人工修改为新版本。
- `GET /api/v1/manual-jobs/{job_id}/sections/{section_key}/revisions`：读取章节版本历史。
- `POST /api/v1/manual-jobs/{job_id}/figures`：从章节图表请求生成全部图表资产。
- `GET /api/v1/manual-jobs/{job_id}/figures`：读取当前图表资产及质量报告。
- `POST /api/v1/manual-jobs/{job_id}/figures/{figure_key}/regenerate`：独立重新生成一张图。
- `GET /api/v1/manual-jobs/{job_id}/figures/{figure_key}.drawio|svg|png`：安全读取当前图表资产。

创建请求：

```json
{"model_config_id":"已启用的模型配置 ID"}
```

任务包含 `research`、`draft`、`diagrams`、`screenshots`、`assemble_docx`、`render_qa` 六个可独立重试的阶段。正文、图表、截图和 DOCX 使用独立结构化产物表持久化，部分失败不会删除已完成阶段。

研究阶段只读取扫描快照中已通过安全筛选的代表性源码，不会把整个仓库无差别发送给模型。输出包含项目事实、带文件哈希和行号的源码引用、研究结论及分章建议。每条研究结论必须分类为 `verified`、`inference` 或 `pending_confirmation`；模型引用未知证据时，服务会删除无效引用并将伪“已验证”结论降级为待确认。

正文阶段不生成 Markdown 文件，而是保存 `paragraph`、`list`、`table`、`figure_request` 四类结构化内容块。事实性内容的证据引用仍须来自研究输入；无证据内容会标记为 `needs_review`。一次批量生成中的单章失败不会删除其他成功章节，用户可以只重试失败章节。人工编辑不会覆盖 AI 版本，而是新增 `origin=user` 的确认版本。

图表阶段以正文 `figure_request` 和章节证据为输入，模型只生成节点、关系、布局和证据引用等语义；Draw.io XML、SVG 与 2 倍分辨率 PNG 由本地确定性渲染器生成。服务端校验节点唯一性、关系端点、显式正交路由、无节点重叠和文件完整性，并为每次重新生成保留独立版本。PNG 供 Word 插入，`.drawio` 始终是可编辑源文件。
