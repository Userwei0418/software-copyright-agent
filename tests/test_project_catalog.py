import json
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.project_catalog import ProjectCatalogService
from software_copyright_agent.storage import Database


class ProjectCatalogServiceTests(unittest.TestCase):
    def test_delete_task_removes_manual_workflows_before_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = Database(root / "app.db")
            database.initialize()
            now = "2026-08-11T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id, kind, original_path, display_name,
                    created_at, last_opened_at) VALUES ('source', 'directory', '/tmp/project',
                    '测试项目', ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id, source_id, root_fingerprint,
                    scanner_version, rules_version, summary_json, manifest_relative_path,
                    created_at) VALUES ('snapshot', 'source', 'hash', 'v1', 'v1', ?,
                    'manifest.jsonl', ?)""", (json.dumps({"file_count": 1}), now),
                )
                connection.execute(
                    """INSERT INTO tasks(id, source_id, snapshot_id, status, workflow_version,
                    quality_policy_version, created_at, updated_at) VALUES
                    ('task', 'source', 'snapshot', 'completed', 'v1', 'v1', ?, ?)""",
                    (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                    settings_json, enabled, created_at, updated_at) VALUES
                    ('model', 'Local', 'ollama', 'http://127.0.0.1:11434', 'writer', '{}',
                    1, ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO manual_draft_runs(id, task_id, model_config_id, version,
                    status, summary_json, elapsed_ms, created_at) VALUES
                    ('draft', 'task', 'model', 1, 'completed', '{}', 1, ?)""", (now,),
                )
            job = ManualPipelineService(database).create("task", "model")

            ProjectCatalogService(database, root).delete_task("task")

            with database.connect() as connection:
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM tasks WHERE id='task'"
                ).fetchone())
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM manual_draft_runs WHERE task_id='task'"
                ).fetchone())
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM manual_generation_jobs WHERE id=?", (job["id"],)
                ).fetchone())
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM model_configs WHERE id='model'"
                ).fetchone())


if __name__ == "__main__":
    unittest.main()
