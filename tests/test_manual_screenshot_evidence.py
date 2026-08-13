import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.manual_screenshot_evidence import (
    ScreenshotEvidenceError, ScreenshotEvidenceService,
)
from software_copyright_agent.storage import Database


class ScreenshotEvidenceServiceTests(unittest.TestCase):
    def _fixture(self, root: Path):
        data_root = root / "data"
        database = Database(data_root / "app.db")
        database.initialize()
        now = "2026-08-12T00:00:00Z"
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO project_sources(id,kind,original_path,display_name,created_at,last_opened_at)
                VALUES('source','directory','/tmp/project','真实截图系统',?,?)""", (now, now))
            connection.execute(
                """INSERT INTO project_snapshots(id,source_id,root_fingerprint,scanner_version,
                rules_version,summary_json,manifest_relative_path,scan_root_mode,scan_root_path,created_at)
                VALUES('snapshot','source','hash','v1','v1','{}','input/manifest.jsonl',
                'external','/tmp/project',?)""", (now,))
            connection.execute(
                """INSERT INTO tasks(id,source_id,snapshot_id,status,workflow_version,
                quality_policy_version,created_at,updated_at)
                VALUES('task','source','snapshot','completed','v1','v1',?,?)""", (now, now))
            connection.execute(
                """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                settings_json,enabled,created_at,updated_at)
                VALUES('vision-model','Vision','openai_compatible','http://127.0.0.1:9','gpt-4o-mini',
                '{"supports_vision":true,"vision_capability_verification":{"passed":true,"kind":"test"}}',1,?,?)""", (now, now))
            connection.execute(
                """INSERT INTO facts(id,task_id,fact_key,value_json,status,source,confidence,
                evidence_ids_json,created_at) VALUES('fact','task','route.main',
                '"/dashboard"','extracted','static',0.95,'[]',?)""", (now,))
        return data_root, database

    @staticmethod
    def _analysis():
        return {
            "page_title": "项目总览", "page_type": "dashboard",
            "purpose": "查看项目材料生成状态", "target_roles": ["材料编制人员"],
            "entry_conditions": ["已选择项目"], "visible_regions": ["任务列表", "状态栏"],
            "key_controls": ["生成按钮"], "workflow_steps": ["查看状态", "进入材料"],
            "success_state": "页面显示当前任务状态", "failure_and_recovery": "失败项提供重试入口",
            "related_backend_actions": [], "route_guess": "/dashboard",
            "related_evidence_refs": ["route.main"], "suggested_group": "项目管理",
            "suggested_order": 1, "suggested_caption": "项目总览页面",
            "confidence": 0.91, "warnings": [],
        }

    def test_pre_generation_batch_import_and_duplicate_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            first, duplicate = root / "screen-2.jpg", root / "screen-10.png"
            Image.new("RGB", (1280, 720), "#ddeeff").save(first)
            Image.new("RGB", (1280, 720), "#ddeeff").save(duplicate)
            service = ScreenshotEvidenceService(database, data_root)
            result = service.import_batch("task", [str(duplicate), str(first)], "folder")
            self.assertEqual(result["imported_count"], 1)
            self.assertEqual(result["warning_count"], 1)
            self.assertEqual(result["status"], "completed_with_warnings")
            asset = service.list_assets("task")[0]
            self.assertEqual(asset["source"], "folder")
            self.assertEqual(asset["analysis_status"], "pending")
            self.assertTrue(service.read_image("task", asset["id"]).startswith(b"\x89PNG"))

    def test_analysis_is_structured_versioned_reviewable_and_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "dashboard.png"
            Image.new("RGB", (1280, 720), "#dbeafe").save(image)
            calls = []

            def model_call(config, prompt, path):
                calls.append((config["model_name"], path.name))
                return json.dumps(self._analysis(), ensure_ascii=False)

            service = ScreenshotEvidenceService(database, data_root, model_call=model_call)
            profile = service.prepare_profile("task")
            self.assertEqual(profile["version"], 1)
            imported = service.import_batch("task", [str(image)])
            asset_id = imported["results"][0]["asset"]["id"]
            analyzed = service.analyze_many("task", [asset_id], "vision-model")
            self.assertEqual(analyzed["completed"], 1)
            self.assertEqual(len(calls), 1)
            cached = service.analyze_many("task", [asset_id], "vision-model")
            self.assertTrue(cached["results"][0]["cache_hit"])
            self.assertEqual(len(calls), 1)
            reviewed = service.review(
                "task", asset_id, self._analysis(), adopted=True,
                group_title="截屏", sort_order=2,
            )
            self.assertEqual(reviewed["review_status"], "reviewed")
            self.assertEqual(reviewed["adoption_status"], "adopted")
            self.assertEqual(reviewed["group_title"], "项目管理")
            withdrawn = next(item for item in service.set_adoption_status(
                "task", [asset_id], "pending") if item["id"] == asset_id)
            self.assertEqual(withdrawn["review_status"], "pending")
            self.assertEqual(withdrawn["adoption_status"], "pending")
            service.review("task", asset_id, self._analysis(), adopted=True,
                           group_title="项目管理", sort_order=2)
            changed_profile = dict(profile["profile"])
            changed_profile["purpose"] = "人工修订后的截图理解用途"
            service.save_profile("task", changed_profile)
            self.assertEqual(service.list_assets("task")[0]["analysis_status"], "outdated")

    def test_adoption_requires_explicit_sensitive_information_clearance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "review.png"
            Image.new("RGB", (1280, 720), "#f5f5f4").save(image)
            service = ScreenshotEvidenceService(
                database, data_root,
                model_call=lambda *_: json.dumps(self._analysis(), ensure_ascii=False),
            )
            asset_id = service.import_batch("task", [str(image)])["results"][0]["asset"]["id"]
            service.analyze_many("task", [asset_id], "vision-model")
            with self.assertRaisesRegex(ScreenshotEvidenceError, "必须确认"):
                service.review(
                    "task", asset_id, self._analysis(), adopted=True,
                    group_title="项目管理", sort_order=1,
                    sensitive_status="unreviewed",
                )

    def test_single_image_failure_is_retryable_without_rerunning_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "retry.png"
            Image.new("RGB", (1280, 720), "#fef3c7").save(image)
            calls = []

            def model_call(config, prompt, path):
                calls.append(path.name)
                return "not-json" if len(calls) <= 2 else json.dumps(
                    self._analysis(), ensure_ascii=False)

            service = ScreenshotEvidenceService(database, data_root, model_call=model_call)
            asset_id = service.import_batch("task", [str(image)])["results"][0]["asset"]["id"]
            failed = service.analyze_many("task", [asset_id], "vision-model")
            self.assertEqual(failed["failed"], 1)
            self.assertEqual(service.list_assets("task")[0]["analysis_status"], "failed")
            retried = service.retry_analysis("task", asset_id, "vision-model")
            self.assertEqual(retried["status"], "completed")
            self.assertEqual(len(calls), 3)

    def test_restart_recovery_turns_process_local_analysis_into_retryable_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "interrupted.png"
            Image.new("RGB", (1280, 720), "#dbeafe").save(image)
            service = ScreenshotEvidenceService(database, data_root)
            batch = service.import_batch("task", [str(image)])
            asset_id = batch["results"][0]["asset"]["id"]
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_project_screenshot_assets SET analysis_status='running' WHERE id=?",
                    (asset_id,),
                )
                connection.execute(
                    "UPDATE manual_screenshot_import_batches SET status='running' WHERE id=?",
                    (batch["id"],),
                )
            recovered = service.recover_interrupted()
            self.assertEqual(recovered["asset_count"], 1)
            self.assertEqual(recovered["batch_count"], 1)
            asset = service.list_assets("task")[0]
            self.assertEqual(asset["analysis_status"], "failed")
            self.assertIn("重试该图片", asset["failure_reason"])
            with database.connect() as connection:
                status = connection.execute(
                    "SELECT status FROM manual_screenshot_import_batches WHERE id=?",
                    (batch["id"],),
                ).fetchone()["status"]
            self.assertEqual(status, "failed")

    def test_unknown_model_is_not_silently_sent_an_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database = self._fixture(Path(temporary))
            with database.connect() as connection:
                connection.execute(
                    """UPDATE model_configs SET model_name='text-only-unknown',settings_json='{}'
                    WHERE id='vision-model'""")
            capability = ScreenshotEvidenceService(database, data_root).model_capability(
                "vision-model")
            self.assertEqual(capability["status"], "unknown")
            self.assertIn("无法确认", capability["message"])

    def test_provider_multimodal_rejection_disables_false_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "text-model.png"
            Image.new("RGB", (1280, 720), "#f8fafc").save(image)
            service = ScreenshotEvidenceService(
                database, data_root,
                model_call=lambda *_: (_ for _ in ()).throw(
                    ScreenshotEvidenceError("HTTP 400: model is not a multimodal model")
                ),
            )
            asset_id = service.import_batch("task", [str(image)])["results"][0]["asset"]["id"]
            failed = service.analyze_many("task", [asset_id], "vision-model")
            self.assertEqual(failed["failed"], 1)
            self.assertIn("已自动关闭", failed["results"][0]["message"])
            capability = service.model_capability("vision-model")
            self.assertEqual(capability["status"], "unsupported")
            self.assertIn("供应商已确认", capability["message"])

    def test_vision_capability_is_enabled_only_after_random_image_challenge(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database = self._fixture(Path(temporary))
            service = ScreenshotEvidenceService(database, data_root)
            original = service._vision_request
            try:
                service._vision_request = lambda config, image, prompt, related=None: '{"code":"WRONG"}'
                with self.assertRaisesRegex(ScreenshotEvidenceError, "图片能力验证失败"):
                    service.verify_vision_capability("vision-model")
                self.assertEqual(service.model_capability("vision-model")["status"], "unsupported")
            finally:
                service._vision_request = original

    def test_running_image_cannot_be_analyzed_by_a_duplicate_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            image = root / "running.png"
            Image.new("RGB", (1280, 720), "#f8fafc").save(image)
            calls = []
            service = ScreenshotEvidenceService(
                database, data_root, model_call=lambda *_: calls.append(True),
            )
            asset_id = service.import_batch("task", [str(image)])["results"][0]["asset"]["id"]
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_project_screenshot_assets SET analysis_status='running' WHERE id=?",
                    (asset_id,),
                )
            result = service.analyze_many("task", [asset_id], "vision-model")
            self.assertEqual(result["failed"], 1)
            self.assertIn("请勿重复", result["results"][0]["message"])
            self.assertEqual(calls, [])

    def test_image_replacement_versions_and_invalidates_dependent_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            first, second = root / "before.png", root / "after.png"
            Image.new("RGB", (1280, 720), "#000000").save(first)
            Image.new("RGB", (1280, 720), "#ffffff").save(second)
            service = ScreenshotEvidenceService(
                database, data_root,
                model_call=lambda *_: json.dumps(self._analysis(), ensure_ascii=False),
            )
            asset_id = service.import_batch("task", [str(first)])["results"][0]["asset"]["id"]
            service.analyze_many("task", [asset_id], "vision-model")
            service.review("task", asset_id, self._analysis(), adopted=True,
                           group_title="项目管理", sort_order=1)
            replaced = service.replace_image("task", asset_id, second)
            self.assertEqual(replaced["version"], 2)
            self.assertEqual(replaced["analysis_status"], "outdated")
            self.assertEqual(replaced["review_status"], "pending")
            self.assertEqual(replaced["adoption_status"], "pending")
            with database.connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) value FROM manual_project_screenshot_revisions WHERE asset_id=?",
                    (asset_id,),
                ).fetchone()["value"]
            self.assertEqual(count, 2)
            history = service.history("task", asset_id)
            self.assertEqual([item["version"] for item in history["image_revisions"]], [2, 1])
            restored = service.rollback(
                "task", asset_id, image_version=1, interpretation_version=2)
            self.assertEqual(restored["version"], 3)
            self.assertEqual(restored["analysis_status"], "completed")
            self.assertEqual(restored["review_status"], "reviewed")
            self.assertEqual(restored["adoption_status"], "pending")
            self.assertEqual(restored["interpretation"]["page_title"], "项目总览")

    def test_analysis_context_includes_same_group_images_and_confirmed_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            group = root / "认证流程"
            group.mkdir()
            first, second = group / "login.png", group / "failure.png"
            Image.new("RGB", (1280, 720), "#000000").save(first)
            Image.new("RGB", (1280, 720), "#ffffff").save(second)
            prompts = []

            def model_call(config, prompt, path):
                prompts.append((path.name, prompt))
                value = self._analysis()
                value["page_title"] = path.stem
                value["suggested_caption"] = path.stem + "页面"
                return json.dumps(value, ensure_ascii=False)

            service = ScreenshotEvidenceService(database, data_root, model_call=model_call)
            result = service.import_batch("task", [str(first), str(second)], "folder")
            assets = {item["title"]: item for item in service.list_assets("task")}
            login_id, failure_id = assets["login"]["id"], assets["failure"]["id"]
            service.analyze_many("task", [login_id], "vision-model")
            login = next(item for item in service.list_assets("task") if item["id"] == login_id)
            service.review("task", login_id, login["interpretation"], adopted=False,
                           group_title="认证流程", sort_order=1)
            service.analyze_many("task", [failure_id], "vision-model")
            self.assertIn("login", prompts[-1][1])
            failure = next(item for item in service.list_assets("task") if item["id"] == failure_id)
            service.review("task", failure_id, failure["interpretation"], adopted=False,
                           group_title="认证流程", sort_order=2)
            service.analyze_many("task", [failure_id], "vision-model")
            self.assertIn("confirmed_user_revisions", prompts[-1][1])
            self.assertIn('"version":2', prompts[-1][1])

    def test_explicit_ui_evidence_decision_is_versioned_and_requires_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database = self._fixture(Path(temporary))
            service = ScreenshotEvidenceService(database, data_root)
            self.assertEqual(service.get_ui_decision("task")["decision"],
                             "waiting_for_screenshots")
            with self.assertRaisesRegex(ScreenshotEvidenceError, "必须填写明确原因"):
                service.set_ui_decision("task", "source_inferred", "")
            decision = service.set_ui_decision(
                "task", "source_inferred", "用户确认当前没有可提供的真实截图")
            self.assertEqual(decision["version"], 1)
            self.assertEqual(service.get_ui_decision("task")["decision"], "source_inferred")

    def test_v29_compatibility_migrates_legacy_asset_and_lifts_reviewed_description(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database = self._fixture(root)
            job = ManualPipelineService(database).create("task", "vision-model")
            relative = "artifacts/manual-screenshots/legacy.v1.png"
            image_path = data_root / "tasks" / "task" / relative
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (1280, 720), "#dbeafe").save(image_path)
            description = {"page_purpose": "查看项目状态", "entry_conditions": "进入项目",
                           "visible_regions": "导航区与状态区", "typical_workflow": "查看并操作",
                           "backend_interactions": "读取本地任务状态",
                           "result_validation_recovery": "失败后可以重试"}
            now = "2026-08-12T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO manual_screenshot_artifacts(id,job_id,screenshot_key,
                    section_key,title,source,image_relative_path,description_json,created_at)
                    VALUES('legacy',?,'workspace','ui_operations','历史工作台','user',?,?,?)""",
                    (job["id"], relative, json.dumps(description, ensure_ascii=False), now),
                )
                connection.execute(
                    """INSERT INTO manual_screenshot_revisions(id,job_id,screenshot_key,version,
                    section_key,title,source,image_relative_path,description_json,width,height,
                    sha256,created_at) VALUES('legacy-r1',?,'workspace',1,'ui_operations',
                    '历史工作台','user',?,?,1280,720,'legacy-sha',?)""",
                    (job["id"], relative, json.dumps(description, ensure_ascii=False), now),
                )
                for table in ("manual_ui_evidence_decisions", "manual_ui_section_sources",
                              "manual_job_screenshot_refs",
                              "manual_screenshot_interpretation_revisions",
                              "manual_project_screenshot_revisions",
                              "manual_project_screenshot_assets",
                              "manual_screenshot_import_batches",
                              "manual_project_profile_revisions"):
                    connection.execute("DROP TABLE " + table)
                connection.execute("DELETE FROM schema_migrations WHERE version IN (29,30)")
            database.initialize()
            service = ScreenshotEvidenceService(database, data_root)
            service.prepare_profile("task")
            assets = service.list_assets("task")
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]["title"], "历史工作台")
            self.assertEqual(assets[0]["review_status"], "reviewed")
            self.assertEqual(assets[0]["adoption_status"], "adopted")
            self.assertEqual(assets[0]["interpretation"]["purpose"], "查看项目状态")
            self.assertTrue(service.read_image("task", assets[0]["id"]).startswith(b"\x89PNG"))
            history = service.history("task", assets[0]["id"])
            self.assertEqual(history["interpretation_revisions"][0]["origin"],
                             "legacy_migration")


if __name__ == "__main__":
    unittest.main()
