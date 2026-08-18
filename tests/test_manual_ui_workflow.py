import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_ui_workflow import (
    ManualUiWorkflowError, ManualUiWorkflowService,
)
from software_copyright_agent.storage import Database


class FakeExecution:
    def __init__(self, calls): self.calls = calls
    def prepare(self, job, key, *args, **kwargs): self.calls.append(("prepare", key))
    def get(self, job, key):
        return {"attempt": 1, "max_attempts": 2}
    def queued(self, job, key): self.calls.append(("queued", key))
    def running(self, job, key, *args): self.calls.append(("running", key))
    def complete(self, job, key, output=None, warnings=False):
        self.calls.append(("complete", key, warnings))
    def waiting_for_screenshots(self, job, key, output=None):
        self.calls.append(("waiting", key))
    def fail(self, job, key, message, category): self.calls.append(("failed", key, category))


class FakeEvidence:
    def snapshot_for_job(self, job):
        return {"profile": {"id": "profile", "profile": {"software_name": "系统"}},
                "screenshots": [{"id": "asset", "revision_id": "asset-r1",
                    "interpretation_id": "analysis-r1", "interpretation_version": 1,
                    "title": "首页", "group_key": "home", "group_title": "首页",
                    "sort_order": 1, "interpretation": {"suggested_caption": "首页"}}]}
    def record_ui_sources(self, *args):
        return {"source_count": 1, "adopted_set_hash": "snapshot-hash"}


class FakeDrafting:
    def generate_ui_from_screenshots(self, job, profile, screenshots):
        return {"id": "section-r2", "version": 2, "section_key": "ui_operations"}


class FakeDocuments:
    def __init__(self, calls, fail=False): self.calls, self.fail = calls, fail
    def assemble(self, job):
        self.calls.append(("assemble", job))
        if self.fail: raise ValueError("装配器失败")
        return {"version": 4, "docx_relative_path": "candidate-v4.docx"}


class FakeQa:
    def __init__(self, calls): self.calls = calls
    def execute(self, job, version):
        self.calls.append(("qa", version))
        return {"document": {"version": version},
                "qa_run": {"passed": True, "page_count": 12}}


class ManualUiWorkflowTests(unittest.TestCase):
    def test_section_retry_uses_adopted_screenshots_without_assembling_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            service = ManualUiWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                evidence=FakeEvidence(), drafting=FakeDrafting(),
                documents=FakeDocuments(calls), qa=FakeQa(calls),
                execution=FakeExecution(calls),
            )
            result = service.regenerate_section("job")
            self.assertEqual(result["section_key"], "ui_operations")
            self.assertIn(("queued", "section:ui_operations"), calls)
            self.assertIn(("complete", "section:ui_operations", False), calls)
            self.assertFalse(any(item[0] in {"assemble", "qa"} for item in calls))

    def test_one_confirmation_rewrites_ui_assembles_once_and_runs_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            service = ManualUiWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                evidence=FakeEvidence(), drafting=FakeDrafting(),
                documents=FakeDocuments(calls), qa=FakeQa(calls),
                execution=FakeExecution(calls),
            )
            result = service.confirm_and_update("job")
            self.assertEqual(result["document"]["version"], 4)
            self.assertEqual(calls.count(("assemble", "job")), 1)
            self.assertEqual(calls.count(("qa", 4)), 1)

    def test_assembly_failure_keeps_ui_revision_and_does_not_run_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            service = ManualUiWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                evidence=FakeEvidence(), drafting=FakeDrafting(),
                documents=FakeDocuments(calls, fail=True), qa=FakeQa(calls),
                execution=FakeExecution(calls),
            )
            with self.assertRaisesRegex(ManualUiWorkflowError, "新候选稿装配失败"):
                service.confirm_and_update("job")
            self.assertEqual(calls.count(("assemble", "job")), 1)
            self.assertFalse(any(item[0] == "qa" for item in calls))


if __name__ == "__main__":
    unittest.main()
