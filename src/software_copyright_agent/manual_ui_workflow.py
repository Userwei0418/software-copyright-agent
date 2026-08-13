from pathlib import Path

from .manual_document import ManualDocumentService
from .manual_drafting import ManualDraftingService
from .manual_execution import ManualExecutionNodeService
from .manual_qa import ManualQaService
from .manual_screenshot_evidence import ScreenshotEvidenceService
from .storage import Database


class ManualUiWorkflowError(ValueError):
    pass


class ManualUiWorkflowService:
    """One durable action: adopt evidence, rewrite chapter 7, assemble once, then QA."""

    def __init__(self, database: Database, data_root: Path, *, evidence=None,
                 drafting=None, documents=None, qa=None, execution=None) -> None:
        self._evidence = evidence or ScreenshotEvidenceService(database, data_root)
        self._drafting = drafting or ManualDraftingService(database, data_root)
        self._documents = documents or ManualDocumentService(database, data_root)
        self._qa = qa or ManualQaService(database, data_root, documents=self._documents)
        self._execution = execution or ManualExecutionNodeService(database)

    def confirm_and_update(self, job_id: str) -> dict:
        self._execution.prepare(
            job_id, "section:ui_operations", "draft", "section", "用户界面说明",
            dependencies=["project_profile", "screenshots"], max_attempts=2,
        )
        self._execution.prepare(
            job_id, "ui_section_update", "draft", "section", "用户界面章节更新",
            dependencies=["project_profile", "screenshots"], max_attempts=2,
        )
        self._execution.running(job_id, "ui_section_update", 1)
        try:
            snapshot = self._evidence.snapshot_for_job(job_id)
            screenshots = snapshot["screenshots"]
            if not screenshots:
                self._execution.waiting_for_screenshots(job_id, "ui_section_update", {
                    "next_action": "至少审核并确认采用一张真实截图",
                })
                raise ManualUiWorkflowError("至少审核并确认采用一张真实截图")
            section = self._drafting.generate_ui_from_screenshots(
                job_id, snapshot["profile"]["profile"], screenshots
            )
            sources = self._evidence.record_ui_sources(
                job_id, section["id"], snapshot["profile"]["id"], screenshots
            )
            self._execution.complete(job_id, "ui_section_update", {
                "version": section["version"], "source_count": len(screenshots),
                "adopted_set_hash": sources["adopted_set_hash"],
                "next_action": "自动装配新的说明书候选版本",
            })
            self._execution.complete(job_id, "section:ui_operations", {
                "version": section["version"], "source_count": len(screenshots),
                "adopted_set_hash": sources["adopted_set_hash"],
            })
        except ManualUiWorkflowError:
            raise
        except Exception as error:
            self._execution.fail(job_id, "ui_section_update", str(error),
                                 "ui_section_generation")
            raise ManualUiWorkflowError(str(error)) from error

        self._execution.prepare(
            job_id, "ui_document_reassemble", "assemble_docx", "assemble",
            "截图驱动文档重新装配", dependencies=["ui_section_update"], max_attempts=2,
        )
        self._execution.running(job_id, "ui_document_reassemble", 1)
        try:
            document = self._documents.assemble(job_id)
            self._execution.complete(job_id, "ui_document_reassemble", {
                "version": document["version"], "artifact_path": document["docx_relative_path"],
                "next_action": "执行截图、第 7 章与最终文档一致性 QA",
            })
        except Exception as error:
            self._execution.fail(job_id, "ui_document_reassemble", str(error),
                                 "document_assembly")
            raise ManualUiWorkflowError(
                "用户界面章节已安全保存，但新候选稿装配失败：{0}".format(error)
            ) from error

        self._execution.prepare(
            job_id, "ui_screenshot_qa", "render_qa", "qa", "截图与正文一致性 QA",
            dependencies=["ui_document_reassemble"], max_attempts=2,
        )
        self._execution.running(job_id, "ui_screenshot_qa", 1)
        try:
            quality = self._qa.execute(job_id, document["version"])
            self._execution.complete(job_id, "ui_screenshot_qa", {
                "passed": quality["qa_run"]["passed"],
                "page_count": quality["qa_run"].get("page_count"),
                "next_action": ("审阅新的截图驱动候选稿" if quality["qa_run"]["passed"]
                                else "查看 QA 定位并仅修复失败项"),
            }, warnings=not quality["qa_run"]["passed"])
        except Exception as error:
            self._execution.fail(job_id, "ui_screenshot_qa", str(error), "quality_assurance")
            raise ManualUiWorkflowError(
                "新候选稿已安全保存，但截图一致性 QA 失败：{0}".format(error)
            ) from error
        return {"job_id": job_id, "section": section, "document": quality["document"],
                "quality": quality["qa_run"], "screenshot_count": len(screenshots)}
