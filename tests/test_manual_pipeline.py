import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_pipeline import ManualPipelineService, PIPELINE_STEPS
from software_copyright_agent.storage import Database


class ManualPipelineServiceTests(unittest.TestCase):
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
            second = service.create("task", "model-config")
            self.assertEqual((first["version"], second["version"]), (1, 2))
            self.assertEqual(first["status"], "queued")
            self.assertEqual(first["current_step"], "research")
            self.assertEqual([step["key"] for step in first["steps"]], list(PIPELINE_STEPS))
            self.assertTrue(all(step["status"] == "pending" for step in first["steps"]))
            self.assertEqual([item["version"] for item in service.list_for_task("task")], [2, 1])


if __name__ == "__main__":
    unittest.main()
