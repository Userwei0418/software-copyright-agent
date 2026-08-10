# 本地资产工作台 API v1

本契约由 `DiagramAssetApi` 实现，领域路由与传输框架解耦；正式 sidecar 使用 FastAPI、Pydantic 和 Uvicorn 解析 HTTP、限制请求体、验证 Schema、注入会话令牌并序列化 `ApiResponse`。

## 安全边界

- 只允许 sidecar 监听 `127.0.0.1` 随机端口。
- 每次桌面应用启动生成至少 32 字符的随机会话令牌。
- 所有 `/api/v1` 请求必须携带该令牌；路由分发前使用常量时间比较验证。
- 不启用通配 CORS；只精确放行 `tauri://localhost` 和 Windows 使用的 `http://tauri.localhost`，不接受客户端提供的文件路径。
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
| GET | `/api/v1/tasks/{task_id}/inspection` | 读取任务状态、事实、证据和待确认项 |
| POST | `/api/v1/tasks/{task_id}/confirmations/{field_key}` | 回答待确认项并返回刷新后的任务检查结果 |
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
