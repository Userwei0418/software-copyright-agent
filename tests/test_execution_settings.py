import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from uuid import uuid4

from software_copyright_agent.app_settings import AppSettingsService
from software_copyright_agent.manual_execution import (
    ManualExecutionNodeService, manual_job_slot,
)
from software_copyright_agent.model_config import ModelConfigInput, ModelConfigService
from software_copyright_agent.storage import Database


class ExecutionSettingsTests(unittest.TestCase):
    def test_advanced_style_prompts_have_safe_defaults_and_are_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            service = AppSettingsService(database)
            values = service.get()
            self.assertIn("正式", values["document_style_prompt"])
            self.assertIn("阅读方向", values["diagram_style_prompt"])
            values["document_style_prompt"] = "自定义专业文档风格"
            values["diagram_style_prompt"] = "自定义横向技术图风格"
            saved = service.save(values)
            self.assertEqual(saved["document_style_prompt"], "自定义专业文档风格")
            self.assertEqual(service.get()["diagram_style_prompt"], "自定义横向技术图风格")

    def test_advanced_style_prompt_length_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = AppSettingsService(Database(Path(temporary) / "app.db"))
            values = service.get()
            values["diagram_style_prompt"] = "x" * 12001
            with self.assertRaisesRegex(ValueError, "Advanced style prompts"):
                service.save(values)

    def test_effective_concurrency_respects_task_and_connection_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            models = ModelConfigService(database)
            settings = AppSettingsService(database)
            model = models.upsert(ModelConfigInput(
                id="model-config-0001", name="商汤", protocol_id="openai_compatible",
                base_url="https://api.senseaudio.cn/v1", model_name="senseaudio-s2",
                credential_ref="sense-provider", max_concurrency=10,
            ))
            self.assertEqual(model["max_concurrency"], 10)
            values = settings.get()
            values["generation_concurrency"] = 3
            settings.save(values)
            self.assertEqual(settings.effective_concurrency(model["id"]), 3)

            values["generation_concurrency"] = 10
            settings.save(values)
            models.upsert(ModelConfigInput(
                id=model["id"], name="商汤", protocol_id="openai_compatible",
                base_url="https://api.senseaudio.cn/v1", model_name="senseaudio-s2",
                credential_ref="sense-provider", max_concurrency=4,
            ))
            self.assertEqual(settings.effective_concurrency(model["id"]), 4)

    def test_upsert_preserves_detected_endpoint_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            models = ModelConfigService(database)
            value = ModelConfigInput(
                id="model-config-0002", name="模型服务", protocol_id="openai_compatible",
                base_url="https://example.com/v1", model_name="example-model",
                max_concurrency=3,
            )
            models.upsert(value)
            models.set_endpoint_mode(value.id, "chat_completions")
            updated = models.upsert(ModelConfigInput(
                **{**value.__dict__, "max_concurrency": 6}
            ))
            self.assertEqual(updated["endpoint_mode"], "chat_completions")
            self.assertEqual(updated["max_concurrency"], 6)

    def test_vision_capability_is_explicit_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            models = ModelConfigService(database)
            value = ModelConfigInput(
                id="model-config-vision", name="模型服务",
                protocol_id="openai_compatible", base_url="https://example.com/v1",
                model_name="custom-multimodal", max_concurrency=5,
            )
            created = models.upsert(value)
            self.assertIsNone(created["supports_vision"])
            models.set_endpoint_mode(value.id, "chat_completions")
            enabled = models.set_vision_capability(value.id, True)
            self.assertTrue(enabled["supports_vision"])
            self.assertEqual(enabled["endpoint_mode"], "chat_completions")
            self.assertEqual(enabled["max_concurrency"], 5)

    def test_default_screenshot_model_requires_real_image_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            models = ModelConfigService(database)
            model = models.upsert(ModelConfigInput(
                id="verified-vision-model", name="视觉模型",
                protocol_id="openai_compatible", base_url="https://example.com/v1",
                model_name="vision", max_concurrency=3,
            ))
            models.set_endpoint_mode(model["id"], "chat_completions")
            models.set_vision_capability(model["id"], True)
            values = AppSettingsService(database).get()
            values["vision_model_id"] = model["id"]
            with self.assertRaisesRegex(ValueError, "real-image"):
                AppSettingsService(database).save(values)
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT settings_json FROM model_configs WHERE id=?", (model["id"],)
                ).fetchone()
                settings = __import__("json").loads(row["settings_json"])
                settings["vision_capability_verification"] = {"passed": True}
                connection.execute(
                    "UPDATE model_configs SET settings_json=? WHERE id=?",
                    (__import__("json").dumps(settings), model["id"]),
                )
            saved = AppSettingsService(database).save(values)
            self.assertEqual(saved["vision_model_id"], model["id"])

    def test_existing_senseaudio_connection_gets_ultra_compatible_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            database.initialize()
            now = "2026-08-11T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                    settings_json,enabled,created_at,updated_at)
                    VALUES('legacy-sense','商汤','openai_compatible',
                    'https://api.senseaudio.cn/v1','senseaudio-s2','{}',1,?,?)""", (now, now),
                )
            item = next(value for value in ModelConfigService(database).list()
                        if value["id"] == "legacy-sense")
            self.assertEqual(item["max_concurrency"], 10)
            values = AppSettingsService(database).get()
            values["generation_concurrency"] = 10
            AppSettingsService(database).save(values)
            self.assertEqual(AppSettingsService(database).effective_concurrency("legacy-sense"), 10)

    def test_execution_node_persists_dependencies_attempt_and_failure_category(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            database.initialize()
            now = "2026-08-11T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id,kind,original_path,display_name,
                    created_at,last_opened_at)
                    VALUES('source','directory','/tmp/project','project',?,?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO tasks(id,source_id,status,current_stage_key,workflow_version,
                    quality_policy_version,row_version,created_at,updated_at)
                    VALUES('task','source','completed','done','v1','v1',1,?,?)""",
                    (now, now),
                )
                connection.execute(
                    """INSERT INTO manual_generation_jobs(id,task_id,model_config_id,version,
                    status,current_step,progress_json,created_at,updated_at)
                    VALUES('job','task','model',1,'running','draft','{}',?,?)""", (now, now),
                )
            nodes = ManualExecutionNodeService(database)
            nodes.prepare("job", "section:introduction", "draft", "section", "引言",
                          dependencies=["research"], max_attempts=3)
            nodes.running("job", "section:introduction", 2)
            failed = nodes.fail("job", "section:introduction", "模型输出不完整",
                                "content_validation")
            self.assertEqual(failed["dependencies"], ["research"])
            self.assertEqual(failed["attempt"], 2)
            self.assertEqual(failed["error_category"], "content_validation")
            queued = nodes.queued("job", "section:introduction")
            self.assertEqual(queued["status"], "queued")
            self.assertIsNone(queued["safe_error_message"])
            nodes.prepare("job", "screenshots", "screenshots", "screenshot", "界面截图",
                          dependencies=["section:ui_operations"])
            nodes.running("job", "screenshots", 1)
            waiting = nodes.waiting_for_authorization(
                "job", "screenshots", {"next_action": "明确授权后启动项目"}
            )
            self.assertEqual(waiting["status"], "waiting_for_authorization")
            self.assertEqual(waiting["output"]["next_action"], "明确授权后启动项目")

    def test_job_slots_bound_workflow_and_item_retry_together(self):
        state = {"active": 0, "peak": 0}
        lock = Lock()
        job_id = str(uuid4())

        def work():
            with manual_job_slot(job_id, 2):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                time.sleep(0.02)
                with lock:
                    state["active"] -= 1

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda _: work(), range(6)))
        self.assertEqual(state["peak"], 2)


if __name__ == "__main__":
    unittest.main()
