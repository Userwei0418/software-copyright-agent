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

            self.assertEqual(versions, [(index,) for index in range(1, 32)])
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
                    "manual_capture_assessments", "manual_screenshot_revisions",
                    "manual_document_qa_runs",
                    "manual_export_records",
                    "manual_execution_nodes",
                    "manual_project_profile_revisions",
                    "manual_screenshot_import_batches",
                    "manual_project_screenshot_assets",
                    "manual_project_screenshot_revisions",
                    "manual_screenshot_interpretation_revisions",
                    "manual_job_screenshot_refs", "manual_ui_section_sources",
                    "manual_ui_evidence_decisions",
                    "manual_qa_decisions",
                    "quick_start_runs",
                }.issubset(tables)
            )
