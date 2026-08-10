# 数据模型、任务状态机与产物协议

## 1. SQLite 原则

- 开启 foreign keys。
- 使用 WAL 模式，限制为单个写入协调器。
- migration 只向前执行，每次升级前备份数据库元数据。
- 业务删除默认采用可审计删除；清理任务文件需单独确认。
- 数据库不保存明文 API Key、大段源码、DOCX、图片或模型二进制输入。

## 2. 核心表

### 应用与模型配置

`schema_migrations`

- `version` PK
- `applied_at`
- `checksum`

`model_configs`

- `id` PK
- `name`
- `protocol_id`
- `base_url`
- `model_name`
- `credential_ref`
- `settings_json`
- `capabilities_json`
- `capabilities_source`: probed | user_override | builtin
- `created_at`、`updated_at`

自定义请求头中的秘密值也必须进入安全存储，`settings_json` 只保存引用。

### 项目与快照

`project_sources`

- `id` PK
- `kind`
- `original_path`
- `display_name`
- `created_at`、`last_opened_at`

`project_snapshots`

- `id` PK
- `source_id` FK
- `root_fingerprint`
- `scanner_version`
- `rules_version`
- `summary_json`
- `manifest_relative_path`
- `created_at`

`evidence`

- `id` PK
- `snapshot_id` FK
- `kind`
- `relative_path`
- `locator_json`
- `excerpt_relative_path`
- `content_hash`
- `extractor`
- `confidence`
- `sensitivity`
- `created_at`

### 任务与阶段

`tasks`

- `id` PK
- `source_id` FK
- `snapshot_id` FK nullable
- `model_config_id` FK
- `status`
- `current_stage_key`
- `workflow_version`
- `quality_policy_version`
- `row_version`
- `created_at`、`started_at`、`finished_at`、`updated_at`
- `failure_category`、`safe_error_message`

`task_stages`

- `id` PK
- `task_id` FK
- `stage_key`
- `sequence`
- `status`
- `attempt`
- `input_fingerprint`
- `checkpoint_json`
- `started_at`、`finished_at`
- `failure_category`、`safe_error_message`
- unique (`task_id`, `stage_key`, `attempt`)

`task_events`

- `id` PK, monotonic
- `task_id` FK
- `stage_run_id` FK nullable
- `event_type`
- `level`
- `message`
- `payload_json`
- `created_at`

SSE 使用 event ID 断点续传。客户端重连时从最后确认的 `task_events.id` 继续读取。

`confirmation_requests`

- `id` PK
- `task_id` FK
- `field_key`
- `question`
- `candidates_json`
- `evidence_ids_json`
- `required`
- `status`
- `answer_json`
- `created_at`、`answered_at`

### 事实、调用、产物和 QA

`facts`

- `id` PK
- `task_id` FK
- `key`
- `value_json`
- `status`
- `source`
- `confidence`
- `evidence_ids_json`
- `created_at`、`confirmed_at`

`model_calls`

- `id` PK
- `task_id` FK
- `stage_run_id` FK
- `config_id` FK
- `purpose`
- `request_fingerprint`
- `response_fingerprint`
- `status`
- `input_tokens`、`output_tokens`
- `cost_json`
- `started_at`、`finished_at`
- `error_category`

`tool_calls`

- `id` PK
- `task_id` FK
- `stage_run_id` FK
- `tool_id`、`tool_version`
- `input_fingerprint`
- `status`
- `started_at`、`finished_at`
- `error_category`

`artifacts`

- `id` PK
- `task_id` FK
- `stage_run_id` FK
- `kind`
- `logical_name`
- `relative_path`
- `media_type`
- `byte_size`
- `sha256`
- `version`
- `status`
- `created_at`
- unique (`task_id`, `logical_name`, `version`)

`artifact_relations`

- `parent_artifact_id` FK
- `child_artifact_id` FK
- `relation_type`

`qa_results`

- `id` PK
- `task_id` FK
- `artifact_id` FK nullable
- `checker_id`、`checker_version`
- `severity`
- `code`
- `message`
- `evidence_json`
- `location_json`
- `remediation`
- `created_at`

## 3. 任务状态

任务状态：

- `created`：已建立记录，尚未开始。
- `running`：当前有一个阶段运行。
- `waiting_for_user`：存在阻断性的确认请求。
- `cancel_requested`：已请求取消，等待安全点。
- `canceled`：已安全停止。
- `failed`：阶段失败且未自动恢复。
- `completed_with_warnings`：产物可交付，但存在非阻断警告。
- `completed`：质量门禁通过。

允许的主要转换：

```text
created -> running
running -> waiting_for_user -> running
running -> cancel_requested -> canceled
running -> failed -> running
running -> completed_with_warnings
running -> completed
canceled -> running       仅从可恢复阶段重启
```

`completed` 或 `completed_with_warnings` 后重新生成，不回退原任务状态；创建新的阶段 attempt 和产物版本，任务重新进入 `running`，历史产物保留。

## 4. 阶段状态

- `pending`
- `running`
- `waiting_for_user`
- `succeeded`
- `failed`
- `cancel_requested`
- `canceled`
- `skipped`
- `stale`

阶段状态必须通过单一状态转换服务修改。数据库约束和应用校验共同阻止非法跳转。

## 5. MVP 工作流

