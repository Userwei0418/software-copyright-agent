# 本地资产工作台 API v1

本契约由 `DiagramAssetApi` 实现，与 FastAPI 等传输框架解耦。未来 sidecar 只负责解析 HTTP、限制请求体、注入会话令牌并序列化 `ApiResponse`。

## 安全边界

- 只允许 sidecar 监听 `127.0.0.1` 随机端口。
- 每次桌面应用启动生成至少 32 字符的随机会话令牌。
- 所有 `/api/v1` 请求必须携带该令牌；路由分发前使用常量时间比较验证。
- 不启用通配 CORS，不接受客户端提供的文件路径。
- SVG 预览路径来自已持久化 revision，并再次校验必须位于对应任务目录内。
- 单次保存最多 500 个白名单 overlay 操作。

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/tasks/{task_id}/diagram-assets` | 读取资产工作台快照 |
| GET | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions` | 列出版本 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions` | 保存人工或 AI 覆盖 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rebase` | 将最新覆盖重放到最新语义图 |
| POST | `/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rollback` | 将历史操作复制为最新版本 |
| GET | `/api/v1/diagram-revisions/{revision_id}` | 读取 revision 与编辑图数据 |
| POST | `/api/v1/diagram-revisions/{revision_id}/resolve` | 逐项解决 rebase 冲突 |
| GET | `/api/v1/diagram-revisions/{revision_id}/preview.svg` | 获取内置 SVG 预览 |

`diagram_key` 首版只允许 `system_architecture` 和 `core_business_flow`。

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
