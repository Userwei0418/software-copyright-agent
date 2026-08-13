import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.source_materials import (
    SourceMaterialsError,
    SourceMaterialsService,
)
from software_copyright_agent.source_document import GENERATOR_VERSION
from software_copyright_agent.source_document_qa import QA_POLICY_VERSION


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, document, qa):
        self._document = document
        self._qa = qa

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, _params):
        if "FROM source_document_runs" in query:
            return _Result(self._document)
        if "FROM source_document_qa_runs" in query:
            return _Result(self._qa)
        raise AssertionError(query)


class _Database:
    def __init__(self, document, qa):
        self._connection = _Connection(document, qa)

    def initialize(self):
        pass

    def connect(self):
        return self._connection


class SourceDocumentPreviewTests(unittest.TestCase):
    def test_representative_sample_rejects_concentrated_legacy_preview(self):
        self.assertFalse(SourceMaterialsService._representative_sample({
            "selected_files": 264, "included_files": 9,
        }))
        self.assertTrue(SourceMaterialsService._representative_sample({
            "selected_files": 264, "included_files": 19,
            "available_buckets": ["frontend_view", "backend_service", "backend_domain"],
            "included_buckets": ["frontend_view", "backend_service", "backend_domain"],
        }))

    def test_legacy_formatter_is_an_explicit_blocker(self):
        blockers = SourceMaterialsService._blockers(
            "completed",
            {"summary": {}},
            {
                "summary": {
                    "sufficient": True,
                    "selected_files": 12,
                    "included_files": 12,
                    "available_buckets": ["backend_service"],
                    "included_buckets": ["backend_service"],
                },
                "current_formatter": False,
            },
        )
        self.assertTrue(any("分页规则已升级" in item for item in blockers))

    def test_reads_real_rendered_pages_for_latest_document_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render = root / "tasks" / "task-1" / "qa" / "source-code" / "v2" / "render"
            render.mkdir(parents=True)
            # LibreOffice/Poppler renders zero-padded names. The reader must
            # match numeric page numbers rather than construct page-1.png.
            (render / "page-01.png").write_bytes(b"\x89PNG\r\n\x1a\nfirst")
            (render / "page-02.png").write_bytes(b"\x89PNG\r\n\x1a\nlast")
            service = SourceMaterialsService(
                _Database(
                    {"id": "document-3", "version": 3,
                     "generator_version": GENERATOR_VERSION},
                    {"version": 2, "passed": 1,
                     "policy_version": QA_POLICY_VERSION,
                     "render_relative_path": "qa/source-code/v2/render"},
                ),
                root,
            )

            self.assertEqual(service.source_document_preview("task-1"), {
                "version": 3, "qa_version": 2, "total_pages": 2,
                "quality_status": "passed", "pages": [1, 2],
            })
            self.assertTrue(
                service.read_source_document_preview_page("task-1", 2).startswith(b"\x89PNG")
            )

    def test_missing_qa_does_not_fall_back_to_fake_code_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = SourceMaterialsService(
                _Database({"id": "document-1", "version": 1}, None),
                Path(temporary),
            )
            with self.assertRaisesRegex(SourceMaterialsError, "真实 Word 渲染结果"):
                service.source_document_preview("task-1")

    def test_historical_generator_or_policy_is_not_current_quality(self):
        current_document = {"generator_version": GENERATOR_VERSION}
        current_qa = {"passed": 1, "policy_version": QA_POLICY_VERSION}
        self.assertEqual(
            SourceMaterialsService._quality_status(current_document, current_qa),
            "passed",
        )
        self.assertEqual(SourceMaterialsService._quality_status(
            {"generator_version": "source-docx-v1"}, current_qa), "outdated")
        self.assertEqual(SourceMaterialsService._quality_status(
            current_document, {"passed": 1, "policy_version": "source-docx-qa-v1"}),
            "outdated",
        )


if __name__ == "__main__":
    unittest.main()
