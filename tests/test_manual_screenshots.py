import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.manual_screenshots import ManualScreenshotService
from software_copyright_agent.storage import Database


class ManualScreenshotServiceTests(unittest.TestCase):
    def _fixture(self, root: Path, with_ui: bool = True):
        data_root = root / "data"
        task_root = data_root / "tasks" / "task"
        manifest = task_root / "input" / "manifest.jsonl"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"path": "index.html"}) + "\n", encoding="utf-8")
        database = Database(data_root / "app.db")
        database.initialize()
        now = "2026-08-10T00:00:00Z"
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO project_sources(id, kind, original_path, display_name,
                created_at, last_opened_at) VALUES
                ('source', 'directory', '/tmp/example', '截图系统', ?, ?)""", (now, now),
            )
            connection.execute(
                """INSERT INTO project_snapshots(id, source_id, root_fingerprint,
                scanner_version, rules_version, summary_json, manifest_relative_path,
                scan_root_mode, scan_root_path, created_at) VALUES
                ('snapshot', 'source', 'hash', 'v1', 'v1', '{}', 'input/manifest.jsonl',
                'external', '/tmp/example', ?)""", (now,),
            )
            connection.execute(
                """INSERT INTO tasks(id, source_id, snapshot_id, status, workflow_version,
                quality_policy_version, created_at, updated_at) VALUES
                ('task', 'source', 'snapshot', 'completed', 'v1', 'v1', ?, ?)""", (now, now),
            )
            connection.execute(
                """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                settings_json, enabled, created_at, updated_at) VALUES
                ('model-config', 'Local', 'ollama', 'http://127.0.0.1:11434',
                'model', '{}', 1, ?, ?)""", (now, now),
            )
        job = ManualPipelineService(database).create("task", "model-config")
        if with_ui:
            with database.connect() as connection:
                for item_id, key, title, ordinal in (
                    ("ui", "ui_operations", "用户界面与操作说明", 7),
                    ("intro", "introduction", "引言", 1),
                ):
                    connection.execute(
                        """INSERT INTO manual_section_artifacts(id, job_id, section_key, title,
                    ordinal, status, content_json, evidence_refs_json, inference_notes_json,
                    figure_requests_json, updated_at) VALUES
                    (?, ?, ?, ?, ?, 'generated', '[]', '[]', '[]', '[]', ?)""",
                        (item_id, job["id"], key, title, ordinal, now),
                    )
        return data_root, database, job

    def test_assessment_import_sanitizes_and_finalizes_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database, job = self._fixture(root)
            source = root / "overview.jpg"
            Image.new("RGB", (1280, 720), "#e2e8f0").save(source, format="JPEG")
            service = ManualScreenshotService(database, data_root)
            assessment = service.assess(job["id"])
            self.assertEqual(assessment["status"], "manual_import")
            description = {
                "page_purpose": "该页面用于查看项目状态并进入后续材料生成流程。",
                "entry_conditions": "用户完成本地项目扫描并在项目列表中选择目标项目后进入。",
                "visible_regions": "页面包含项目摘要、阶段进度、主要操作按钮和异常提示区域。",
                "typical_workflow": "用户确认项目信息，依次查看生成状态并在完成后打开预览。",
                "backend_interactions": "页面通过本地接口读取 SQLite 中的任务、阶段和产物状态。",
                "result_validation_recovery": "成功时展示可预览资产，失败时保留阶段结果并允许单步重试。",
            }
            imported = service.import_image(
                job["id"], source, "ui_operations", "项目概览页面", description
            )
            target = data_root / "tasks" / "task" / imported["image_relative_path"]
            self.assertEqual(target.suffix, ".png")
            self.assertTrue(service.read_image(job["id"], imported["screenshot_key"])
                            .startswith(b"\x89PNG"))
            revised_description = dict(description)
            revised_description["page_purpose"] = "该页面用于集中查看项目状态、材料完整度并进入正式生成流程。"
            revised = service.update_metadata(
                job["id"], imported["screenshot_key"], "introduction",
                "项目工作台页面", revised_description,
            )
            self.assertEqual(revised["version"], 2)
            self.assertEqual(revised["section_key"], "introduction")
            replacement = root / "overview-new.png"
            Image.new("RGB", (1440, 900), "#cbd5e1").save(replacement, format="PNG")
            replaced = service.replace_image(job["id"], imported["screenshot_key"], replacement)
            self.assertEqual(replaced["version"], 3)
            self.assertEqual((replaced["width"], replaced["height"]), (1440, 900))
            archived = service.set_archived(job["id"], imported["screenshot_key"], True)
            self.assertTrue(archived["archived"])
            self.assertEqual(service.list(job["id"]), [])
            self.assertEqual(len(service.list(job["id"], include_archived=True)), 1)
            archived_rollback = service.rollback(job["id"], imported["screenshot_key"], 2)
            self.assertTrue(archived_rollback["archived"])
            self.assertEqual(service.list(job["id"]), [])
            restored = service.set_archived(job["id"], imported["screenshot_key"], False)
            self.assertFalse(restored["archived"])
            rolled_back = service.rollback(job["id"], imported["screenshot_key"], 2)
            self.assertEqual(rolled_back["version"], 7)
            self.assertEqual(rolled_back["title"], "项目工作台页面")
            history = service.revisions(job["id"], imported["screenshot_key"])
            self.assertEqual([item["version"] for item in history], [7, 6, 5, 4, 3, 2, 1])
            self.assertEqual(history[0]["edit_source"], "rollback")
            result = service.finalize(job["id"])
            self.assertEqual(result["status"], "completed")
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "assemble_docx")
            self.assertEqual(refreshed["progress"]["completed"], 4)

    def test_not_applicable_screenshot_stage_is_skipped_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database, job = self._fixture(Path(temporary), with_ui=False)
            service = ManualScreenshotService(database, data_root)
            result = service.finalize(job["id"])
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["assessment"]["status"], "not_applicable")
            refreshed = ManualPipelineService(database).get(job["id"])
            step = next(item for item in refreshed["steps"] if item["key"] == "screenshots")
            self.assertEqual(step["status"], "skipped")
            self.assertEqual(refreshed["current_step"], "assemble_docx")


if __name__ == "__main__":
    unittest.main()
