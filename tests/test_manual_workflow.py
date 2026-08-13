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
    def assemble_checkpoint(self, job_id):
        self.calls.append(("checkpoint", job_id))
        return {"version": 1, "filename": "review.docx",
                "docx_relative_path": "review.docx"}


class ManualWorkflowServiceTests(unittest.TestCase):
    def test_explicit_source_inferred_choice_finishes_without_fake_running_screenshot_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls, nodes = [], []

            class Evidence:
                def prepare_profile(self, task_id):
                    return {"id": "profile", "version": 1, "origin": "research", "profile": {}}
                def snapshot_for_job(self, job_id):
                    return {"profile": self.prepare_profile("task"), "screenshots": []}
                def get_ui_decision(self, task_id):
                    return {"version": 1, "decision": "source_inferred",
                            "reason": "用户确认当前无法提供真实截图"}
                def list_assets(self, task_id): return []

            class Execution:
                def prepare(self, job, key, *args, **kwargs): nodes.append(("prepare", key, None))
                def running(self, job, key, *args, **kwargs): nodes.append(("running", key, None))
                def complete(self, job, key, output=None, **kwargs):
                    nodes.append(("complete", key, output or {}))
                def waiting_for_screenshots(self, job, key, output=None):
                    nodes.append(("waiting_for_screenshots", key, output or {}))
                def waiting_for_review(self, job, key, output=None):
                    nodes.append(("waiting_for_review", key, output or {}))
                def fail(self, job, key, *args, **kwargs): nodes.append(("failed", key, {}))

            service = ManualWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                pipeline=FakePipeline(calls), execution=Execution(), screenshot_evidence=Evidence(),
                research=FakeStage(calls, "research", {"version": 1, "elapsed_ms": 1,
                                                        "research_notes": []}),
                drafting=FakeStage(calls, "draft", {"status": "completed", "sections": [],
                                                     "errors": []}),
                figures=FakeStage(calls, "figures", {"status": "completed", "figures": [],
                                                      "errors": []}),
                screenshots=ScreenshotStages(calls),
                documents=FakeStage(calls, "document", {"version": 1}),
                qa=FakeStage(calls, "qa", {"document": {"version": 1},
                                            "qa_run": {"passed": False}}),
            )
            service.run_existing("job")
            ui_completion = next(item for item in nodes
                                 if item[0] == "complete" and item[1] == "section:ui_operations")
            self.assertEqual(ui_completion[2]["evidence_mode"], "source_inferred")
            self.assertFalse(any(item[0] == "waiting_for_screenshots" and
                                 item[1] in {"section:ui_operations", "screenshots"}
                                 for item in nodes))

    def test_completed_chapter_dispatches_its_figure_before_draft_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = []

            class StreamingDraft:
                def generate_all(self, job_id, on_section_completed=None):
                    calls.append(("draft-start", job_id))
                    first = {"section_key": "architecture", "figure_requests": [{}]}
                    on_section_completed(first)
                    calls.append(("draft-after-first", job_id))
                    second = {"section_key": "modules", "figure_requests": [{}]}
                    on_section_completed(second)
                    return {"status": "completed", "sections": [first, second], "errors": []}

            class StreamingFigures:
                def begin_incremental(self, job_id):
                    calls.append(("figures-begin", job_id)); return {"step_id": "figures"}
                def generate_for_section(self, job_id, section_key):
                    calls.append(("figure-section", section_key)); return {"generated": [{}], "errors": []}
                def finish_incremental(self, job_id, stream, results):
                    calls.append(("figures-finish", len(results)))
                    return {"status": "completed", "figures": [{}, {}], "errors": []}

            service = ManualWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary), pipeline=FakePipeline(calls),
                research=FakeStage(calls, "research", {"version": 1, "elapsed_ms": 1,
                                                        "research_notes": []}),
                drafting=StreamingDraft(), figures=StreamingFigures(),
                screenshots=ScreenshotStages(calls),
                documents=FakeStage(calls, "document", {"version": 1}),
                qa=FakeStage(calls, "qa", {"document": {"version": 1},
                                            "qa_run": {"passed": True}}),
            )
            service.generate("task", "model")
            names = [item[0] for item in calls]
            self.assertIn(("figure-section", "architecture"), calls)
            self.assertIn(("figure-section", "modules"), calls)
            self.assertLess(names.index("figures-begin"), names.index("checkpoint"))
            self.assertLess(names.index("draft-after-first"), names.index("checkpoint"))

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
            names = [item[0] for item in calls]
            self.assertEqual(names[:2], ["create", "research"])
            self.assertIn("draft", names)
            self.assertLess(names.index("draft"), names.index("checkpoint"))
            self.assertLess(names.index("assess"), names.index("checkpoint"))
            self.assertLess(names.index("checkpoint"), names.index("document"))
            self.assertLess(names.index("figures"), names.index("document"))
            self.assertLess(names.index("finalize"), names.index("document"))
            self.assertEqual(names[-2:], ["qa", "get"])
            self.assertEqual(result["document"]["version"], 1)
            self.assertEqual(result["checkpoint"]["document"]["filename"], "review.docx")
            self.assertEqual(result["draft"]["section_count"], 2)
            self.assertTrue(result["quality"]["passed"])

    def test_existing_job_runs_without_creating_a_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            service = ManualWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                pipeline=FakePipeline(calls),
                research=FakeStage(calls, "research", {"version": 1, "elapsed_ms": 10,
                                                        "research_notes": []}),
                drafting=FakeStage(calls, "draft", {"status": "completed", "sections": [],
                                                     "errors": []}),
                figures=FakeStage(calls, "figures", {"status": "completed", "figures": [],
                                                      "errors": []}),
                screenshots=ScreenshotStages(calls),
                documents=FakeStage(calls, "document", {"version": 1}),
                qa=FakeStage(calls, "qa", {"document": {"version": 1},
                                            "qa_run": {"passed": True}}),
            )
            service.run_existing("persisted-job")
            self.assertNotIn("create", [item[0] for item in calls])
            self.assertEqual(calls[0], ("research", "persisted-job"))

    def test_figure_and_screenshot_failures_do_not_block_current_asset_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = []

            class FailingFigures:
                def generate_all(self, job_id):
                    calls.append(("figures", job_id)); raise ValueError("图表模型超时")
                def list(self, job_id): return []

            class FailingScreenshots:
                def assess(self, job_id):
                    calls.append(("assess", job_id)); raise ValueError("项目不可启动")

            service = ManualWorkflowService(
                Database(Path(temporary) / "app.db"), Path(temporary),
                pipeline=FakePipeline(calls),
                research=FakeStage(calls, "research", {"version": 1, "elapsed_ms": 10,
                                                        "research_notes": []}),
                drafting=FakeStage(calls, "draft", {"status": "completed", "sections": [],
                                                     "errors": []}),
                figures=FailingFigures(), screenshots=FailingScreenshots(),
                documents=FakeStage(calls, "document", {"version": 2,
                    "filename": "candidate.docx"}),
                qa=FakeStage(calls, "qa", {"document": {"version": 2},
                    "qa_run": {"passed": False}}),
            )
            result = service.run_existing("job")
            self.assertEqual(result["figures"]["status"], "completed_with_warnings")
            self.assertEqual(result["screenshots"]["status"], "completed_with_warnings")
            self.assertEqual(result["document"]["version"], 2)
            self.assertIn("document", [item[0] for item in calls])


class ScreenshotStages:
    def __init__(self, calls): self.calls = calls
    def assess(self, job_id):
        self.calls.append(("assess", job_id)); return {"status": "manual_import", "reason": "adapter"}
    def finalize(self, job_id):
        self.calls.append(("finalize", job_id)); return {"status": "skipped", "screenshots": []}


if __name__ == "__main__":
    unittest.main()
