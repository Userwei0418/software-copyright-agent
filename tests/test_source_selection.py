import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.service import ScanProjectService
from software_copyright_agent.source_plan_service import SourcePlanError, SourcePlanService
from software_copyright_agent.source_selection import SourceSelector
from software_copyright_agent.storage import Database


def create_project(root: Path) -> None:
    (root / "package.json").write_text(
        '{"name":"source-demo","version":"1.0.0"}', encoding="utf-8"
    )
    files = {
        "src/features/order_service.py": "def create_order():\n    return True\n",
        "src/main.py": "def main():\n    return True\n",
        "src/utils/helper.py": "def helper():\n    return True\n",
        "api/routes.py": "def route():\n    return True\n",
        "tests/test_order.py": "def test_order():\n    assert True\n",
        "migrations/001_init.py": "def upgrade():\n    pass\n",
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class SourceSelectorTests(unittest.TestCase):
    def test_exclusions_and_abc_grades_are_explainable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_project(root)
            data = root / "manifest.jsonl"
            rows = []
            for path in sorted(root.rglob("*.py")):
                rows.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size": path.stat().st_size,
                        "category": "source",
                        "language": "Python",
                        "is_binary": False,
                    }
                )
            data.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

            plan = SourceSelector().build(root, data)
            candidates = {item.relative_path: item for item in plan.candidates}

            self.assertEqual(candidates["src/features/order_service.py"].grade, "A")
            self.assertIn("business_module", candidates["src/features/order_service.py"].reasons)
            self.assertEqual(candidates["src/main.py"].grade, "A")
            self.assertIn("application_entry", candidates["src/main.py"].reasons)
            self.assertEqual(candidates["api/routes.py"].grade, "B")
            self.assertEqual(candidates["src/utils/helper.py"].grade, "C")
            self.assertEqual(candidates["tests/test_order.py"].exclusion_code, "test_code")
            self.assertEqual(candidates["migrations/001_init.py"].exclusion_code, "migration_code")

            relaxed = SourceSelector().build(root, data, strategy="relaxed")
            maximum = SourceSelector().build(root, data, strategy="maximum")
            relaxed_by_path = {item.relative_path: item for item in relaxed.candidates}
            maximum_by_path = {item.relative_path: item for item in maximum.candidates}
            self.assertTrue(relaxed_by_path["src/utils/helper.py"].selected)
            self.assertFalse(relaxed_by_path["tests/test_order.py"].selected)
            self.assertTrue(maximum_by_path["tests/test_order.py"].selected)
            self.assertTrue(maximum_by_path["migrations/001_init.py"].selected)

    def test_service_filename_is_ranked_as_business_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src" / "payment_service.py"
            source.parent.mkdir(parents=True)
            source.write_text("def pay():\n    return True\n", encoding="utf-8")
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "path": "src/payment_service.py",
                        "size": source.stat().st_size,
                        "category": "source",
                        "language": "Python",
                        "is_binary": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            candidate = SourceSelector().build(root, manifest).candidates[0]

            self.assertEqual(candidate.grade, "A")
            self.assertIn("business_module", candidate.reasons)


class SourcePlanServiceTests(unittest.TestCase):
    def test_plan_is_versioned_persisted_and_updates_task_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            create_project(project)
            data_root = base / "data"
            database = Database(data_root / "app.db")
            task = ScanProjectService(database, data_root).execute(project)
            service = SourcePlanService(database, data_root)

            first = service.execute(task.task_id)
            second = service.execute(task.task_id)

            self.assertEqual(first.version, 1)
            self.assertEqual(second.version, 2)
            self.assertTrue(first.artifact_path.is_file())
            self.assertEqual(first.plan.selected_files, 3)

            connection = sqlite3.connect(str(database.path))
            try:
                runs = connection.execute(
                    "SELECT COUNT(*) FROM source_plan_runs WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()[0]
                candidates = connection.execute(
                    "SELECT COUNT(*) FROM source_candidates"
                ).fetchone()[0]
                task_row = connection.execute(
                    "SELECT status, current_stage_key, row_version FROM tasks WHERE id = ?",
                    (task.task_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(runs, 2)
            self.assertEqual(candidates, first.plan.total_source_files * 2)
            self.assertEqual(task_row, ("completed", "05_select_source", 7))

    def test_waiting_task_cannot_build_source_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            (project / "README.md").write_text("# Needs confirmation\n", encoding="utf-8")
            data_root = base / "data"
            database = Database(data_root / "app.db")
            task = ScanProjectService(database, data_root).execute(project)

            with self.assertRaises(SourcePlanError):
                SourcePlanService(database, data_root).execute(task.task_id)
