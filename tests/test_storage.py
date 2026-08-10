import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.storage import Database, MIGRATION_001


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_v1_database_is_upgraded_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "app.db")
            connection = sqlite3.connect(str(database.path))
            try:
                connection.executescript(MIGRATION_001)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (1, 'now', 'migration-001')"
                )
                connection.commit()
            finally:
                connection.close()

            database.initialize()

            connection = sqlite3.connect(str(database.path))
            try:
                versions = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                connection.close()

            self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,), (18,)])
            self.assertTrue(
                {
                    "facts", "evidence", "confirmation_requests",
                    "source_plan_runs", "source_candidates", "code_preview_runs",
                    "source_document_runs",
                    "source_document_qa_runs",
                    "manual_plan_runs",
                    "diagram_plan_runs",
                    "diagram_artifact_runs",
                    "diagram_asset_revisions",
                    "model_credentials",
                    "manual_generation_jobs", "manual_research_artifacts",
                    "manual_section_revisions",
                    "manual_figure_revisions",
                }.issubset(tables)
            )
