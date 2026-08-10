# 核心领域对象与接口草案

## 1. 约定

本文件描述语义契约，不是最终 Python 语法。实现阶段优先使用不可变数据对象、明确枚举和版本化 Schema。

所有 ID 使用 UUIDv7 或等价的可排序随机 ID。所有时间使用 UTC。所有跨边界对象包含 `schema_version`。

## 2. 领域对象

### ProjectSource

表示用户选择的输入，不表示已扫描结果。

```text
ProjectSource
- id
- kind: directory | zip
- original_path
- display_name
- created_at
```

### ProjectSnapshot

表示一次确定的扫描输入：

```text
ProjectSnapshot
- id
- source_id
- root_fingerprint
- scanner_version
- ignore_rules_version
- created_at
- file_count
- total_bytes
- files[]: path, size, mtime_ns, content_hash, category
```

恢复任务时先比较快照指纹。输入变化不覆盖旧快照，而是生成新快照并要求用户决定重新分析还是查看旧结果。

### Evidence

```text
Evidence
- id
- snapshot_id
- kind: source | config | documentation | user_confirmation | derived
- relative_path?
- locator?: line range | JSON path | symbol | section
- excerpt?
- content_hash?
- extractor
- confidence
- sensitivity
- created_at
```

`derived` 证据必须引用一个或多个上游 Evidence ID，不能成为无来源的项目事实。

### Fact

```text
Fact
- id
- key
- value
- status: candidate | confirmed | rejected | superseded
- source: deterministic | model | user
- confidence
- evidence_ids[]
- confirmed_at?
```

软件名称、版本、用途等关键事实只有达到规则阈值或经用户确认后才能进入正式产物。

### ConfirmationRequest

```text
ConfirmationRequest
- id
- task_id
- field_key
- question
- candidates[]
- evidence_ids[]
- required
- status: pending | answered | dismissed
- answer?
```

### Artifact

```text
Artifact
- id
- task_id
- stage_run_id
- kind
- logical_name
- relative_path
- media_type
- byte_size
- sha256
- version
- status: draft | qa_blocked | deliverable | superseded
- parent_artifact_ids[]
- created_at
```

同一逻辑产物重新生成时增加版本，旧版本标记为 `superseded`，不直接覆盖审计记录。

### QAResult

```text
QAResult
- id
- artifact_id?
- task_id
- checker_id
- checker_version
- severity: info | warning | error | blocker
- code
- message
- evidence
- location?
- remediation?
- created_at
```

是否可交付由版本化 `QualityPolicy` 汇总判断，不能由某个模型响应直接赋值。

## 3. 模型抽象

### ProtocolAdapter

负责厂商协议、HTTP 和流式事件转换：

```text
ProtocolAdapter
+ protocol_id
+ validate_config(config)
+ probe(config) -> ProtocolProbe
+ invoke(request, credentials, timeout, cancel_token) -> RawModelResult
+ stream(request, credentials, timeout, cancel_token) -> ModelEvent stream
```

职责包括：

- URL、请求头和请求体映射。
- 认证注入。
- SSE/流式协议解析。
- 供应商错误、限流和用量标准化。
- 不负责软著提示词、事实判断或业务重试策略。

### ModelProvider

表示一份可用的模型服务配置与能力：

```text
ModelProvider
+ profile() -> ModelProfile
+ capabilities() -> ModelCapabilities
+ generate_text(request) -> TextResult
+ generate_structured(request, schema) -> StructuredResult
+ generate_with_images(request) -> VisionResult
+ stream(request) -> ModelEvent stream
```

`ModelCapabilities` 至少包含：

- `context_window`
- `max_output_tokens`
- `structured_output`: native_schema | json_mode | prompt_only | none
- `tool_calling`
- `vision_input`
- `streaming`
- `system_message`
- `usage_reporting`
- 支持的输入图片类型和数量限制

配置中允许用户覆盖未知或探测不准确的能力，但覆盖值必须留痕。

### 结构化生成策略

