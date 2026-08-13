from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import time

from .manual_document import ManualDocumentService
from .manual_drafting import ManualDraftingService
from .manual_execution import ManualExecutionNodeService
from .manual_figures import ManualFigureService
from .manual_pipeline import ManualPipelineService
from .manual_research import ManualResearchService
from .manual_qa import ManualQaService
from .manual_screenshots import ManualScreenshotService
from .manual_screenshot_evidence import ScreenshotEvidenceService
from .storage import Database


class ManualWorkflowError(ValueError):
    pass


class ManualWorkflowService:
    """Runs the formal pipeline behind one user action while retaining every checkpoint."""

    def __init__(self, database: Database, data_root: Path, *, pipeline=None,
                 research=None, drafting=None, figures=None, screenshots=None,
                 documents=None, qa=None, execution=None, screenshot_evidence=None) -> None:
        self._pipeline = pipeline or ManualPipelineService(database)
        self._execution = execution or (
            ManualExecutionNodeService(database) if pipeline is None else _NoopExecutionNodes()
        )
        self._research = research or ManualResearchService(database, data_root)
        self._drafting = drafting or ManualDraftingService(database, data_root)
        self._figures = figures or ManualFigureService(database, data_root)
        self._screenshots = screenshots or ManualScreenshotService(database, data_root)
        self._screenshot_evidence = screenshot_evidence or (
            ScreenshotEvidenceService(database, data_root)
            if pipeline is None else _NoopScreenshotEvidence()
        )
        self._documents = documents or ManualDocumentService(database, data_root)
        self._qa = qa or ManualQaService(database, data_root, documents=self._documents)

    def generate(self, task_id: str, model_config_id: str) -> dict:
        job = self._pipeline.create(task_id, model_config_id)
        return self.run_existing(job["id"])

    def prepare_context(self, job_id: str) -> dict:
        """Freeze the evidence profile before screenshot interpretation.

        This checkpoint is deliberately idempotent.  Quick Start calls it before
        importing screenshots and ``run_existing`` calls it again when continuing
        the same job; a completed research node is reused rather than creating a
        new research artifact or invalidating already reviewed screenshots.
        """
        nodes = {item["key"]: item for item in self._execution.list(job_id)} \
            if hasattr(self._execution, "list") else {}
        self._execution.prepare(
            job_id, "research", "research", "research", "项目证据研究", max_attempts=1,
        )
        research_node = nodes.get("research")
        research = self._research.latest(job_id) if hasattr(self._research, "latest") else None
        if not research or not research_node or research_node.get("status") not in {
                "completed", "completed_with_warnings"}:
            self._execution.running(job_id, "research", 1)
            try:
                research = self._research.execute(job_id)
                self._execution.complete(job_id, "research", {
                    "version": research.get("version"),
                    "elapsed_ms": research.get("elapsed_ms"),
                    "note_count": len(research.get("research_notes", [])),
                    "reuse_policy": "后续阶段复用此研究快照，不重复创建说明书任务",
                })
            except Exception as error:
                self._execution.fail(job_id, "research", str(error), "research")
                raise
        task_id = self._pipeline.get(job_id).get("task_id") or "task"
        self._execution.prepare(
            job_id, "project_profile", "research", "profile", "截图理解项目概要",
            dependencies=["research"], max_attempts=1,
        )
        try:
            project_profile = self._screenshot_evidence.prepare_profile(task_id)
            self._execution.complete(job_id, "project_profile", {
                "version": project_profile["version"],
                "origin": project_profile["origin"],
                "fingerprint": project_profile.get("fingerprint"),
                "next_action": "项目画像已冻结；截图解读将绑定此版本并可直接复用",
            })
        except Exception as error:
            self._execution.fail(job_id, "project_profile", str(error), "project_profile")
            raise
        return {"research": research, "project_profile": project_profile,
                "task_id": task_id}

    def run_existing(self, job_id: str) -> dict:
        """Run a job that was persisted before execution was dispatched.

        Desktop callers use this entry point from a background worker so page
        navigation or a closed HTTP response cannot erase the durable job state.
        """
        figure_executor = None
        try:
            context = self.prepare_context(job_id)
            research = context["research"]
            project_profile = context["project_profile"]
            task_id = context["task_id"]
            self._execution.prepare(
                job_id, "screenshot_plan", "screenshot_plan", "screenshot",
                "界面截图候选规划", dependencies=["research"], max_attempts=1,
            )

            def screenshot_plan_task() -> dict:
                self._execution.running(job_id, "screenshot_plan", 1)
                try:
                    planned = self._screenshots.assess(job_id)
                    self._execution.complete(job_id, "screenshot_plan", {
                        "assessment_status": planned.get("status"),
                        "reason": planned.get("reason"),
                        "candidate_count": len(planned.get("static_entries", [])),
                        "next_action": planned.get("next_action"),
                    }, warnings=planned.get("status") == "not_applicable")
                    return {"assessment": planned, "error": None}
                except Exception as error:
                    self._execution.fail(
                        job_id, "screenshot_plan", str(error), "screenshot_planning"
                    )
                    return {"assessment": None, "error": str(error)}

            incremental_figures = all(hasattr(self._figures, name) for name in (
                "begin_incremental", "generate_for_section", "finish_incremental"
            ))
            figure_stream = None
            figure_futures = []
            figure_dispatch_lock = Lock()
            if incremental_figures:
                figure_executor = ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="manual-section-figure"
                )

            def dispatch_section_figures(section: dict) -> None:
                nonlocal figure_stream
                if not incremental_figures or figure_executor is None:
                    return
                with figure_dispatch_lock:
                    if figure_stream is None:
                        figure_stream = self._figures.begin_incremental(job_id)
                    figure_futures.append(figure_executor.submit(
                        self._figures.generate_for_section,
                        job_id, section["section_key"],
                    ))

            self._execution.prepare(
                job_id, "section:ui_operations", "draft", "section",
                "用户界面与操作说明", dependencies=["project_profile"], max_attempts=2,
            )
            try:
                ui_snapshot = self._screenshot_evidence.snapshot_for_job(job_id)
            except Exception as error:
                ui_snapshot = {"profile": project_profile, "screenshots": [],
                               "snapshot_error": str(error)}
            ui_decision = self._screenshot_evidence.get_ui_decision(task_id)

            def ui_chapter_task() -> dict:
                if not ui_snapshot.get("screenshots"):
                    if ui_decision["decision"] in {"source_inferred", "not_applicable"}:
                        self._execution.complete(job_id, "section:ui_operations", {
                            "evidence_mode": ui_decision["decision"],
                            "reason": ui_decision["reason"],
                            "decision_version": ui_decision["version"],
                            "next_action": ("人工审阅源码推断版，并确认不作为真实截图证据"
                                            if ui_decision["decision"] == "source_inferred"
                                            else "项目已明确标记为不适用用户界面截图"),
                        }, warnings=ui_decision["decision"] == "source_inferred")
                        return {"section": None, "error": None,
                                "status": ui_decision["decision"]}
                    self._execution.waiting_for_screenshots(job_id, "section:ui_operations", {
                        "reason": ui_snapshot.get("snapshot_error") or
                                  "尚无已审核并确认采用的真实截图",
                        "next_action": "导入并审核真实截图；其他章节和阶段审阅稿继续生成",
                    })
                    return {"section": None, "error": None,
                            "status": "waiting_for_screenshots"}
                self._execution.running(job_id, "section:ui_operations", 1)
                try:
                    section = self._drafting.generate_ui_from_screenshots(
                        job_id, ui_snapshot["profile"]["profile"],
                        ui_snapshot["screenshots"],
                    )
                    source = self._screenshot_evidence.record_ui_sources(
                        job_id, section["id"], ui_snapshot["profile"]["id"],
                        ui_snapshot["screenshots"],
                    )
                    self._execution.complete(job_id, "section:ui_operations", {
                        "version": section["version"], "source_count": source["source_count"],
                        "adopted_set_hash": source["adopted_set_hash"],
                        "next_action": "审阅截图驱动的第 7 章",
                    })
                    return {"section": section, "error": None, "status": "completed"}
                except Exception as error:
                    self._execution.fail(job_id, "section:ui_operations", str(error),
                                         "ui_section_generation")
                    return {"section": None, "error": str(error), "status": "failed"}

            # Candidate planning only reads the persisted project manifest, so it can
            # run beside model-backed chapter drafting.  Each completed chapter also
            # dispatches its own figure requests immediately; no all-prose barrier remains.
            with ThreadPoolExecutor(max_workers=3,
                                    thread_name_prefix="manual-draft-plan") as executor:
                draft_future = executor.submit(
                    self._drafting.generate_all, job_id,
                    on_section_completed=dispatch_section_figures,
                ) if incremental_figures else executor.submit(
                    self._drafting.generate_all, job_id
                )
                screenshot_plan_future = executor.submit(screenshot_plan_task)
                ui_future = executor.submit(ui_chapter_task)
                draft = draft_future.result()
                screenshot_plan = screenshot_plan_future.result()
                ui_result = ui_future.result()
            section_dependencies = [
                "section:{0}".format(item.get("section_key"))
                for item in draft.get("sections", []) if item.get("section_key")
            ]
            if ui_result.get("section"):
                section_dependencies.append("section:ui_operations")
            self._execution.prepare(
                job_id, "review_checkpoint", "assemble_docx", "assemble",
                "正文预览快照（非最终装配）",
                dependencies=section_dependencies or ["research"],
                max_attempts=1,
            )
            self._execution.prepare(
                job_id, "screenshots", "screenshots", "screenshot", "界面截图候选与授权",
                dependencies=(["screenshot_plan"] + (
                    ["section:ui_operations"] if ui_result.get("section") else []
                )), max_attempts=1,
            )

            def checkpoint_task() -> dict:
                started = time.monotonic()
                self._execution.running(job_id, "review_checkpoint", 1)
                try:
                    checkpoint = self._documents.assemble_checkpoint(job_id)
                    self._execution.complete(job_id, "review_checkpoint", {
                        "version": checkpoint["version"],
                        "filename": checkpoint.get("filename"),
                        "artifact_path": checkpoint.get("docx_relative_path"),
                        "document_kind": "review_checkpoint",
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                        "next_action": ("仅供提前审阅正文，不是正式候选稿；等待图表任务结束、"
                                        "截图证据确认后再进入正式装配"),
                    })
                    return {"document": checkpoint, "error": None}
                except Exception as error:
                    self._execution.fail(
                        job_id, "review_checkpoint", str(error), "checkpoint_assembly"
                    )
                    return {"document": None, "error": str(error)}

            def figure_task() -> dict:
                try:
                    if incremental_figures:
                        if figure_stream is None:
                            figure_stream_value = self._figures.begin_incremental(job_id)
                        else:
                            figure_stream_value = figure_stream
                        results = [future.result() for future in list(figure_futures)]
                        return self._figures.finish_incremental(
                            job_id, figure_stream_value, results
                        )
                    return self._figures.generate_all(job_id)
                except Exception as error:
                    available = []
                    if hasattr(self._figures, "list"):
                        try:
                            available = self._figures.list(job_id)
                        except Exception:
                            available = []
                    return {"status": "completed_with_warnings", "figures": available,
                            "errors": [{"message": str(error), "category": "diagram"}]}

            def screenshot_task() -> dict:
                self._execution.running(job_id, "screenshots", 1)
                try:
                    if screenshot_plan.get("assessment") is None:
                        raise ValueError(
                            screenshot_plan.get("error") or "截图候选规划失败"
                        )
                    current_assessment = screenshot_plan["assessment"]
                    current_stage = self._screenshots.finalize(job_id)
                    project_assets = self._screenshot_evidence.list_assets(task_id)
                    adopted = [item for item in project_assets
                               if item["adoption_status"] == "adopted" and not item["archived"]]
                    pending_review = [item for item in project_assets
                                      if item["review_status"] != "reviewed" and
                                      not item["archived"]]
                    output = {
                        "status": current_stage["status"],
                        "count": len(adopted),
                        "pending_review_count": len(pending_review),
                        "assessment_status": current_assessment.get("status"),
                        "reason": current_assessment.get("reason"),
                        "next_action": current_assessment.get("next_action"),
                    }
                    if pending_review:
                        self._execution.waiting_for_review(job_id, "screenshots", output)
                    elif not adopted:
                        if ui_decision["decision"] in {"source_inferred", "not_applicable"}:
                            output.update({"evidence_mode": ui_decision["decision"],
                                           "decision_reason": ui_decision["reason"]})
                            self._execution.complete(
                                job_id, "screenshots", output,
                                warnings=ui_decision["decision"] == "source_inferred",
                            )
                        else:
                            self._execution.waiting_for_screenshots(job_id, "screenshots", output)
                    else:
                        self._execution.complete(
                            job_id, "screenshots", output,
                            warnings=current_stage["status"] != "completed",
                        )
                    return {"assessment": current_assessment, "stage": current_stage,
                            "error": None}
                except Exception as error:
                    self._execution.fail(job_id, "screenshots", str(error), "screenshot")
                    return {
                        "assessment": {"status": "warning", "reason": str(error),
                                       "next_action": "稍后重试截图规划或人工导入真实截图"},
                        "stage": {"status": "completed_with_warnings", "screenshots": []},
                        "error": str(error),
                    }

            # These nodes share only completed section artifacts.  The prose checkpoint
            # can therefore land while model-backed figures and authorization-bound
            # screenshot planning continue independently.
            with ThreadPoolExecutor(max_workers=3,
                                    thread_name_prefix="manual-assets") as executor:
                checkpoint_future = executor.submit(checkpoint_task)
                figures_future = executor.submit(figure_task)
                screenshots_future = executor.submit(screenshot_task)
                checkpoint_result = checkpoint_future.result()
                figures = figures_future.result()
                screenshot_result = screenshots_future.result()
            assessment = screenshot_result["assessment"]
            screenshot_stage = screenshot_result["stage"]
            asset_nodes = self._execution.list(job_id) if hasattr(self._execution, "list") else []
            blocking_assets = [item for item in asset_nodes if (
                item.get("kind") == "figure" and item.get("status") != "completed"
            ) or (
                item.get("key") == "screenshots" and item.get("status") in {
                    "failed", "waiting_for_authorization", "waiting_for_review",
                    "waiting_for_screenshots", "outdated",
                }
            )]
            if blocking_assets:
                reason = "；".join("{0}：{1}".format(item["title"], item["status"])
                                  for item in blocking_assets[:6])
                self._pipeline.wait_for_assets(job_id, reason)
                return {
                    "job": self._pipeline.get(job_id),
                    "research": {"version": research.get("version"),
                                 "elapsed_ms": research.get("elapsed_ms"),
                                 "note_count": len(research.get("research_notes", []))},
                    "draft": {"status": draft["status"],
                              "section_count": len(draft["sections"]) +
                              (1 if ui_result.get("section") else 0),
                              "errors": draft["errors"]},
                    "figures": {"status": figures["status"],
                                "count": len(figures["figures"]),
                                "errors": figures["errors"]},
                    "screenshots": {"assessment": assessment,
                                    "status": screenshot_stage["status"],
                                    "count": len(screenshot_stage["screenshots"])},
                    "checkpoint": checkpoint_result,
                    "document": checkpoint_result.get("document"),
                    "quality": None,
                    "awaiting_assets": True,
                }
            figure_dependencies = [
                item["key"] for item in self._execution.list(job_id)
                if item.get("kind") == "figure"
            ] if hasattr(self._execution, "list") else []
            self._execution.prepare(
                job_id, "assemble", "assemble_docx", "assemble", "Word 文档装配",
                dependencies=["review_checkpoint", "screenshots"] + figure_dependencies,
                max_attempts=1,
            )
            self._execution.running(job_id, "assemble", 1)
            try:
                document = self._documents.assemble(job_id)
                self._execution.complete(job_id, "assemble", {
                    "version": document["version"], "filename": document.get("filename"),
                    "artifact_path": document.get("docx_relative_path"),
                    "document_kind": "formal_candidate",
                    "next_action": "执行逐页渲染与质量检查",
                })
            except Exception as error:
                self._execution.fail(job_id, "assemble", str(error), "document_assembly")
                raise
            self._execution.prepare(
                job_id, "qa", "render_qa", "qa", "逐页渲染与质量检查",
                dependencies=["assemble"], max_attempts=1,
            )
            self._execution.running(job_id, "qa", 1)
            try:
                quality = self._qa.execute(job_id, document["version"])
                self._execution.complete(job_id, "qa", {
                    "passed": quality["qa_run"]["passed"],
                    "page_count": quality["qa_run"].get("page_count"),
                    "next_action": ("人工确认后生成终稿" if quality["qa_run"]["passed"]
                                    else "查看问题、重试失败节点或导出审阅稿"),
                }, warnings=not quality["qa_run"]["passed"])
            except Exception as error:
                self._execution.fail(job_id, "qa", str(error), "quality_assurance")
                raise
        except Exception as error:
            raise ManualWorkflowError(str(error)) from error
        finally:
            if figure_executor is not None:
                figure_executor.shutdown(wait=True)
        return {
            "job": self._pipeline.get(job_id),
            "research": {
                "version": research.get("version"),
                "elapsed_ms": research.get("elapsed_ms"),
                "note_count": len(research.get("research_notes", [])),
            },
            "draft": {
                "status": draft["status"], "section_count": len(draft["sections"]) +
                (1 if ui_result.get("section") else 0),
                "errors": draft["errors"],
            },
            "figures": {
                "status": figures["status"], "count": len(figures["figures"]),
                "errors": figures["errors"],
            },
            "screenshots": {
                "assessment": assessment, "status": screenshot_stage["status"],
                "count": len(screenshot_stage["screenshots"]),
            },
            "checkpoint": checkpoint_result,
            "document": quality["document"],
            "quality": quality["qa_run"],
        }


class _NoopExecutionNodes:
    def prepare(self, *args, **kwargs): return None
    def running(self, *args, **kwargs): return None
    def complete(self, *args, **kwargs): return None
    def waiting_for_authorization(self, *args, **kwargs): return None
    def waiting_for_screenshots(self, *args, **kwargs): return None
    def waiting_for_review(self, *args, **kwargs): return None
    def fail(self, *args, **kwargs): return None


class _NoopScreenshotEvidence:
    def prepare_profile(self, task_id):
        return {"id": "profile", "version": 1, "origin": "research", "profile": {}}
    def snapshot_for_job(self, job_id):
        return {"profile": self.prepare_profile("task"), "screenshots": []}
    def get_ui_decision(self, task_id):
        return {"version": 0, "decision": "waiting_for_screenshots", "reason": ""}
    def record_ui_sources(self, *args, **kwargs):
        return {"source_count": 0, "adopted_set_hash": ""}
    def list_assets(self, task_id): return []