```text
01_ingest
02_scan
03_extract_facts
04_confirm_metadata
05_select_source
06_generate_source_doc
07_plan_manual
08_generate_diagrams
09_generate_manual_doc
10_render
11_quality_gate
12_finalize
```

阶段说明：

- `ingest`：校验目录或安全解压 ZIP，建立输入指纹。
- `scan`：生成清单、技术栈和候选文件分类。
- `extract_facts`：确定性提取优先，模型补充语义候选。
- `confirm_metadata`：缺少关键事实时等待用户确认。
- `select_source`：规则过滤、去重、语义排序和页数可行性分析。
- `generate_source_doc`：确定性换行、分页和 Word 出件。
- `plan_manual`：依据事实和证据生成说明书结构与内容计划。
- `generate_diagrams`：生成 Draw.io XML、SVG 和 PNG。
- `generate_manual_doc`：生成说明书 Word，允许使用手动导入截图。
- `render`：DOCX 转 PDF 并渲染页面。
- `quality_gate`：汇总确定性检查，必要时回到具体生成阶段。
- `finalize`：登记最终产物、报告和导出清单。

## 6. 输入指纹与缓存

每个阶段的 `input_fingerprint` 由以下内容的规范化哈希组成：

- 上游 artifact 哈希。
- 所用事实和用户确认值。
- 规则、提示模板、Tool 和 checker 版本。
- 模型配置的非秘密标识及模型名。
- 与结果相关的参数。

只有阶段状态为 `succeeded`、产物仍存在且哈希匹配、当前输入指纹相同，才允许复用。模型输出默认可复用已保存结果，不在恢复时无条件再次计费调用。

## 7. 重试语义

- 自动重试只处理明确可重试的网络、限流和短暂运行时错误。
- 每次重试产生新的模型或工具调用记录。
- 阶段级手动重试增加 `attempt`，不覆盖旧 attempt。
- 重试某阶段会把依赖其输出的成功阶段标记为 `stale`。
- `stale` 阶段在再次执行前保留旧产物，但旧产物不能作为最终交付版本。
- 认证、Schema 永久不兼容、磁盘空间不足和用户输入缺失不盲目重试。

## 8. 取消语义

1. 写入 `cancel_requested`，追加事件。
2. 触发内存 CancellationToken。
3. 模型流在下一个事件边界关闭连接。
4. 渲染子进程先温和终止，超时后清理进程树。
5. 当前原子数据库事务完成或回滚。
6. 未完成临时文件保留到恢复审计或安全清理。
7. 任务和阶段转为 `canceled`。

取消不是失败，不触发自动重试。

## 9. 启动恢复

应用启动时执行恢复审计：

1. 将遗留 `running` 阶段标记为需要恢复检查，不立即执行。
2. 检查源项目、任务目录、磁盘空间和运行时组件。
3. 校验 checkpoint Schema、输入指纹和已登记 artifact 哈希。
4. 可恢复时从最近安全 checkpoint 继续。
5. 不可安全恢复时将当前 attempt 标记失败，并提供“重新执行该阶段”。
6. 清理未被数据库引用且超过保留期的临时文件。

模型调用如果连接中断且没有完整、已校验的响应，不尝试续写半个响应；重新调用并创建新记录。

## 10. 任务目录协议

```text
tasks/{task_id}/
├── task.json
├── input/
│   ├── source.json
│   ├── manifest.jsonl
│   └── extracted/              # 仅 ZIP 输入
├── evidence/
│   └── {evidence_id}.json
├── intermediate/
│   ├── facts/
│   ├── source-selection/
│   ├── manual-plan/
│   └── model-results/
├── screenshots/
│   └── imported/
├── artifacts/
│   ├── source-code/
│   ├── manual/
│   ├── diagrams/
│   └── reports/
├── renders/
│   ├── source-code/
│   ├── manual/
│   └── diagrams/
├── qa/
└── tmp/
```

内部文件名使用稳定 ASCII：

```text
{logical_name}.v{version}.{extension}
```

例如：

- `source-code.v1.docx`
- `software-manual.v2.docx`
- `system-architecture.v1.drawio`
- `system-architecture.v1.svg`
- `quality-report.v1.json`

用户导出时才生成中文可见文件名，并过滤 Windows 非法字符、保留名和尾部点/空格。

## 11. 产物发布门禁

产物状态变化：

```text
draft -> qa_blocked -> draft       修订后重新检查
draft -> deliverable               质量门禁通过
deliverable -> superseded          新版本成为交付版本
```

以下问题必须阻断交付：

- 检测到秘密、个人敏感数据或路径越界内容。
- 使用测试、mock、依赖、重复或生成代码虚假补页。
- 关键项目事实没有证据或用户确认。
- 源代码页数、封面和分页规则不符合策略且没有真实不足披露。
- DOCX 无法打开或渲染。
- Draw.io XML 无效，或存在阻断级路由问题。
- 登记的文件哈希与磁盘文件不一致。

## 12. 数据保留与删除

- 用户可单独删除任务产物或整个任务。
- 删除前展示将移除的任务目录和数据库记录范围。
- 默认先移动到应用回收区并设置保留期，再永久清理。
- 删除模型配置不级联删除历史任务。
- 删除项目源记录前，必须先处理引用它的任务。
- 数据库备份不包含系统安全存储中的 Key。
