import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_pipeline import (
    ManualPipelineError,
    ManualPipelineService,
    PIPELINE_STEPS,
)
from software_copyright_agent.manual_execution import ManualExecutionNodeService
from software_copyright_agent.storage import Database


class ManualPipelineServiceTests(unittest.TestCase):
    def test_progress_is_aggregated_from_real_execution_nodes(self) -> None:
        progress = ManualPipelineService._node_progress([
            {"status": "completed"}, {"status": "running"},
            {"status": "failed"}, {"status": "waiting_for_authorization"},
        ], {"completed": 1, "total": 6, "percent": 17, "current_title": "旧阶段"})
        self.assertEqual(progress["completed"], 3)
        self.assertEqual(progress["total"], 4)
        self.assertEqual(progress["percent"], 75)
        self.assertEqual(progress["running_nodes"], 1)
        self.assertEqual(progress["node_status_counts"]["waiting_for_authorization"], 1)

    def test_create_persists_versioned_job_and_independent_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id, kind, original_path, display_name,
                    created_at, last_opened_at) VALUES ('source', 'directory', '/tmp/project',
                    'project', ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id, source_id, root_fingerprint,
                    scanner_version, rules_version, summary_json, manifest_relative_path,
                    created_at) VALUES ('snapshot', 'source', 'hash', 'v1', 'v1', '{}',
                    'manifest.jsonl', ?)""", (now,),
                )
                connection.execute(
                    """INSERT INTO tasks(id, source_id, snapshot_id, status, workflow_version,
                    quality_policy_version, created_at, updated_at) VALUES
                    ('task', 'source', 'snapshot', 'completed', 'v1', 'v1', ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                    settings_json, enabled, created_at, updated_at) VALUES
                    ('model-config', 'provider', 'openai_compatible', 'https://example.com/v1',
                    'model', '{}', 1, ?, ?)""", (now, now),
                )
            service = ManualPipelineService(database)
            first = service.create("task", "model-config")
            with self.assertRaisesRegex(ManualPipelineError, "v1"):
                service.create("task", "model-config")
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_generation_jobs SET status='completed' WHERE id=?",
                    (first["id"],),
                )
            second = service.create("task", "model-config")
            self.assertEqual((first["version"], second["version"]), (1, 2))
            self.assertEqual(first["status"], "queued")
            self.assertEqual(first["current_step"], "research")
            self.assertEqual([step["key"] for step in first["steps"]], list(PIPELINE_STEPS))
            self.assertTrue(all(step["status"] == "pending" for step in first["steps"]))
            self.assertEqual([item["version"] for item in service.list_for_task("task")], [2, 1])

    def test_startup_recovery_marks_only_interrupted_work_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id,kind,original_path,display_name,
                    created_at,last_opened_at) VALUES ('s','directory','/tmp/p','p',?,?)""",
                    (now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id,source_id,root_fingerprint,
                    scanner_version,rules_version,summary_json,manifest_relative_path,created_at)
                    VALUES ('n','s','h','v1','v1','{}','manifest.jsonl',?)""", (now,),
                )
                connection.execute(
                    """INSERT INTO tasks(id,source_id,snapshot_id,status,workflow_version,
                    quality_policy_version,created_at,updated_at) VALUES
                    ('t','s','n','completed','v1','v1',?,?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                    settings_json,enabled,created_at,updated_at) VALUES
                    ('m','p','openai_compatible','https://example.com/v1','model','{}',1,?,?)""",
                    (now, now),
                )
            service = ManualPipelineService(database)
            job = service.create("t", "m")
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_generation_jobs SET status='completed' WHERE id=?",
                    (job["id"],),
                )
            completed_job = service.create("t", "m")
            execution = ManualExecutionNodeService(database)
            execution.prepare(completed_job["id"], "ui_section_update", "draft", "section",
                              "用户界面章节更新")
            execution.running(completed_job["id"], "ui_section_update", 1)
            with database.connect() as connection:
                connection.execute(
                    "UPDATE manual_generation_jobs SET status='running' WHERE id=?", (job["id"],))
                connection.execute(
                    """UPDATE manual_generation_steps SET status='running',started_at=?
                    WHERE job_id=? AND step_key='research'""", (now, job["id"]),
                )
                connection.execute(
                    "UPDATE manual_generation_jobs SET status='completed' WHERE id=?",
                    (completed_job["id"],),
                )
            self.assertEqual(service.recover_interrupted_jobs(), 1)
            recovered = service.get(job["id"])
            self.assertEqual(recovered["status"], "failed")
            self.assertIn("中断", recovered["safe_error_message"])
            self.assertEqual(recovered["steps"][0]["status"], "failed")
            completed_node = next(item for item in execution.list(completed_job["id"])
                                  if item["key"] == "ui_section_update")
            self.assertEqual(completed_node["status"], "failed")
            self.assertEqual(completed_node["error_category"], "interrupted")
            self.assertEqual(service.recover_interrupted_jobs(), 0)

    def test_listing_recovers_job_with_stale_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            database.initialize()
            old = "2020-01-01T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id,kind,original_path,display_name,
                    created_at,last_opened_at) VALUES ('s','directory','/tmp/p','p',?,?)""",
                    (old, old),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id,source_id,root_fingerprint,
                    scanner_version,rules_version,summary_json,manifest_relative_path,created_at)
                    VALUES ('n','s','h','v1','v1','{}','manifest.jsonl',?)""", (old,),
                )
                connection.execute(
                    """INSERT INTO tasks(id,source_id,snapshot_id,status,workflow_version,
                    quality_policy_version,created_at,updated_at) VALUES
                    ('t','s','n','completed','v1','v1',?,?)""", (old, old),
                )
                connection.execute(
                    """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                    settings_json,enabled,created_at,updated_at) VALUES
                    ('m','p','openai_compatible','https://example.com/v1','model','{}',1,?,?)""",
                    (old, old),
                )
            service = ManualPipelineService(database)
            job = service.create("t", "m")
            with database.connect() as connection:
                connection.execute(
                    """UPDATE manual_generation_jobs SET status='running',updated_at=?
                    WHERE id=?""", (old, job["id"]),
                )
            listed = service.list_for_task("t")
            self.assertEqual(listed[0]["status"], "failed")
            self.assertIn("长时间", listed[0]["safe_error_message"])


if __name__ == "__main__":
    unittest.main()
