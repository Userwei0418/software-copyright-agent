from pathlib import Path

from .manual_document import ManualDocumentService
from .manual_drafting import ManualDraftingService
from .manual_figures import ManualFigureService
from .manual_pipeline import ManualPipelineService
from .manual_research import ManualResearchService
from .manual_screenshots import ManualScreenshotService
from .storage import Database


class ManualWorkflowError(ValueError):
    pass


class ManualWorkflowService:
    """Runs the formal pipeline behind one user action while retaining every checkpoint."""

    def __init__(self, database: Database, data_root: Path, *, pipeline=None,
                 research=None, drafting=None, figures=None, screenshots=None,
                 documents=None) -> None:
        self._pipeline = pipeline or ManualPipelineService(database)
        self._research = research or ManualResearchService(database, data_root)
        self._drafting = drafting or ManualDraftingService(database, data_root)
        self._figures = figures or ManualFigureService(database, data_root)
        self._screenshots = screenshots or ManualScreenshotService(database, data_root)
        self._documents = documents or ManualDocumentService(database, data_root)

    def generate(self, task_id: str, model_config_id: str) -> dict:
        job = self._pipeline.create(task_id, model_config_id)
        try:
            research = self._research.execute(job["id"])
            draft = self._drafting.generate_all(job["id"])
            figures = self._figures.generate_all(job["id"])
            assessment = self._screenshots.assess(job["id"])
            screenshot_stage = self._screenshots.finalize(job["id"])
            document = self._documents.assemble(job["id"])
        except Exception as error:
            raise ManualWorkflowError(str(error)) from error
        return {
            "job": self._pipeline.get(job["id"]),
            "research": {
                "version": research.get("version"),
                "elapsed_ms": research.get("elapsed_ms"),
                "note_count": len(research.get("research_notes", [])),
            },
            "draft": {
                "status": draft["status"], "section_count": len(draft["sections"]),
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
            "document": document,
        }
