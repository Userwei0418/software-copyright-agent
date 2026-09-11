# 软件说明书首阶段排版回归 2026-09-11

本报告仅记录首阶段确定性回归；之后的真实模型任务、人工修订及当前 v15/v18 标准见 `../real-model-e2e/` 和仓库总审计。

本阶段修复说明书产物的内容检查、版本标识、目录、PDF 预览和终稿门禁，并使用隔离 SQLite 数据和真实 LibreOffice 运行验证。未读取或修改正式应用数据库，未调用付费或本地模型。

## 已复现并修复

| 问题 | 修复与证据 |
| --- | --- |
| 回归样本的 companion 渲染调用缺少参数，运行即 TypeError | 修复调用；新增 `--render-dir` 使用产品真实 LibreOffice 渲染器；合成图片使用随产品分发的中文字体，并显式标记为合成排版样本 |
| 非连续章节编号让目录无法回写，例如章节 1、2、3、7 被查成 1、2、3、4 | 按 `ordinal` 找真实渲染页；新增 `structure.toc_page_numbers` 比较缓存目录与最终 PDF；真实样本回写为 3、3、4、6 |
| Word QA 没有读取表格和嵌套表格正文 | 占位词、推断、无证据验收/上线结论和字体覆盖检查现在包含单元格；回归包括表格中的 TODO、已通过验收和嵌套表格的推断 |
| 截图只有解读版本 ID 也能通过完整性检查，缺失敏感审查默认为安全 | 增加六项操作说明完整性检查；敏感审查必须明确 `confirmed_safe`。既有 legacy 兼容数据仍由旧迁移路径提供其状态，本轮没有重新认证历史截图 |
| 截图用途/流程被套话重新拼接并重复，短说明溢到孤立尾页 | 保留已审核用途原句，流程只出现一次；图片与短说明尽量作为一个单元分页，超长说明仍允许换页 |
| PDF 实际名称为 DOCX 文件名，但数据库总登记 preview.pdf | 保存渲染器实际返回的 PDF 路径；真实产品 QA 后读回 PDF 254723 字节，头部和 SHA 检查有效 |
| 旧 DOCX 可由当前最新章节/图片元数据重新执行 QA；修改项目版本也会给旧文件显示新版本名 | QA 拒绝过期文档，要求重装配；新产物固化软件名与版本，项目事实改变后保留历史文件名并标记过期 |
| QA failed 可直接生成人工终稿；终稿自身 QA failed 仍可正式下载 | 终稿生成要求有效 QA passed；正式下载和导出凭据要求当前终稿自身 QA passed/当前版本标准，审阅下载保留；既有白名单 defer_check 继续按原规则计算有效通过 |
| 重跑同一版本 QA 会反复累加同一个警告 | 新 QA 替换旧 QA 警告，仅与装配时的原始 warnings 合计 |

生成器版本：`formal-manual-docx-v13`。QA 标准：`manual-docx-qa-v17`。旧标准产物应重新装配及检查。

## 验证

- 说明书、QA、导出、工作流、截图工作流与 Sidecar 路由的 36 项回归通过；测试使用仓库 `.venv`。
- `runtime-check.json`：真实 `ManualDocumentService.assemble → ManualQaService.execute → read_pdf`。样本没有真实证据且包含待确认内容，被占位/证据覆盖/章节深度三个 blocker 拒绝终稿；目录、图号、DOCX 哈希和 PDF 读回通过。
- `runtime-final-lifecycle.json`：仅为测试添加明确的 fixture 引用并移除占位词，人工调用既有章节深度白名单豁免，在隔离数据库验证正反生命周期。终稿自身检查未通过时正式下载返回 409、审阅下载返回 200；其自身有效 QA 通过后正式下载返回 200，并校验 SHA 和记录真实导出凭据。该测试中的豁免和合成引用不能解释为真实项目事实通过验收。
- 真实产品渲染为 6 页；再使用 Documents 技能 `render_docx.py` 独立渲染已回写目录的 DOCX，逐页检查全部 6 页。中文、表格边界、图注、目录、截图和页脚可见，无空正文页、遮挡或裁切。第 5 页是前章的短表格尾页，产品 QA 如实报告页面密度 warning；未将该排版样本声明为可提交材料。
- 实际 Word/Office Windows 客户端、真实模型生成的章节内容、真实用户截图审核和正式应用数据库仍不在本轮隔离验证范围内。自动 QA 对事实仅提供引用/规则检查，不能替代人工核对源码是否支持每项叙述。

## 重现入口

在项目根目录执行：

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.test_manual_document tests.test_manual_qa tests.test_manual_exports tests.test_manual_workflow tests.test_manual_ui_workflow tests.test_sidecar
PYTHONPATH=src .venv/bin/python scripts/build_manual_qa_fixture.py /tmp/manual-layout/layout.docx --companion-dir /tmp/manual-layout/companion --render-dir /tmp/manual-layout/word
```

`fixture-inputs.json` 保存合成输入，`render-report.json` 保存产品渲染结果。`layout-fixture.docx`、`runtime-checked.docx`、渲染 PNG/PDF 和 companion 图仅保留本地，不提交大型二进制。目录中的 JSON/本报告可作为小型审计证据提交。
