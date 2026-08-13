import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from docx import Document
from PIL import Image, ImageDraw

from software_copyright_agent.manual_document import ManualDocumentService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.manual_qa import (
    CompanionRenderResult, ManualCompanionRenderer, ManualDocxInspector,
    ManualQaError, ManualQaService,
)
from software_copyright_agent.storage import Database


class ManualQaServiceTests(unittest.TestCase):
    def test_header_footer_only_body_page_is_a_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document_path = root / "manual.docx"
            document = Document()
            document.sections[0].page_width = 7560310
            document.sections[0].page_height = 10692130
            document.add_paragraph("quality fixture")
            document.save(document_path)
            page_paths = []
            for index in range(1, 4):
                path = root / f"page-{index}.png"
                image = Image.new("RGB", (1191, 1684), "white")
                # Simulate the header/footer that made the real empty body page
                # evade a whole-image whiteness check.
                draw = ImageDraw.Draw(image)
                draw.text((850, 70), "header", fill="black")
                draw.text((580, 1600), str(index), fill="black")
                image.save(path)
                page_paths.append(path)
            render = CompanionRenderResult(
                root / "preview.pdf", tuple(page_paths),
                ("cover", "toc", "content"), (0.4, 0.4, 0.0), (3,),
            )
            result = ManualDocxInspector().inspect(
                document_path, hashlib.sha256(document_path.read_bytes()).hexdigest(),
                [], [], [], render, {},
            )
            checks = {item.key: item for item in result.checks}
            self.assertFalse(checks["render.body_blank_pages"].passed)
            self.assertEqual(checks["render.body_blank_pages"].actual, [3])
            self.assertFalse(result.passed)

    def test_companion_render_cross_checks_docx_and_completes_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = Database(root / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id,kind,original_path,display_name,
                    created_at,last_opened_at) VALUES
                    ('source','directory','/tmp/project','质量检查系统',?,?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id,source_id,root_fingerprint,
                    scanner_version,rules_version,summary_json,manifest_relative_path,created_at)
                    VALUES ('snapshot','source','hash','v1','v1','{}','manifest.jsonl',?)""",
                    (now,),
                )
                connection.execute(
                    """INSERT INTO tasks(id,source_id,snapshot_id,status,workflow_version,
                    quality_policy_version,created_at,updated_at) VALUES
                    ('task','source','snapshot','completed','v1','v1',?,?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                    settings_json,enabled,created_at,updated_at) VALUES
                    ('model','Local','ollama','http://127.0.0.1:11434','model','{}',1,?,?)""",
                    (now, now),
                )
            job = ManualPipelineService(database).create("task", "model")
            blocks = [
                {"type": "subheading", "title": "结构化生成与质量验证"},
                {"type": "paragraph", "text": (
                    "本系统使用结构化证据驱动说明书生成，正文、图表、截图和文档均保存独立版本。"
                    "质量检查会交叉验证 Word 结构与同源 A4 页面，确保中文字体、表格和图片完整。"
                    "生成流程先汇总项目事实与源码证据，再按章节组织职责、输入、处理、输出和异常恢复说明。"
                    "前端使用 Vue.js 组织页面与交互，技术名称不应被误判为内部源文件。"
                    "每个阶段的状态与产物均写入本地数据库，页面切换或应用重启后仍可继续检查已有结果。"
                ) * 3},
                {"type": "paragraph", "text": (
                    "文档装配阶段根据章节语义插入对应图表和界面截图，保留可编辑源文件与图片预览。"
                    "导出前会检查章节数量、图片关系、页面尺寸和字体覆盖，发现缺失时阻止将低质量版本标记为通过。"
                ) * 3},
                {"type": "paragraph", "text": (
                    "用户可在章节编辑器中修订正文，也可在图表工作台移动节点、调整标签并显式保存新版本。"
                    "界面截图通过独立资产页导入，填写页面用途、进入条件、可见区域、典型流程、后台交互和异常恢复后，"
                    "再与最新正文和图表共同装配，从而保证最终文档中的文字、图示与实际软件行为保持一致。"
                ) * 3},
                {"type": "list", "lead": "主要检查包括：",
                 "items": ["DOCX 完整性和嵌入字体", "标题、表格与图片数量", "页面空白和内容密度"]},
                {"type": "table", "title": "检查项目", "headers": ["项目", "结果"],
                 "rows": [["文档结构", "验证标题、表格、图片和字体关系"],
                          ["页面渲染", "验证 A4 尺寸、空白页和内容密度"],
                          ["版本追踪", "保留正文、图表、截图和导出文档版本"]]},
            ]
            for block in blocks:
                block["evidence_refs"] = ["source:src/manual.py:L1-L80"]
                block["inference"] = False
            figure_blocks = blocks + [{"type": "figure_request", "figure_key": "architecture",
                                       "title": "质量检查架构图"}]
            with database.connect() as connection:
                for ordinal, (key, title, content) in enumerate((
                    ("introduction", "引言", blocks),
                    ("architecture", "总体设计", figure_blocks),
                    ("ui_operations", "用户界面与操作说明", blocks),
                ), 1):
                    connection.execute(
                        """INSERT INTO manual_section_artifacts(id,job_id,section_key,title,
                        ordinal,status,content_json,evidence_refs_json,inference_notes_json,
                        figure_requests_json,updated_at) VALUES
                        (?,?,?,?,?,'generated',?,'[]','[]','[]',?)""",
                        (str(uuid4()), job["id"], key, title, ordinal,
                         json.dumps(content, ensure_ascii=False), now),
                    )
            task_root = root / "tasks" / "task"
            figure_relative = "artifacts/manual-figures/architecture.v1.png"
            screenshot_relative = "artifacts/manual-screenshots/workspace.v1.png"
            for relative, color in ((figure_relative, "#e8eef5"),
                                    (screenshot_relative, "#f2f5f7")):
                path = task_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGB", (1400, 800), color)
                ImageDraw.Draw(image).rectangle((120, 120, 1280, 680), outline="#2e74b5", width=8)
                image.save(path, "PNG")
            description = {
                "page_purpose": "查看正式说明书质量状态。",
                "entry_conditions": "项目已生成正式 Word 文档。",
                "visible_regions": "包含页面预览、页码导航、检查摘要和导出操作。",
                "typical_workflow": "用户查看页面并在通过后导出。",
                "backend_interactions": "页面读取本地 QA 记录和 PNG 预览。",
                "result_validation_recovery": "失败版本保留，可修正后重新检查。",
            }
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO manual_figure_artifacts(id,job_id,figure_key,section_key,
                    figure_type,title,status,semantic_json,drawio_relative_path,svg_relative_path,
                    png_relative_path,qa_json,updated_at) VALUES
                    (?,?,'architecture','architecture','architecture','质量检查架构图','verified',
                    '{}','a.drawio','a.svg',?,'{}',?)""",
                    (str(uuid4()), job["id"], figure_relative, now),
                )
                connection.execute(
                    """INSERT INTO manual_screenshot_artifacts(id,job_id,screenshot_key,
                    section_key,title,source,image_relative_path,description_json,created_at)
                    VALUES (?,?,'workspace','ui_operations','质量检查页面','user',?,?,?)""",
                    (str(uuid4()), job["id"], screenshot_relative,
                     json.dumps(description, ensure_ascii=False), now),
                )
            documents = ManualDocumentService(database, root)
            assembled = documents.assemble(job["id"])
            result = ManualQaService(database, root, documents=documents,
                                     renderer=ManualCompanionRenderer()).execute(
                job["id"], assembled["version"]
            )

            self.assertEqual(result["document"]["status"], "qa_passed")
            self.assertTrue(result["qa_run"]["passed"])
            checks = {item["key"]: item for item in result["qa_run"]["checks"]}
            self.assertTrue(checks["content.evidence_coverage"]["passed"])
            self.assertTrue(checks["content.epistemic_caveats"]["passed"])
            self.assertGreaterEqual(result["qa_run"]["page_count"], 3)
            page = ManualQaService(database, root, documents=documents,
                                   renderer=ManualCompanionRenderer()).read_page(
                job["id"], assembled["version"], 1
            )
            self.assertTrue(page.startswith(b"\x89PNG"))
            pdf = ManualQaService(database, root, documents=documents,
                                  renderer=ManualCompanionRenderer()).read_pdf(
                job["id"], assembled["version"]
            )
            self.assertTrue(pdf.startswith(b"%PDF"))
            pipeline = ManualPipelineService(database).get(job["id"])
            self.assertEqual(pipeline["status"], "completed")
            self.assertEqual(pipeline["progress"]["percent"], 100)
            self.assertEqual(pipeline["steps"][-1]["status"], "completed")

            with database.connect() as connection:
                row = connection.execute(
                    """SELECT content_json FROM manual_section_artifacts
                    WHERE job_id=? AND section_key='introduction'""", (job["id"],),
                ).fetchone()
                revised = json.loads(row["content_json"])
                paragraph = next(item for item in revised if item["type"] == "paragraph")
                paragraph["text"] += "人工修订后会装配为新版本，原始交付件继续保留。"
                connection.execute(
                    """UPDATE manual_section_artifacts SET content_json=?
                    WHERE job_id=? AND section_key='introduction'""",
                    (json.dumps(revised, ensure_ascii=False), job["id"]),
                )
            assembled_v2 = documents.assemble(job["id"])
            checked_v2 = ManualQaService(database, root, documents=documents,
                                         renderer=ManualCompanionRenderer()).execute(
                job["id"], assembled_v2["version"]
            )
            self.assertEqual(checked_v2["document"]["version"], 2)
            self.assertEqual(checked_v2["document"]["status"], "qa_passed")
            versions = documents.list(job["id"])
            self.assertEqual([item["version"] for item in versions], [2, 1])
            self.assertTrue(all(item["status"] == "qa_passed" for item in versions))
            self.assertNotEqual(versions[0]["sha256"], versions[1]["sha256"])

            with database.connect() as connection:
                row = connection.execute(
                    """SELECT content_json FROM manual_section_artifacts
                    WHERE job_id=? AND section_key='introduction'""", (job["id"],),
                ).fetchone()
                caveated = json.loads(row["content_json"])
                paragraph = next(item for item in caveated if item["type"] == "paragraph")
                paragraph["text"] = "根据项目证据推断，" + paragraph["text"]
                connection.execute(
                    """UPDATE manual_section_artifacts SET content_json=?
                    WHERE job_id=? AND section_key='introduction'""",
                    (json.dumps(caveated, ensure_ascii=False), job["id"]),
                )
            assembled_v3 = documents.assemble(job["id"])
            checked_v3 = ManualQaService(database, root, documents=documents,
                                         renderer=ManualCompanionRenderer()).execute(
                job["id"], assembled_v3["version"]
            )
            caveat_checks = {item["key"]: item for item in checked_v3["qa_run"]["checks"]}
            self.assertFalse(caveat_checks["content.epistemic_caveats"]["passed"])
            self.assertEqual(checked_v3["document"]["status"], "qa_failed")

            paragraph["text"] = paragraph["text"].replace("根据项目证据推断，", "") + (
                "各项技术指标符合设计预期，系统具备上线运行条件。"
            )
            with database.connect() as connection:
                connection.execute(
                    """UPDATE manual_section_artifacts SET content_json=?
                    WHERE job_id=? AND section_key='introduction'""",
                    (json.dumps(caveated, ensure_ascii=False), job["id"]),
                )
            assembled_v4 = documents.assemble(job["id"])
            checked_v4 = ManualQaService(database, root, documents=documents,
                                         renderer=ManualCompanionRenderer()).execute(
                job["id"], assembled_v4["version"]
            )
            outcome_checks = {item["key"]: item for item in checked_v4["qa_run"]["checks"]}
            self.assertFalse(outcome_checks["content.unverified_outcomes"]["passed"])
            self.assertEqual(checked_v4["document"]["status"], "qa_failed")
            qa_service = ManualQaService(
                database, root, documents=documents, renderer=ManualCompanionRenderer()
            )
            with self.assertRaisesRegex(ManualQaError, "不能通过忽略绕过"):
                qa_service.defer_check(
                    job["id"], assembled_v4["version"],
                    "content.unverified_outcomes", "等待补充独立测试报告后处理",
                )

            # A human may waive a layout-only blocker. Keep the original failed
            # fact, but recompute the effective delivery gate from unwaived keys.
            with database.connect() as connection:
                qa_row = connection.execute(
                    """SELECT id,checks_json,summary_json FROM manual_document_qa_runs
                    WHERE document_artifact_id=? ORDER BY qa_version DESC LIMIT 1""",
                    (checked_v4["document"]["id"],),
                ).fetchone()
                checks_payload = json.loads(qa_row["checks_json"])
                for item in checks_payload:
                    if item["key"] == "render.page_density":
                        item.update({"passed": False, "severity": "blocker", "actual": [2],
                                     "message": "存在大面积空白页"})
                    elif item["severity"] == "blocker":
                        item["passed"] = True
                summary = json.loads(qa_row["summary_json"])
                summary.update({"passed": False, "failed_check_count": 1,
                                "failed_checks": ["render.page_density"]})
                connection.execute(
                    """UPDATE manual_document_qa_runs SET passed=0,checks_json=?,summary_json=?
                    WHERE id=?""",
                    (json.dumps(checks_payload, ensure_ascii=False),
                     json.dumps(summary, ensure_ascii=False), qa_row["id"]),
                )
            waived = qa_service.defer_check(
                job["id"], assembled_v4["version"],
                "render.page_density", "人工检查页面可读性后接受当前分页",
            )
            self.assertTrue(waived["passed"])
            self.assertEqual(waived["decisions"][0]["action"], "deferred")
            self.assertEqual(waived["summary"]["failed_checks"], [])
            self.assertEqual(waived["summary"]["raw_failed_checks"], ["render.page_density"])


if __name__ == "__main__":
    unittest.main()
