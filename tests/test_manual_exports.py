import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_exports import ManualExportError, ManualExportService
from software_copyright_agent.manual_document import GENERATOR_VERSION
from software_copyright_agent.manual_qa import QA_POLICY_VERSION
from software_copyright_agent.storage import Database


class ManualExportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "app.db")
        self.database.initialize()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO project_sources VALUES (?,?,?,?,?,?)",
                ("source", "directory", str(self.root), "demo", "now", "now"),
            )
            connection.execute(
                """INSERT INTO tasks(id,source_id,status,workflow_version,
                quality_policy_version,created_at,updated_at) VALUES
                ('task','source','completed','mvp-1','mvp-1','now','now')"""
            )
            connection.execute(
                """INSERT INTO manual_generation_jobs(id,task_id,model_config_id,version,
                status,current_step,progress_json,created_at,updated_at) VALUES
                ('job','task','model',1,'completed_with_warnings','render_qa','{}','now','now')"""
            )
            self.body = b"PK\x03\x04verified-docx-fixture"
            self.digest = hashlib.sha256(self.body).hexdigest()
            connection.execute(
                """INSERT INTO manual_document_artifacts(id,job_id,version,status,
                docx_relative_path,qa_json,sha256,created_at) VALUES
                ('document','job',1,'qa_failed','artifact.docx','{}',?,'now')""",
                (self.digest,),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_records_a_verified_review_export(self) -> None:
        destination = self.root / "demo-审阅稿.docx"
        destination.write_bytes(self.body)
        result = ManualExportService(self.database).record(
            "job", 1, "review", str(destination), len(self.body), self.digest,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["destination_path"], str(destination.resolve()))
        records = ManualExportService(self.database).list("job")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["export_kind"], "review")

    def test_rejects_a_file_that_does_not_match_document(self) -> None:
        destination = self.root / "broken.docx"
        destination.write_bytes(b"not-the-document")
        with self.assertRaisesRegex(ManualExportError, "说明书不一致"):
            ManualExportService(self.database).record(
                "job", 1, "review", str(destination), destination.stat().st_size,
                hashlib.sha256(destination.read_bytes()).hexdigest(),
            )

    def test_review_candidate_cannot_be_recorded_as_formal_export(self) -> None:
        destination = self.root / "demo-终稿.docx"
        destination.write_bytes(self.body)
        with self.assertRaisesRegex(ManualExportError, "审阅稿不能登记为终稿"):
            ManualExportService(self.database).record(
                "job", 1, "formal", str(destination), len(self.body), self.digest,
            )

    def test_final_export_requires_passed_current_qa_and_generator(self) -> None:
        destination = self.root / "final.docx"
        destination.write_bytes(self.body)
        with self.database.connect() as connection:
            connection.execute("UPDATE manual_document_artifacts SET qa_json=? WHERE id='document'",
                               (json.dumps({"document_kind": "final_document",
                                            "generator_version": GENERATOR_VERSION}),))
        export = lambda: ManualExportService(self.database).record(
            "job", 1, "formal", str(destination), len(self.body), self.digest)
        with self.assertRaisesRegex(ManualExportError, "质量检查"):
            export()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_document_qa_runs(id,document_artifact_id,job_id,qa_version,
                policy_version,renderer_kind,passed,checks_json,summary_json,report_relative_path,
                render_relative_path,preview_pdf_relative_path,created_at)
                VALUES ('qa','document','job',1,?,'libreoffice_word',0,'[]','{}','report','render','pdf','now')""",
                (QA_POLICY_VERSION,))
        with self.assertRaisesRegex(ManualExportError, "质量检查"):
            export()
        with self.database.connect() as connection:
            connection.execute("UPDATE manual_document_qa_runs SET passed=1,policy_version='old'")
        with self.assertRaisesRegex(ManualExportError, "质量检查"):
            export()
        with self.database.connect() as connection:
            connection.execute("UPDATE manual_document_qa_runs SET policy_version=?", (QA_POLICY_VERSION,))
            connection.execute("UPDATE manual_document_artifacts SET qa_json=? WHERE id='document'",
                               (json.dumps({"document_kind": "final_document", "generator_version": "old"}),))
        with self.assertRaisesRegex(ManualExportError, "质量检查"):
            export()
        with self.database.connect() as connection:
            connection.execute("UPDATE manual_document_artifacts SET qa_json=? WHERE id='document'",
                               (json.dumps({"document_kind": "final_document",
                                            "generator_version": GENERATOR_VERSION}),))
        self.assertTrue(export()["verified"])


if __name__ == "__main__":
    unittest.main()
