import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_workflow import ManualWorkflowService
from software_copyright_agent.storage import Database


class FakePipeline:
    def __init__(self, calls): self.calls = calls
    def create(self, task_id, model_id):
        self.calls.append(("create", task_id, model_id)); return {"id": "job"}
    def get(self, job_id):
        self.calls.append(("get", job_id)); return {"id": job_id, "progress": {"completed": 5}}


class FakeStage:
    def __init__(self, calls, name, result): self.calls, self.name, self.result = calls, name, result
    def execute(self, job_id, version=None):
        self.calls.append(
            (self.name, job_id) if version is None else (self.name, job_id, version)
        )
        return self.result
    def generate_all(self, job_id): self.calls.append((self.name, job_id)); return self.result
    def assess(self, job_id): self.calls.append((self.name, job_id)); return self.result
    def finalize(self, job_id): self.calls.append((self.name, job_id)); return self.result
    def assemble(self, job_id): self.calls.append((self.name, job_id)); return self.result


class ManualWorkflowServiceTests(unittest.TestCase):
    def test_one_action_runs_formal_stages_in_delivery_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            service = ManualWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                pipeline=FakePipeline(calls),
                research=FakeStage(calls, "research", {"version": 1, "elapsed_ms": 10,
                                                        "research_notes": [{}, {}]}),
                drafting=FakeStage(calls, "draft", {"status": "completed", "sections": [{}, {}],
                                                     "errors": []}),
                figures=FakeStage(calls, "figures", {"status": "completed", "figures": [{}],
                                                      "errors": []}),
                screenshots=ScreenshotStages(calls),
                documents=FakeStage(calls, "document", {"version": 1, "filename": "manual.docx"}),
                qa=FakeStage(calls, "qa", {
                    "document": {"version": 1, "filename": "manual.docx", "status": "qa_passed"},
                    "qa_run": {"passed": True, "page_count": 6},
                }),
            )
            result = service.generate("task", "model")
            self.assertEqual([item[0] for item in calls], [
                "create", "research", "draft", "figures", "assess", "finalize", "document", "qa", "get"
            ])
            self.assertEqual(result["document"]["version"], 1)
            self.assertEqual(result["draft"]["section_count"], 2)
            self.assertTrue(result["quality"]["passed"])


class ScreenshotStages:
    def __init__(self, calls): self.calls = calls
    def assess(self, job_id):
        self.calls.append(("assess", job_id)); return {"status": "manual_import", "reason": "adapter"}
    def finalize(self, job_id):
        self.calls.append(("finalize", job_id)); return {"status": "skipped", "screenshots": []}


if __name__ == "__main__":
    unittest.main()