1. 优先使用原生 JSON Schema。
2. 其次使用 JSON mode。
3. 再次使用受约束提示和本地解析。
4. 使用同一 Schema 做本地验证。
5. 失败时将精简校验错误发送给模型修复。
6. 达到次数上限后返回可诊断错误，不构造默认业务值。

### 标准错误

```text
ModelError
- category: auth | permission | rate_limit | timeout | context_limit |
            invalid_request | invalid_response | unavailable | canceled | unknown
- provider_code?
- retryable
- retry_after?
- safe_message
- diagnostic_ref
```

认证错误和无效请求不自动重试；限流、短暂不可用和网络超时按幂等策略退避重试。

## 4. Tool 接口

Tool 是应用明确注册的受控本地能力，不是任意 Shell：

```text
Tool
+ descriptor() -> ToolDescriptor
+ validate(input, context) -> ValidationResult
+ execute(input, context, cancel_token) -> ToolResult
```

`ToolDescriptor` 包含：

- 名称、版本和用途。
- 输入/输出 Schema。
- 允许访问的路径范围。
- 是否产生副作用。
- 是否幂等、是否可重试。
- 默认超时和资源上限。
- 是否需要用户确认。

`ToolContext` 只暴露任务根目录、已批准项目根目录、临时目录和必要服务端口，不暴露完整操作系统环境。

MVP 工具类型：

- 项目扫描与文件读取。
- 秘密与敏感文件检测。
- 代码分类、换行和分页。
- DOCX 生成与检查。
- Draw.io XML 生成、检查和渲染。
- PDF 转换和页面渲染。
- 产物哈希与注册。

## 5. 应用服务接口

```text
TaskService
+ create_task(source, model_config_id) -> Task
+ start_task(task_id, expected_version)
+ cancel_task(task_id, expected_version)
+ retry_stage(task_id, stage_id, expected_version)
+ answer_confirmation(request_id, answer, expected_version)
+ recover_tasks() -> RecoveryReport
```

所有写命令带 `expected_version`，避免 UI 重复点击或旧页面覆盖新状态。

```text
ArtifactService
+ list_artifacts(task_id)
+ get_artifact(artifact_id)
+ export_artifact(artifact_id, destination)
+ run_quality_gate(task_id) -> QualityGateResult
```

```text
ModelConfigService
+ save_config(metadata, secret) -> ModelConfig
+ test_config(config_id) -> ModelTestResult
+ delete_config(config_id)
+ list_configs()
```

删除配置只删除凭据和配置记录，不删除历史任务中的非秘密调用摘要。

## 6. 存储端口

```text
UnitOfWork
+ tasks
+ stages
+ events
+ facts
+ evidence
+ artifacts
+ qa_results
+ commit()
+ rollback()
```

一次状态转换、阶段记录、checkpoint 和对应事件必须在同一 SQLite 事务中提交。文件产物采用“两阶段注册”：先原子写入文件，再在事务中登记哈希和元数据；孤立临时文件由恢复审计清理。

## 7. 质量策略接口

```text
QualityChecker
+ checker_id
+ applies_to(artifact_kind)
+ check(artifact, context) -> list[QAResult]
```

```text
QualityPolicy
+ policy_id
+ policy_version
+ evaluate(results) -> QualityGateResult
```

`QualityGateResult` 包含阻断项、警告、豁免及最终状态。豁免只能由明确的用户动作产生，并记录原因；“源码不足”可以形成真实披露，但不能豁免敏感信息或伪造内容。

## 8. 取消与进度

所有长操作接收协作式 `CancellationToken`，在文件批次、模型流事件、渲染子进程和阶段边界检查取消。不可安全中断的短事务完成后再进入取消状态。

进度使用结构化单位，不承诺伪精确百分比：

```text
Progress
- phase
- completed_units
- total_units?
- unit_name
- message
```

UI 可在已知总量时显示百分比，未知总量时显示阶段活动状态。

## 9. 版本兼容

- SQLite 使用单调 migration 版本。
- 领域 JSON 使用独立 `schema_version`。
- Tool、checker、规则和提示模板均记录版本。
- checkpoint 保存执行所需的版本集合。
- 新版本无法安全读取旧 checkpoint 时，将阶段标记为需要重新执行，而不是猜测恢复。
