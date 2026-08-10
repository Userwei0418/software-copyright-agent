import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from software_copyright_agent.manual_document import ManualDocumentService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.manual_qa import ManualQaService
from software_copyright_agent.storage import Database


class ManualQaServiceTests(unittest.TestCase):
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
                {"type": "paragraph", "text": (
                    "本系统使用结构化证据驱动说明书生成，正文、图表、截图和文档均保存独立版本。"
                    "质量检查会交叉验证 Word 结构与同源 A4 页面，确保中文字体、表格和图片完整。"
                )},
                {"type": "list", "lead": "主要检查包括：",
                 "items": ["DOCX 完整性和嵌入字体", "标题、表格与图片数量", "页面空白和内容密度"]},
                {"type": "table", "title": "检查项目", "headers": ["项目", "结果"],
                 "rows": [["文档结构", "通过"], ["页面渲染", "通过"]]},
            ]
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
            result = ManualQaService(database, root, documents=documents).execute(
                job["id"], assembled["version"]
            )

            self.assertEqual(result["document"]["status"], "qa_passed")
            self.assertTrue(result["qa_run"]["passed"])
            self.assertGreaterEqual(result["qa_run"]["page_count"], 3)
            page = ManualQaService(database, root, documents=documents).read_page(
                job["id"], assembled["version"], 1
            )
            self.assertTrue(page.startswith(b"\x89PNG"))
            pdf = ManualQaService(database, root, documents=documents).read_pdf(
                job["id"], assembled["version"]
            )
            self.assertTrue(pdf.startswith(b"%PDF"))
            pipeline = ManualPipelineService(database).get(job["id"])
            self.assertEqual(pipeline["status"], "completed")
            self.assertEqual(pipeline["progress"]["percent"], 100)
            self.assertEqual(pipeline["steps"][-1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
