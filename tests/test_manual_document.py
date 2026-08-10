import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from uuid import uuid4

from docx import Document
from PIL import Image, ImageDraw

from software_copyright_agent.manual_document import ManualDocumentService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ManualDocumentServiceTests(unittest.TestCase):
    def test_assembles_native_word_sections_tables_figures_and_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = Database(root / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id,kind,original_path,display_name,
                    created_at,last_opened_at) VALUES
                    ('source','directory','/tmp/project','证据化系统',?,?)""", (now, now),
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
                for key, value in (("project.name", "证据化系统"),
                                   ("project.version", "V2.0")):
                    connection.execute(
                        """INSERT INTO facts(id,task_id,fact_key,value_json,status,source,
                        confidence,evidence_ids_json,created_at,confirmed_at) VALUES
                        (?,?,?,?,'confirmed','user',1,'[]',?,?)""",
                        (str(uuid4()), "task", key, json.dumps(value), now, now),
                    )
                connection.execute(
                    """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                    settings_json,enabled,created_at,updated_at) VALUES
                    ('model-config','Local','ollama','http://127.0.0.1:11434','model',
                    '{}',1,?,?)""", (now, now),
                )
            job = ManualPipelineService(database).create("task", "model-config")
            blocks = [
                {"type": "paragraph", "text": "本系统围绕证据化处理组织业务能力，所有阶段结果均在本地持久化并支持恢复。",
                 "evidence_refs": ["ref"], "inference": False},
                {"type": "list", "lead": "核心职责包括：", "items": ["读取并校验项目材料", "保存可追溯的生成结果"],
                 "evidence_refs": ["ref"], "inference": False},
                {"type": "table", "title": "模块职责", "headers": ["模块", "职责"],
                 "rows": [["扫描模块", "提取项目事实"], ["文档模块", "装配正式说明书"]],
                 "evidence_refs": ["ref"], "inference": False},
            ]
            figure_blocks = blocks + [{
                "type": "figure_request", "figure_key": "architecture",
                "figure_type": "architecture", "title": "系统架构图",
                "purpose": "展示模块协作", "evidence_refs": ["ref"],
            }]
            with database.connect() as connection:
                for ordinal, (key, title, content) in enumerate((
                    ("introduction", "引言", blocks),
                    ("architecture", "总体设计", figure_blocks),
                    ("modules", "功能与模块设计", blocks),
                ), 1):
                    connection.execute(
                        """INSERT INTO manual_section_artifacts(id,job_id,section_key,title,
                        ordinal,status,content_json,evidence_refs_json,inference_notes_json,
                        figure_requests_json,updated_at) VALUES
                        (?,?,?,?,?,'generated',?,'[\"ref\"]','[]','[]',?)""",
                        (str(uuid4()), job["id"], key, title, ordinal,
                         json.dumps(content, ensure_ascii=False), now),
                    )
            task_root = root / "tasks" / "task"
            figure_relative = "artifacts/manual-figures/architecture.v1.png"
            screenshot_relative = "artifacts/manual-screenshots/main.v1.png"
            for relative, label in ((figure_relative, "ARCH"), (screenshot_relative, "UI")):
                path = task_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGB", (1200, 700), "white")
                ImageDraw.Draw(image).text((80, 80), label, fill="black")
                image.save(path, "PNG")
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO manual_figure_artifacts(id,job_id,figure_key,section_key,
                    figure_type,title,status,semantic_json,drawio_relative_path,svg_relative_path,
                    png_relative_path,qa_json,updated_at) VALUES
                    (?,?, 'architecture','architecture','architecture','系统架构图','verified',
                    '{}','a.drawio','a.svg',?,'{}',?)""",
                    (str(uuid4()), job["id"], figure_relative, now),
                )
                description = {
                    "page_purpose": "用于查看当前项目的整体处理状态和主要材料入口。",
                    "entry_conditions": "用户完成项目扫描并从项目列表进入当前任务。",
                    "visible_regions": "页面包含导航区、状态区、操作区和结果预览区。",
                    "typical_workflow": "用户确认项目后启动生成，并在完成后查看文档结果。",
                    "backend_interactions": "页面通过本地接口读取 SQLite 中的任务与产物状态。",
                    "result_validation_recovery": "成功后展示结果；失败时保留阶段记录并允许重试。",
                }
                connection.execute(
                    """INSERT INTO manual_screenshot_artifacts(id,job_id,screenshot_key,
                    section_key,title,source,image_relative_path,description_json,created_at)
                    VALUES (?,?,'main','modules','项目工作台','user',?,?,?)""",
                    (str(uuid4()), job["id"], screenshot_relative,
                     json.dumps(description, ensure_ascii=False), now),
                )

            service = ManualDocumentService(database, root)
            result = service.assemble(job["id"])
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["filename"], "证据化系统-V2.0-软件说明书.docx")
            self.assertEqual(result["integrity"]["status"], "verified")
            self.assertEqual(result["qa"]["figure_count"], 1)
            self.assertEqual(result["qa"]["screenshot_count"], 1)
            artifact = task_root / result["docx_relative_path"]
            doc = Document(artifact)
            self.assertGreaterEqual(len(doc.inline_shapes), 2)
            self.assertEqual(len(doc.tables), 3)
            headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
            self.assertIn("1  引言", headings)
            self.assertIn("3  功能与模块设计", headings)
            with zipfile.ZipFile(artifact) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
                numbering_xml = archive.read("word/numbering.xml").decode("utf-8")
                settings_xml = archive.read("word/settings.xml").decode("utf-8")
                font_table_xml = archive.read("word/fontTable.xml").decode("utf-8")
                font_relationships_xml = archive.read(
                    "word/_rels/fontTable.xml.rels"
                ).decode("utf-8")
                embedded_font = archive.read("word/fonts/NotoSansCJKsc-Regular.odttf")
            self.assertIn("章节目录", document_xml)
            self.assertIn("01    引言", document_xml)
            self.assertIn('w:numFmt w:val="bullet"', numbering_xml)
            self.assertIn('w:updateFields w:val="true"', settings_xml)
            self.assertIn('w:name="Noto Sans CJK SC"', font_table_xml)
            self.assertIn("w:embedRegular", font_table_xml)
            self.assertIn("relationships/font", font_relationships_xml)
            self.assertGreater(len(embedded_font), 1_000_000)
            self.assertEqual(service.read(job["id"], 1), artifact.read_bytes())
            self.assertEqual(result["freshness"]["status"], "current")
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_figure_artifacts SET updated_at='2099-01-01T00:00:00Z' WHERE job_id=?",
                    (job["id"],),
                )
            self.assertEqual(service.list(job["id"])[0]["freshness"]["status"], "outdated")
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_figure_artifacts SET updated_at=? WHERE job_id=?",
                    (now, job["id"]),
                )
                connection.execute(
                    "UPDATE manual_screenshot_artifacts SET updated_at='2099-01-02T00:00:00Z' WHERE job_id=?",
                    (job["id"],),
                )
            screenshot_freshness = service.list(job["id"])[0]["freshness"]
            self.assertEqual(screenshot_freshness["status"], "outdated")
            pipeline = ManualPipelineService(database).get(job["id"])
            self.assertEqual(pipeline["current_step"], "render_qa")
            self.assertEqual(pipeline["progress"]["completed"], 5)


if __name__ == "__main__":
    unittest.main()
