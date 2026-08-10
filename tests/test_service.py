import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.service import ScanProjectService
from software_copyright_agent.storage import Database


class FailingScanner:
    def scan(self, project_root: Path) -> object:
        raise OSError("simulated read failure")


class ScanProjectServiceTests(unittest.TestCase):
    def test_scan_is_persisted_with_manifest_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            (project / "main.py").write_text("print('hello')\n", encoding="utf-8")
            data_root = base / "data"
            database = Database(data_root / "app.db")

            persisted = ScanProjectService(database, data_root).execute(project)

            self.assertTrue(persisted.manifest_path.is_file())
            rows = persisted.manifest_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(rows[0])["path"], "main.py")

            connection = sqlite3.connect(str(database.path))
            try:
                task = connection.execute(
                    "SELECT status, snapshot_id, row_version FROM tasks WHERE id = ?",
                    (persisted.task_id,),
                ).fetchone()
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (persisted.task_id,),
                ).fetchone()[0]
                migration_count = connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(task[0], "completed")
            self.assertEqual(task[1], persisted.snapshot_id)
            self.assertEqual(task[2], 3)
            self.assertEqual(event_count, 3)
            self.assertEqual(migration_count, 1)

    def test_scan_failure_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            data_root = base / "data"
            database = Database(data_root / "app.db")
            service = ScanProjectService(database, data_root, scanner=FailingScanner())

            with self.assertRaises(OSError):
                service.execute(project)

            connection = sqlite3.connect(str(database.path))
            try:
                task = connection.execute(
                    "SELECT status, failure_category, row_version FROM tasks"
                ).fetchone()
                stage = connection.execute(
                    "SELECT status, failure_category FROM task_stages"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(task, ("failed", "scan_error", 3))
            self.assertEqual(stage, ("failed", "scan_error"))
