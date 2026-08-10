import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.confirmation import ConfirmationError, ConfirmationService
from software_copyright_agent.service import ScanProjectService
from software_copyright_agent.storage import Database


class ConfirmationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("# README Name\n", encoding="utf-8")
        self.database = Database(self.root / "data" / "app.db")
        self.persisted = ScanProjectService(
            self.database, self.root / "data"
        ).execute(self.project)
        self.service = ConfirmationService(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_partial_then_final_confirmation_resumes_task(self) -> None:
        first = self.service.answer(
            self.persisted.task_id, "project.name", "正式软件名称"
        )
        self.assertEqual(first.remaining_required, 1)
        self.assertEqual(first.task_status.value, "waiting_for_user")

        second = self.service.answer(
            self.persisted.task_id, "project.version", "V1.0"
        )
        self.assertEqual(second.remaining_required, 0)
        self.assertEqual(second.task_status.value, "completed")

        connection = sqlite3.connect(str(self.database.path))
        try:
            task = connection.execute(
                "SELECT status, row_version FROM tasks WHERE id = ?",
                (self.persisted.task_id,),
            ).fetchone()
            facts = connection.execute(
                """SELECT fact_key, value_json, status, source FROM facts
                WHERE task_id = ? ORDER BY fact_key, created_at""",
                (self.persisted.task_id,),
            ).fetchall()
            user_evidence_count = connection.execute(
                "SELECT COUNT(*) FROM evidence WHERE kind = 'user_confirmation'"
            ).fetchone()[0]
            confirmation_stage = connection.execute(
                """SELECT status FROM task_stages
                WHERE task_id = ? AND stage_key = '04_confirm_metadata'""",
                (self.persisted.task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(task, ("completed", 5))
        self.assertIn(
            ("project.name", json.dumps("README Name", ensure_ascii=False, separators=(",", ":")), "superseded", "deterministic"),
            facts,
        )
        self.assertIn(
            ("project.name", json.dumps("正式软件名称", ensure_ascii=False, separators=(",", ":")), "confirmed", "user"),
            facts,
        )
        self.assertIn(
            ("project.version", json.dumps("V1.0", ensure_ascii=False, separators=(",", ":")), "confirmed", "user"),
            facts,
        )
        self.assertEqual(user_evidence_count, 2)
        self.assertEqual(confirmation_stage, "succeeded")

    def test_duplicate_confirmation_is_rejected(self) -> None:
        self.service.answer(self.persisted.task_id, "project.name", "正式名称")

        with self.assertRaises(ConfirmationError):
            self.service.answer(self.persisted.task_id, "project.name", "另一个名称")

        connection = sqlite3.connect(str(self.database.path))
        try:
            answered = connection.execute(
                """SELECT COUNT(*) FROM confirmation_requests
                WHERE field_key = 'project.name' AND status = 'answered'"""
            ).fetchone()[0]
            user_facts = connection.execute(
                """SELECT COUNT(*) FROM facts
                WHERE fact_key = 'project.name' AND source = 'user'"""
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(answered, 1)
        self.assertEqual(user_facts, 1)
