import json
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.run_diagnostics import RunDiagnosticsService
from software_copyright_agent.storage import Database


class RunDiagnosticsTests(unittest.TestCase):
    def test_export_payload_redacts_credentials_paths_and_large_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = Database(root / "app.db")
            database.initialize()
            config = {"software_name": "演示", "project_path": "/Users/example/project",
                      "screenshot_folder": "/Users/example/screens", "api_token": "secret"}
            stages = [{"key": "finalize", "title": "装配双文档", "description": "",
                       "status": "failed", "attempt": 12, "message": "quality gate",
                       "started_at": None, "finished_at": None,
                       "output": {"failure": {"content": "x" * 5000,
                                              "path": "/Users/example/private.docx"}}}]
            now = "2026-08-14T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO quick_start_runs(id,status,current_stage,config_json,
                    stages_json,outputs_json,created_at,updated_at)
                    VALUES('run-1','failed','finalize',?,?,?, ?,?)""",
                    (json.dumps(config), json.dumps(stages), "{}", now, now),
                )
            payload = RunDiagnosticsService(database).recent(5)
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(payload["run_count"], 1)
            self.assertIn("[REDACTED]", encoded)
            self.assertIn("[OMITTED]", encoded)
            self.assertNotIn("/Users/example", encoded)
            self.assertNotIn('"secret"', encoded)


if __name__ == "__main__":
    unittest.main()
