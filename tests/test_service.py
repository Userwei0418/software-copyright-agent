import json
import sqlite3
import tempfile
import unittest
import zipfile
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
            (project / "package.json").write_text(
                '{"name":"demo-app","version":"1.2.3","dependencies":{"react":"1"}}',
                encoding="utf-8",
            )
            data_root = base / "data"
            database = Database(data_root / "app.db")

            persisted = ScanProjectService(database, data_root).execute(project)

            self.assertTrue(persisted.manifest_path.is_file())
            self.assertTrue(persisted.scan_report_path.is_file())
            rows = persisted.manifest_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(rows[0])["path"], "main.py")
            report = json.loads(persisted.scan_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], 1)

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
                fact_count = connection.execute(
                    "SELECT COUNT(*) FROM facts WHERE task_id = ?", (persisted.task_id,)
                ).fetchone()[0]
                confirmation_count = connection.execute(
                    "SELECT COUNT(*) FROM confirmation_requests WHERE task_id = ?",
                    (persisted.task_id,),
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(task[0], "completed")
            self.assertEqual(task[1], persisted.snapshot_id)
            self.assertEqual(task[2], 3)
            self.assertEqual(event_count, 5)
            self.assertEqual(migration_count, 31)
            self.assertGreaterEqual(fact_count, 4)
            self.assertEqual(confirmation_count, 0)

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

    def test_zip_project_is_extracted_scanned_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "project.zip"
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr("project/main.py", "print('zip')\n")
            data_root = base / "data"
            database = Database(data_root / "app.db")

            persisted = ScanProjectService(database, data_root).execute(archive)

            connection = sqlite3.connect(str(database.path))
            try:
                source = connection.execute(
                    "SELECT kind, original_path FROM project_sources"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(source, ("zip", str(archive.resolve())))
            self.assertEqual([item.relative_path for item in persisted.result.files], ["main.py"])
            self.assertIn("input/extracted/project", str(persisted.result.root))
