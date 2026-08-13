import argparse
import ctypes
import hmac
import json
import os
import queue
import socket
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .diagram_asset_service import DiagramAssetService
from .confirmation import ConfirmationError, ConfirmationService
from .ingestion import IngestionError
from .inspection import InspectionError, InspectionService
from .local_api import ApiResponse, DiagramAssetApi
from .scanner import ScanError
from .project_catalog import ProjectCatalogService
from .service import ScanProjectService, utc_now
from .source_materials import SourceMaterialsError, SourceMaterialsService
from .source_plan_service import SourcePlanError
from .code_preview import CodePreviewError
from .source_document import SourceDocumentError
from .source_document_qa import SourceDocumentQaError
from .manual_workspace import ManualWorkspaceError, ManualWorkspaceService
from .manual_plan_service import ManualPlanError
from .manual_generation import ManualGenerationError
from .manual_pipeline import ManualPipelineError, ManualPipelineService
from .manual_research import ManualResearchError, ManualResearchService
from .manual_drafting import ManualDraftingError, ManualDraftingService
from .manual_figures import ManualFigureError, ManualFigureService
from .manual_screenshots import ManualScreenshotError, ManualScreenshotService
from .manual_screenshot_evidence import ScreenshotEvidenceError, ScreenshotEvidenceService
from .capture_adapter import CaptureAdapterError, ProjectCaptureAdapterService
from .manual_document import ManualDocumentError, ManualDocumentService
from .manual_exports import ManualExportError, ManualExportService
from .manual_qa import ManualQaError, ManualQaService
from .manual_workflow import ManualWorkflowError, ManualWorkflowService
from .manual_ui_workflow import ManualUiWorkflowError, ManualUiWorkflowService
from .diagram_plan_service import DiagramPlanError
from .drawio_service import DrawioGenerationError
from .storage import Database
from .model_config import ModelConfigInput, ModelConfigService
from .credential_vault import CredentialVault
from .app_settings import AppSettingsService
from .quick_start import QuickStartError, QuickStartService
from .run_diagnostics import RunDiagnosticsService


SIDECAR_PROTOCOL_VERSION = 1
SIDECAR_VERSION = "0.1.0"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_DRAWIO_EDITOR_REQUEST_BYTES = 16 * 1024 * 1024
SESSION_HEADER = "X-Session-Token"
ALLOWED_DESKTOP_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "http://127.0.0.1:1420",
]


def _run_manual_job(workflow: ManualWorkflowService, job_id: str) -> None:
    """Keep a persisted manual job alive independently of its initiating request."""
    try:
        workflow.run_existing(job_id)
    except ManualWorkflowError:
        # Individual stages persist their safe failure state. The background
        # worker must never crash the local API process or expose model details.
        return


def _run_manual_ui_update(workflow: ManualUiWorkflowService, job_id: str) -> None:
    try:
        workflow.confirm_and_update(job_id)
    except ManualUiWorkflowError:
        return


class OverlayAction(str, Enum):
    NODE_MOVE = "node.move"
    NODE_RESIZE = "node.resize"
    NODE_STYLE = "node.style"
    NODE_LABEL = "node.label"
    NODE_HIDE = "node.hide"
    EDGE_ROUTE = "edge.route"
    EDGE_STYLE = "edge.style"
    EDGE_LABEL = "edge.label"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverlayOperationRequest(StrictModel):
    action: OverlayAction
    target: str = Field(min_length=1, max_length=200)
    payload: Dict[str, Any] = Field(default_factory=dict)
    expected_target_fingerprint: Optional[str] = Field(default=None, max_length=64)


class SaveRevisionRequest(StrictModel):
    edit_source: Literal["manual", "ai"]
    operations: List[OverlayOperationRequest] = Field(max_length=500)


class ManualFigureEditorRevisionRequest(StrictModel):
    xml: str = Field(min_length=32, max_length=4 * 1024 * 1024)
    svg: str = Field(min_length=32, max_length=6 * 1024 * 1024)
    png: str = Field(min_length=32, max_length=10 * 1024 * 1024)


class RollbackRequest(StrictModel):
    version: int = Field(ge=1)


class ManualFigureAiPreviewRequest(StrictModel):
    instruction: str = Field(min_length=3, max_length=2000)


class ManualFigureAiPatchRequest(StrictModel):
    instruction: str = Field(min_length=3, max_length=2000)
    xml: str = Field(min_length=32, max_length=4 * 1024 * 1024)
    model_config_id: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ConflictResolutionRequest(StrictModel):
    operation_index: int = Field(ge=0)
    resolution: Literal["drop", "accept_current", "retarget"]
    target: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ResolveRevisionRequest(StrictModel):
    resolutions: List[ConflictResolutionRequest] = Field(max_length=500)


class ScanProjectRequest(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class ConfirmationAnswerRequest(StrictModel):
    value: str = Field(min_length=1, max_length=500)


class ModelConfigRequest(StrictModel):
    id: str = Field(min_length=8, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    protocol_id: Literal["openai_compatible", "anthropic", "ollama"]
    base_url: str = Field(min_length=8, max_length=2048)
    model_name: str = Field(min_length=1, max_length=200)
    credential_ref: Optional[str] = Field(default=None, max_length=100)
    max_concurrency: int = Field(default=3, ge=1, le=10)


class CredentialRequest(StrictModel):
    api_key: str = Field(min_length=8, max_length=8192)


class EndpointModeRequest(StrictModel):
    endpoint_mode: Literal["messages", "chat_completions", "responses", "ollama_chat"]


class VisionCapabilityRequest(StrictModel):
    supports_vision: bool


class ManualGenerationRequest(StrictModel):
    model_config_id: str = Field(min_length=8, max_length=64)


class ManualSectionEditRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    blocks: List[Dict[str, Any]] = Field(min_length=3, max_length=100)


class ManualQaDecisionRequest(StrictModel):
    check_key: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=4, max_length=1000)


class ManualExportRecordRequest(StrictModel):
    document_version: int = Field(ge=1)
    export_kind: Literal["review", "formal"]
    destination_path: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ScreenshotDescriptionRequest(StrictModel):
    page_purpose: str = Field(min_length=12, max_length=2000)
    entry_conditions: str = Field(min_length=12, max_length=2000)
    visible_regions: str = Field(min_length=12, max_length=2000)
    typical_workflow: str = Field(min_length=12, max_length=2000)
    backend_interactions: str = Field(min_length=12, max_length=2000)
    result_validation_recovery: str = Field(min_length=12, max_length=2000)


class ScreenshotImportRequest(StrictModel):
    path: str = Field(min_length=1, max_length=4096)
    section_key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: ScreenshotDescriptionRequest
    source: Literal["user", "automated"] = "user"


class ScreenshotEditRequest(StrictModel):
    section_key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: ScreenshotDescriptionRequest


class ScreenshotReplaceRequest(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class ScreenshotRollbackRequest(StrictModel):
    image_version: Optional[int] = Field(default=None, ge=1)
    interpretation_version: Optional[int] = Field(default=None, ge=1)


class ScreenshotArchiveRequest(StrictModel):
    archived: bool


class ScreenshotBatchImportRequest(StrictModel):
    paths: List[str] = Field(min_length=1, max_length=500)
    source: Literal["user", "clipboard", "folder", "automated"] = "user"
    job_id: Optional[str] = Field(default=None, max_length=64)


class ScreenshotFolderImportRequest(StrictModel):
    path: str = Field(min_length=1, max_length=4096)
    recursive: bool = False
    job_id: Optional[str] = Field(default=None, max_length=64)


class ScreenshotClipboardImportRequest(StrictModel):
    data_base64: str = Field(min_length=8, max_length=28 * 1024 * 1024)
    filename: str = Field(default="clipboard.png", max_length=240)
    job_id: Optional[str] = Field(default=None, max_length=64)


class ScreenshotAnalyzeRequest(StrictModel):
    asset_ids: List[str] = Field(min_length=1, max_length=200)
    model_config_id: str = Field(min_length=1, max_length=200)
    job_id: Optional[str] = Field(default=None, max_length=64)


class ScreenshotNodeRetryRequest(StrictModel):
    node_key: str = Field(min_length=1, max_length=240)


class ScreenshotReviewRequest(StrictModel):
    interpretation: Dict[str, Any]
    adopted: bool = False
    group_title: str = Field(default="", max_length=200)
    sort_order: int = Field(default=0, ge=0, le=10000)
    sensitive_status: Literal["unreviewed", "confirmed_safe", "contains_sensitive"] = "confirmed_safe"


class ScreenshotAdoptionRequest(StrictModel):
    asset_ids: List[str] = Field(min_length=1, max_length=500)
    adopted: bool


class ScreenshotAdoptionStatusRequest(StrictModel):
    asset_ids: List[str] = Field(min_length=1, max_length=500)
    status: Literal["pending", "adopted", "excluded"]


class ProjectProfileRequest(StrictModel):
    profile: Dict[str, Any]


class UiEvidenceDecisionRequest(StrictModel):
    decision: Literal["waiting_for_screenshots", "source_inferred", "not_applicable"]
    reason: str = Field(default="", max_length=1000)


class AppSettingsRequest(StrictModel):
    manual_model_id: Optional[str] = Field(default=None, max_length=64)
    diagram_model_id: Optional[str] = Field(default=None, max_length=64)
    vision_model_id: Optional[str] = Field(default=None, max_length=64)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1024, le=32768)
    source_strategy: Literal["standard", "relaxed", "maximum"]
    auto_preview: bool
    generation_concurrency: int = Field(default=3, ge=1, le=10)
    document_style_prompt: str = Field(default="", max_length=12000)
    diagram_style_prompt: str = Field(default="", max_length=12000)


class QuickStartRequest(StrictModel):
    project_path: str = Field(min_length=1, max_length=4096)
    software_name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="V1.0", min_length=1, max_length=100)
    screenshot_folder: str = Field(min_length=1, max_length=4096)
    manual_model_id: str = Field(min_length=8, max_length=64)
    diagram_model_id: str = Field(min_length=8, max_length=64)
    vision_model_id: str = Field(min_length=8, max_length=64)
    source_strategy: Literal["standard", "relaxed", "maximum"] = "standard"
    concurrency: int = Field(default=3, ge=1, le=10)
    retry_limit: int = Field(default=2, ge=0, le=5)
    recursive_screenshots: bool = True
    finalize_with_warnings: bool = True
    sensitive_confirmed: bool
    auto_adopt_confirmed: bool


class RequestSizeLimitMiddleware:
    def __init__(self, app, maximum_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            maximum_bytes = (24 * 1024 * 1024
                             if scope.get("path", "").endswith("/screenshots/clipboard")
                             else MAX_DRAWIO_EDITOR_REQUEST_BYTES
                             if scope.get("path", "").endswith(
                                 ("/editor-revision", "/ai-patch", "/ai-patch-stream"))
                             else self.maximum_bytes)
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    too_large = int(content_length) > maximum_bytes
                except ValueError:
                    too_large = True
                if too_large:
                    response = JSONResponse(
                        status_code=413,
                        content={"error": {"code": "request_too_large",
                                           "message": "Request body exceeds the endpoint limit"}},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(data_dir: Path, session_token: str) -> FastAPI:
    service = DiagramAssetService(Database(data_dir / "app.db"), data_dir)
    database = Database(data_dir / "app.db")
    scan_service = ScanProjectService(database, data_dir)
    inspection_service = InspectionService(database)
    confirmation_service = ConfirmationService(database)
    catalog_service = ProjectCatalogService(database, data_dir)
    source_materials_service = SourceMaterialsService(database, data_dir)
    manual_workspace_service = ManualWorkspaceService(database, data_dir)
    manual_pipeline_service = ManualPipelineService(database)
    manual_pipeline_service.recover_interrupted_jobs()
    manual_research_service = ManualResearchService(database, data_dir)
    manual_drafting_service = ManualDraftingService(database, data_dir)
    manual_figure_service = ManualFigureService(database, data_dir)
    manual_screenshot_service = ManualScreenshotService(
        database, data_dir, capture_adapter_available=True
    )
    screenshot_evidence_service = ScreenshotEvidenceService(database, data_dir)
    screenshot_evidence_service.recover_interrupted()
    capture_adapter_service = ProjectCaptureAdapterService(database, data_dir)
    manual_document_service = ManualDocumentService(database, data_dir)
    manual_export_service = ManualExportService(database)
    manual_qa_service = ManualQaService(
        database, data_dir, documents=manual_document_service
    )
    manual_workflow_service = ManualWorkflowService(
        database, data_dir, documents=manual_document_service, qa=manual_qa_service
    )
    manual_ui_workflow_service = ManualUiWorkflowService(
        database, data_dir, evidence=screenshot_evidence_service,
        drafting=manual_drafting_service, documents=manual_document_service,
        qa=manual_qa_service,
    )
    quick_start_service = QuickStartService(
        database, scan=scan_service, inspection=inspection_service,
        confirmation=confirmation_service, source=source_materials_service,
        screenshots=screenshot_evidence_service, pipeline=manual_pipeline_service,
        workflow=manual_workflow_service, documents=manual_document_service,
        qa=manual_qa_service, settings=AppSettingsService(database),
    )
    quick_start_service.recover_interrupted()
    run_diagnostics_service = RunDiagnosticsService(database)
    auto_ui_lock = threading.Lock()
    auto_ui_pending = set()
    auto_ui_running = set()

    def schedule_auto_ui_update(task_id: str) -> None:
        """Debounce screenshot review changes into one durable chapter/document update."""
        with auto_ui_lock:
            auto_ui_pending.add(task_id)
            if task_id in auto_ui_running:
                return
            auto_ui_running.add(task_id)

        def worker() -> None:
            try:
                while True:
                    time.sleep(0.8)
                    jobs = manual_pipeline_service.list_for_task(task_id)
                    if not jobs:
                        return
                    job = jobs[0]
                    if job["status"] in {"queued", "running"}:
                        time.sleep(1.2)
                        continue
                    with auto_ui_lock:
                        auto_ui_pending.discard(task_id)
                    if screenshot_evidence_service.ui_update_required(job["id"]):
                        _run_manual_ui_update(manual_ui_workflow_service, job["id"])
                    with auto_ui_lock:
                        if task_id not in auto_ui_pending:
                            return
            finally:
                with auto_ui_lock:
                    auto_ui_running.discard(task_id)

        threading.Thread(target=worker, name="auto-ui-update-" + task_id[:8],
                         daemon=True).start()
    model_config_service = ModelConfigService(database)
    credential_vault = CredentialVault(database, data_dir)
    app_settings_service = AppSettingsService(database)
    api = DiagramAssetApi(service, session_token)
    app = FastAPI(title="Software Copyright Agent Sidecar", version=SIDECAR_VERSION,
                  docs_url=None, redoc_url=None)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_DESKTOP_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=[SESSION_HEADER, "Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        del request, error
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_request",
                               "message": "Request schema is invalid"}},
        )

    def bridge(method: str, path: str, body: object, token: Optional[str]):
        return _to_fastapi(api.handle(method, path, body, token))

    def authorized(token: Optional[str]) -> bool:
        return isinstance(token, str) and hmac.compare_digest(token, session_token)

    @app.get("/api/v1/health")
    def health(x_session_token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(x_session_token):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "unauthorized",
                                   "message": "Invalid session token"}},
            )
        return {"status": "ok", "version": SIDECAR_VERSION,
                "protocol_version": SIDECAR_PROTOCOL_VERSION}

    @app.post("/api/v1/projects/scan")
    def scan_project(payload: ScanProjectRequest,
                     token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            persisted = scan_service.execute(Path(payload.path))
            inspection = inspection_service.inspect(persisted.task_id)
        except (IngestionError, ScanError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "project_scan_error", "message": str(error)}
            })
        return {
            "task_id": persisted.task_id,
            "snapshot_id": persisted.snapshot_id,
            "summary": {
                "file_count": len(persisted.result.files),
                "ignored_count": persisted.result.ignored_count,
                "total_bytes": persisted.result.total_bytes,
                "secret_finding_count": len(persisted.result.secret_findings),
                "languages": sorted({item.language for item in persisted.result.files
                                     if item.language}),
            },
            "inspection": inspection,
        }

    @app.get("/api/v1/tasks")
    def recent_tasks(limit: int = 20,
                     token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return {"items": catalog_service.list_recent(limit)}
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "invalid_request", "message": str(error)}
            })

    @app.post("/api/v1/quick-start")
    def create_quick_start(
        payload: QuickStartRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return quick_start_service.create(payload.model_dump())
        except QuickStartError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "quick_start_error", "message": str(error)}
            })

    @app.get("/api/v1/quick-start")
    def list_quick_starts(
        limit: int = 20,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": quick_start_service.list(limit)}

    @app.get("/api/v1/quick-start/{run_id}")
    def get_quick_start(
        run_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return quick_start_service.get(run_id)
        except QuickStartError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "quick_start_not_found", "message": str(error)}
            })

    @app.post("/api/v1/quick-start/{run_id}/retry")
    def retry_quick_start(
        run_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return quick_start_service.retry(run_id)
        except QuickStartError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "quick_start_error", "message": str(error)}
            })

    @app.delete("/api/v1/quick-start/{run_id}")
    def discard_quick_start(
        run_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            quick_start_service.discard(run_id)
        except QuickStartError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "quick_start_error", "message": str(error)}
            })
        return Response(status_code=204)

    @app.get("/api/v1/run-diagnostics")
    def run_diagnostics(
        limit: int = 5,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return run_diagnostics_service.recent(limit)

    @app.get("/api/v1/model-configs")
    def model_configs(token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": model_config_service.list()}

    @app.put("/api/v1/model-credentials/{provider_id}")
    def store_model_credential(provider_id: str, payload: CredentialRequest,
                               token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            credential_vault.store(provider_id, payload.api_key)
            return {"stored": True}
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "credential_error", "message": str(error)}
            })

    @app.get("/api/v1/model-credentials/{provider_id}")
    def read_model_credential(provider_id: str,
                              token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return {"api_key": credential_vault.read(provider_id)}
        except ValueError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "credential_not_found", "message": str(error)}
            })

    @app.get("/api/v1/model-credentials/{provider_id}/status")
    def model_credential_status(provider_id: str,
                                token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return {"available": credential_vault.has(provider_id)}
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "credential_error", "message": str(error)}
            })

    @app.delete("/api/v1/model-credentials/{provider_id}")
    def delete_stored_model_credential(provider_id: str,
                                       token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        credential_vault.delete(provider_id)
        return Response(status_code=204)

    @app.post("/api/v1/model-configs")
    def save_model_config(payload: ModelConfigRequest,
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return model_config_service.upsert(ModelConfigInput(**payload.model_dump()))
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "model_config_error", "message": str(error)}
            })

    @app.post("/api/v1/model-configs/{config_id}/endpoint-mode")
    def save_model_endpoint_mode(config_id: str, payload: EndpointModeRequest,
                                 token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return model_config_service.set_endpoint_mode(config_id, payload.endpoint_mode)
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "model_config_error", "message": str(error)}
            })

    @app.post("/api/v1/model-configs/{config_id}/vision-capability")
    def save_model_vision_capability(
        config_id: str,
        payload: VisionCapabilityRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            if payload.supports_vision:
                verification = screenshot_evidence_service.verify_vision_capability(config_id)
                result = model_config_service.list()
                updated = next(item for item in result if item["id"] == config_id)
                return {**updated, "vision_verification": verification}
            return model_config_service.set_vision_capability(config_id, False)
        except (ValueError, ScreenshotEvidenceError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "model_config_error", "message": str(error)}
            })

    @app.post("/api/v1/model-configs/{config_id}/verified")
    def verify_model_config(config_id: str,
                            token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return model_config_service.mark_verified(config_id)
        except ValueError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "model_config_not_found", "message": str(error)}
            })

    @app.delete("/api/v1/model-configs/{config_id}")
    def delete_model_config(config_id: str,
                            token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            model_config_service.delete(config_id)
        except ValueError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "model_config_not_found", "message": str(error)}
            })
        return Response(status_code=204)

    @app.get("/api/v1/settings")
    def app_settings(token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return app_settings_service.get()

    @app.post("/api/v1/settings")
    def save_app_settings(payload: AppSettingsRequest,
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return app_settings_service.save(payload.model_dump())
        except ValueError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "settings_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/rescan")
    def rescan_project(task_id: str,
                       token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        database.initialize()
        with database.connect() as connection:
            source = connection.execute(
                """SELECT ps.original_path FROM tasks t
                JOIN project_sources ps ON ps.id = t.source_id WHERE t.id = ?""",
                (task_id,),
            ).fetchone()
            confirmed_rows = connection.execute(
                """SELECT fact_key, value_json FROM facts WHERE task_id = ?
                AND status = 'confirmed' ORDER BY created_at DESC""",
                (task_id,),
            ).fetchall()
        if source is None:
            return JSONResponse(status_code=404, content={
                "error": {"code": "task_not_found", "message": "Task source not found"}
            })
        try:
            persisted = scan_service.execute(Path(source["original_path"]))
            inspection = inspection_service.inspect(persisted.task_id)
            confirmed = {}
            for row in confirmed_rows:
                confirmed.setdefault(row["fact_key"], json.loads(row["value_json"]))
            pending_keys = {
                item["field_key"] for item in inspection["confirmations"]
                if item["status"] == "pending"
            }
            for field_key in sorted(pending_keys & confirmed.keys()):
                value = confirmed[field_key]
                if isinstance(value, (str, int, float, bool)):
                    confirmation_service.answer(
                        persisted.task_id, field_key, str(value)
                    )
            inspection = inspection_service.inspect(persisted.task_id)
        except (IngestionError, ScanError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "project_rescan_error", "message": str(error)}
            })
        return {
            "task_id": persisted.task_id,
            "snapshot_id": persisted.snapshot_id,
            "summary": {
                "file_count": len(persisted.result.files),
                "ignored_count": persisted.result.ignored_count,
                "total_bytes": persisted.result.total_bytes,
                "secret_finding_count": len(persisted.result.secret_findings),
                "languages": sorted({item.language for item in persisted.result.files
                                     if item.language}),
            },
            "inspection": inspection,
        }

    @app.get("/api/v1/tasks/{task_id}/inspection")
    def inspection(task_id: str,
                   token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return inspection_service.inspect(task_id)
        except InspectionError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "task_not_found", "message": str(error)}
            })

    @app.delete("/api/v1/tasks/{task_id}")
    def delete_task(task_id: str,
                    token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            catalog_service.delete_task(task_id)
        except ValueError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "task_not_found", "message": str(error)}
            })
        return Response(status_code=204)

    @app.post("/api/v1/tasks/{task_id}/confirmations/{field_key}")
    def answer_confirmation(task_id: str, field_key: str,
                            payload: ConfirmationAnswerRequest,
                            token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            answered = confirmation_service.answer(task_id, field_key, payload.value)
            refreshed = inspection_service.inspect(task_id)
        except ConfirmationError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "confirmation_error", "message": str(error)}
            })
        return {
            "field_key": answered.field_key,
            "remaining_required": answered.remaining_required,
            "task_status": answered.task_status.value,
            "inspection": refreshed,
        }

    def source_material_response(action, task_id: str, token: Optional[str]):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return action(task_id)
        except SourceMaterialsError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "task_not_found", "message": str(error)}
            })
        except (SourcePlanError, CodePreviewError, SourceDocumentError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "source_material_error", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/source-materials")
    def source_materials(task_id: str,
                         token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(source_materials_service.snapshot, task_id, token)

    @app.post("/api/v1/tasks/{task_id}/source-materials/source-plan")
    def build_source_plan(task_id: str,
                          strategy: Literal["standard", "relaxed", "maximum"] = "standard",
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            lambda value: source_materials_service.build_source_plan(value, strategy),
            task_id, token
        )

    @app.get("/api/v1/tasks/{task_id}/source-materials/code-preview/pages")
    def source_preview_pages(task_id: str,
                             all_pages: bool = False,
                             token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            lambda value: source_materials_service.preview_pages(value, all_pages),
            task_id, token
        )

    @app.post("/api/v1/tasks/{task_id}/source-materials/code-preview")
    def build_code_preview(task_id: str,
                           token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            source_materials_service.build_code_preview, task_id, token
        )

    @app.post("/api/v1/tasks/{task_id}/source-materials/source-docx")
    def build_source_docx(task_id: str,
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            source_materials_service.build_source_document, task_id, token
        )

    @app.get("/api/v1/tasks/{task_id}/source-materials/source-docx/preview")
    def source_document_preview(
        task_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        return source_material_response(
            source_materials_service.source_document_preview, task_id, token
        )

    @app.get("/api/v1/tasks/{task_id}/source-materials/source-docx/qa-capability")
    def source_document_qa_capability(
        task_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        return source_material_response(
            source_materials_service.source_document_qa_capability, task_id, token
        )

    @app.post("/api/v1/tasks/{task_id}/source-materials/source-docx/qa")
    def run_source_document_qa(
        task_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return source_materials_service.run_source_document_qa(task_id)
        except (SourceMaterialsError, SourceDocumentQaError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "source_document_qa_error", "message": str(error)}
            })

    @app.get(
        "/api/v1/tasks/{task_id}/source-materials/source-docx/preview/pages/{page_number}.png"
    )
    def source_document_preview_page(
        task_id: str,
        page_number: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return Response(
                content=source_materials_service.read_source_document_preview_page(
                    task_id, page_number
                ),
                media_type="image/png",
            )
        except SourceMaterialsError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "source_document_preview_not_found", "message": str(error)}
            })

    def manual_response(action, task_id: str, token: Optional[str]):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return action(task_id)
        except ManualWorkspaceError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_workspace_unavailable", "message": str(error)}
            })
        except (ManualPlanError, DiagramPlanError, DrawioGenerationError,
                ManualGenerationError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_workflow_error", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/manual-workspace")
    def manual_workspace(task_id: str,
                         token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return manual_response(manual_workspace_service.snapshot, task_id, token)

    @app.post("/api/v1/tasks/{task_id}/manual-workspace/manual-plan")
    def build_manual_plan(task_id: str,
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return manual_response(manual_workspace_service.build_manual_plan, task_id, token)

    @app.post("/api/v1/tasks/{task_id}/manual-workspace/diagram-plan")
    def build_diagram_plan(task_id: str,
                           token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return manual_response(manual_workspace_service.build_diagram_plan, task_id, token)

    @app.post("/api/v1/tasks/{task_id}/manual-workspace/diagram-artifacts")
    def build_diagram_artifacts(task_id: str,
                                token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return manual_response(manual_workspace_service.build_diagrams, task_id, token)

    @app.post("/api/v1/tasks/{task_id}/manual-workspace/generate")
    def generate_manual(task_id: str, payload: ManualGenerationRequest,
                        token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return manual_response(
            lambda value: manual_workspace_service.generate_manual(value, payload.model_config_id),
            task_id, token,
        )

    @app.post("/api/v1/tasks/{task_id}/manual-jobs")
    def create_manual_job(task_id: str, payload: ManualGenerationRequest,
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_pipeline_service.create(task_id, payload.model_config_id)
        except ManualPipelineError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_pipeline_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/manual-jobs/generate")
    def generate_formal_manual(
        task_id: str,
        payload: ManualGenerationRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            job = manual_pipeline_service.create(task_id, payload.model_config_id)
            worker = threading.Thread(
                target=_run_manual_job,
                args=(manual_workflow_service, job["id"]),
                name="manual-job-{0}".format(job["id"][:8]),
                daemon=True,
            )
            worker.start()
            return job
        except (ManualPipelineError, ManualWorkflowError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_workflow_error", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/manual-jobs")
    def list_manual_jobs(task_id: str,
                         token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_pipeline_service.list_for_task(task_id)}

    @app.get("/api/v1/manual-jobs/{job_id}")
    def get_manual_job(job_id: str,
                       token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_pipeline_service.get(job_id)
        except ManualPipelineError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_pipeline_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/research")
    def execute_manual_research(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_research_service.execute(job_id)
        except ManualResearchError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_research_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/research")
    def get_manual_research(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            result = manual_research_service.latest(job_id)
        except ManualResearchError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_research_not_found", "message": str(error)}
            })
        if result is None:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_research_not_found",
                          "message": "该说明书任务尚无项目研究结果"}
            })
        return result

    @app.post("/api/v1/manual-jobs/{job_id}/draft")
    def generate_manual_draft(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_drafting_service.generate_all(job_id)
        except ManualDraftingError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_drafting_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/sections")
    def list_manual_sections(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_drafting_service.list_sections(job_id)}

    @app.post("/api/v1/manual-jobs/{job_id}/sections/{section_key}/regenerate")
    def regenerate_manual_section(
        job_id: str,
        section_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_drafting_service.regenerate(job_id, section_key)
        except ManualDraftingError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_drafting_error", "message": str(error)}
            })

    @app.put("/api/v1/manual-jobs/{job_id}/sections/{section_key}")
    def edit_manual_section(
        job_id: str,
        section_key: str,
        payload: ManualSectionEditRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_drafting_service.save_edit(
                job_id, section_key, payload.title, payload.blocks
            )
        except ManualDraftingError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_drafting_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/sections/{section_key}/revisions")
    def list_manual_section_revisions(
        job_id: str,
        section_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_drafting_service.revisions(job_id, section_key)}

    @app.post("/api/v1/manual-jobs/{job_id}/figures")
    def generate_manual_figures(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.generate_all(job_id)
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/figures")
    def list_manual_figures(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return {"items": manual_figure_service.list(job_id)}
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/regenerate")
    def regenerate_manual_figure(
        job_id: str,
        figure_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.regenerate(job_id, figure_key)
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/revisions")
    def list_manual_figure_revisions(
        job_id: str,
        figure_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_figure_service.revisions(job_id, figure_key)}

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/revisions")
    def create_manual_figure_revision(
        job_id: str,
        figure_key: str,
        payload: SaveRevisionRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.create_revision(
                job_id, figure_key, [item.model_dump() for item in payload.operations],
                payload.edit_source,
            )
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/editor-revision")
    def save_manual_figure_editor_revision(
        job_id: str,
        figure_key: str,
        payload: ManualFigureEditorRevisionRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.save_editor_revision(
                job_id, figure_key, payload.xml, payload.svg, payload.png
            )
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/ai-preview")
    def preview_manual_figure_ai_edit(
        job_id: str,
        figure_key: str,
        payload: ManualFigureAiPreviewRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.ai_preview(job_id, figure_key, payload.instruction)
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/ai-patch")
    def patch_manual_figure_with_ai(
        job_id: str,
        figure_key: str,
        payload: ManualFigureAiPatchRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.ai_patch_editor_xml(
                job_id, figure_key, payload.instruction, payload.xml,
                payload.model_config_id,
            )
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/ai-patch-stream")
    def stream_manual_figure_ai_patch(
        job_id: str,
        figure_key: str,
        payload: ManualFigureAiPatchRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })

        def event_stream():
            events = queue.Queue()
            finished = object()

            def run() -> None:
                try:
                    events.put({"type": "phase", "phase": "queued",
                                "message": "已连接模型，正在准备当前 XML"})
                    result = manual_figure_service.ai_patch_editor_xml(
                        job_id, figure_key, payload.instruction, payload.xml,
                        payload.model_config_id, events.put,
                    )
                    events.put({"type": "result", "result": result})
                except ManualFigureError as error:
                    events.put({"type": "error", "message": str(error)})
                except Exception:
                    events.put({"type": "error", "message": "AI 图表流式修改失败"})
                finally:
                    events.put(finished)

            threading.Thread(target=run, daemon=True).start()
            while True:
                try:
                    event = events.get(timeout=1.0)
                except queue.Empty:
                    yield json.dumps({"type": "heartbeat"}, separators=(",", ":")) + "\n"
                    continue
                if event is finished:
                    break
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"

        return StreamingResponse(
            event_stream(), media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/manual-jobs/{job_id}/figures/{figure_key}/rollback")
    def rollback_manual_figure(
        job_id: str,
        figure_key: str,
        payload: RollbackRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_figure_service.rollback(job_id, figure_key, payload.version)
        except ManualFigureError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_figure_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/figures/{figure_key}.{asset_format}")
    def get_manual_figure_asset(
        job_id: str,
        figure_key: str,
        asset_format: Literal["drawio", "svg", "png"],
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            body, media_type = manual_figure_service.read_asset(
                job_id, figure_key, asset_format
            )
            return Response(content=body, media_type=media_type)
        except ManualFigureError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_figure_not_found", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/screenshots/workspace")
    def get_screenshot_evidence_workspace(
        task_id: str,
        include_archived: bool = False,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            profile = screenshot_evidence_service.prepare_profile(task_id)
            with database.connect() as connection:
                batches = connection.execute(
                    """SELECT * FROM manual_screenshot_import_batches WHERE task_id=?
                    ORDER BY created_at DESC LIMIT 30""", (task_id,),
                ).fetchall()
            return {
                "profile": profile,
                "assets": screenshot_evidence_service.list_assets(task_id, include_archived),
                "vision_models": screenshot_evidence_service.list_vision_models(),
                "batches": [{**dict(row), "summary": json.loads(row["summary_json"] or "{}")}
                            for row in batches],
                "ui_evidence_decision": screenshot_evidence_service.get_ui_decision(task_id),
                "privacy_notice": "截图将发送给所选视觉模型供应商。请先检查账号、手机号、密钥、客户数据等敏感信息。",
            }
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_evidence_error", "message": str(error)}
            })

    @app.put("/api/v1/tasks/{task_id}/screenshots/profile")
    def save_screenshot_project_profile(
        task_id: str,
        payload: ProjectProfileRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.save_profile(task_id, payload.profile)
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_evidence_error", "message": str(error)}
            })

    @app.put("/api/v1/tasks/{task_id}/screenshots/ui-evidence-decision")
    def save_ui_evidence_decision(
        task_id: str,
        payload: UiEvidenceDecisionRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.set_ui_decision(
                task_id, payload.decision, payload.reason
            )
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "ui_evidence_decision_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/import-batch")
    def import_screenshot_evidence_batch(
        task_id: str,
        payload: ScreenshotBatchImportRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.import_batch(
                task_id, payload.paths, payload.source, payload.job_id
            )
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_import_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/import-folder")
    def import_screenshot_evidence_folder(
        task_id: str,
        payload: ScreenshotFolderImportRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.import_folder(
                task_id, Path(payload.path), payload.recursive, payload.job_id
            )
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_import_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/clipboard")
    def import_screenshot_evidence_clipboard(
        task_id: str,
        payload: ScreenshotClipboardImportRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.import_base64(
                task_id, payload.data_base64, payload.filename, payload.job_id
            )
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_import_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/analyze")
    def analyze_screenshot_evidence(
        task_id: str,
        payload: ScreenshotAnalyzeRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            capability = screenshot_evidence_service.model_capability(payload.model_config_id)
            if capability["status"] != "supported":
                raise ScreenshotEvidenceError(capability["message"])
            known = {item["id"] for item in screenshot_evidence_service.list_assets(task_id)}
            asset_ids = [value for value in payload.asset_ids if value in known]
            if not asset_ids:
                raise ScreenshotEvidenceError("没有可分析的截图")
            queued_ids = []
            with database.connect() as connection:
                for asset_id in asset_ids:
                    changed = connection.execute(
                        """UPDATE manual_project_screenshot_assets
                        SET analysis_status='queued',failure_reason=NULL,updated_at=?
                        WHERE id=? AND task_id=?
                          AND analysis_status NOT IN ('queued','running')""",
                        (utc_now(), asset_id, task_id),
                    ).rowcount
                    if changed:
                        queued_ids.append(asset_id)
            if not queued_ids:
                raise ScreenshotEvidenceError("所选截图正在分析，请勿重复启动")
            threading.Thread(
                target=screenshot_evidence_service.analyze_many,
                args=(task_id, queued_ids, payload.model_config_id, payload.job_id),
                name="screenshot-analysis-" + task_id[:8], daemon=True,
            ).start()
            return {"status": "queued", "asset_ids": queued_ids,
                    "model": capability, "privacy_notice_shown": True}
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_analysis_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/retry-analysis")
    def retry_screenshot_analysis_node(
        job_id: str,
        payload: ScreenshotNodeRetryRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        prefix = "screenshot-analysis:"
        if not payload.node_key.startswith(prefix):
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_analysis_error",
                          "message": "该节点不是可重试的单张截图解读节点"}
            })
        try:
            job = manual_pipeline_service.get(job_id)
            node = next((item for item in job["nodes"]
                         if item["key"] == payload.node_key), None)
            if node is None or node["kind"] != "screenshot_analysis":
                raise ScreenshotEvidenceError("截图解读节点不存在")
            asset_id = payload.node_key[len(prefix):]
            model_config_id = str(node.get("model_config_id") or "")
            capability = screenshot_evidence_service.model_capability(model_config_id)
            if capability["status"] != "supported":
                raise ScreenshotEvidenceError(capability["message"])
            assets = {item["id"]: item for item in screenshot_evidence_service.list_assets(
                job["task_id"])}
            if asset_id not in assets:
                raise ScreenshotEvidenceError("节点对应的截图不存在")
            with database.connect() as connection:
                changed = connection.execute(
                    """UPDATE manual_project_screenshot_assets SET analysis_status='queued',
                    failure_reason=NULL,updated_at=? WHERE id=? AND task_id=?
                    AND analysis_status NOT IN ('queued','running')""",
                    (utc_now(), asset_id, job["task_id"]),
                ).rowcount
            if not changed:
                raise ScreenshotEvidenceError("该截图正在分析，请勿重复重试")
            threading.Thread(
                target=screenshot_evidence_service.analyze_many,
                args=(job["task_id"], [asset_id], model_config_id, job_id),
                name="screenshot-retry-" + asset_id[:8], daemon=True,
            ).start()
            return {"status": "queued", "asset_id": asset_id,
                    "model_config_id": model_config_id}
        except (ScreenshotEvidenceError, ManualPipelineError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_analysis_error", "message": str(error)}
            })

    @app.put("/api/v1/tasks/{task_id}/screenshots/{asset_id}/review")
    def review_screenshot_evidence(
        task_id: str,
        asset_id: str,
        payload: ScreenshotReviewRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            result = screenshot_evidence_service.review(
                task_id, asset_id, payload.interpretation, adopted=payload.adopted,
                group_title=payload.group_title, sort_order=payload.sort_order,
                sensitive_status=payload.sensitive_status,
            )
            schedule_auto_ui_update(task_id)
            return result
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_review_error", "message": str(error)}
            })

    @app.put("/api/v1/tasks/{task_id}/screenshots/{asset_id}/image")
    def replace_screenshot_evidence_image(
        task_id: str,
        asset_id: str,
        payload: ScreenshotReplaceRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.replace_image(task_id, asset_id, Path(payload.path))
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_replace_error", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/screenshots/{asset_id}/history")
    def get_screenshot_evidence_history(
        task_id: str,
        asset_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.history(task_id, asset_id)
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_history_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/{asset_id}/rollback")
    def rollback_screenshot_evidence(
        task_id: str,
        asset_id: str,
        payload: ScreenshotRollbackRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return screenshot_evidence_service.rollback(
                task_id, asset_id, image_version=payload.image_version,
                interpretation_version=payload.interpretation_version,
            )
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_rollback_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/adoption")
    def set_screenshot_evidence_adoption(
        task_id: str,
        payload: ScreenshotAdoptionRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            items = screenshot_evidence_service.set_adoption(
                task_id, payload.asset_ids, payload.adopted
            )
            schedule_auto_ui_update(task_id)
            return {"items": items}
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_adoption_error", "message": str(error)}
            })

    @app.post("/api/v1/tasks/{task_id}/screenshots/adoption-status")
    def set_screenshot_evidence_adoption_status(
        task_id: str,
        payload: ScreenshotAdoptionStatusRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            items = screenshot_evidence_service.set_adoption_status(
                task_id, payload.asset_ids, payload.status
            )
            schedule_auto_ui_update(task_id)
            return {"items": items}
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "screenshot_adoption_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/confirm-and-update")
    def confirm_screenshots_and_update_manual(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            job = manual_pipeline_service.get(job_id)
            assets = screenshot_evidence_service.list_assets(job["task_id"])
            adopted = [item for item in assets if item["adoption_status"] == "adopted"]
            if not adopted:
                raise ScreenshotEvidenceError("至少审核并确认采用一张真实截图")
            active = next((item for item in job["nodes"]
                           if item["key"] in {"ui_section_update", "ui_document_reassemble",
                                              "ui_screenshot_qa"}
                           and item["status"] in {"queued", "running"}), None)
            if active:
                return {"status": "queued", "job_id": job_id,
                        "screenshot_count": len(adopted),
                        "message": "第 7 章更新已在执行：" + active["title"]}
            schedule_auto_ui_update(job["task_id"])
            return {"status": "queued", "job_id": job_id,
                    "screenshot_count": len(adopted),
                    "message": "已开始保存采用集、更新第 7 章、装配新候选稿并执行一致性 QA"}
        except (ScreenshotEvidenceError, ManualPipelineError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_ui_update_error", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/screenshots/{asset_id}.png")
    def get_screenshot_evidence_image(
        task_id: str,
        asset_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return Response(content=screenshot_evidence_service.read_image(task_id, asset_id),
                            media_type="image/png")
        except ScreenshotEvidenceError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "screenshot_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/assessment")
    def assess_manual_screenshots(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.assess(job_id)
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots/assessment")
    def get_manual_screenshot_assessment(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        result = manual_screenshot_service.latest_assessment(job_id)
        if result is None:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_screenshot_assessment_not_found",
                          "message": "尚未执行截图安全评估"}
            })
        return result

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots/launch-plan")
    def get_manual_screenshot_launch_plan(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return capture_adapter_service.launch_plan(job_id)
        except CaptureAdapterError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "capture_adapter_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots/launch-plan/{candidate_id}")
    def get_manual_screenshot_launch_candidate(
        job_id: str,
        candidate_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return capture_adapter_service.candidate(job_id, candidate_id)
        except CaptureAdapterError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "capture_candidate_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/import")
    def import_manual_screenshot(
        job_id: str,
        payload: ScreenshotImportRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.import_image(
                job_id, Path(payload.path), payload.section_key, payload.title,
                payload.description.model_dump(mode="json"), payload.source,
            )
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/finalize")
    def finalize_manual_screenshots(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.finalize(job_id)
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots")
    def list_manual_screenshots(
        job_id: str,
        include_archived: bool = False,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_screenshot_service.list(job_id, include_archived)}

    @app.put("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}")
    def edit_manual_screenshot(
        job_id: str,
        screenshot_key: str,
        payload: ScreenshotEditRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.update_metadata(
                job_id, screenshot_key, payload.section_key, payload.title,
                payload.description.model_dump(mode="json"),
            )
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}/replace")
    def replace_manual_screenshot(
        job_id: str,
        screenshot_key: str,
        payload: ScreenshotReplaceRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.replace_image(
                job_id, screenshot_key, Path(payload.path)
            )
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}/revisions")
    def list_manual_screenshot_revisions(
        job_id: str,
        screenshot_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_screenshot_service.revisions(job_id, screenshot_key)}

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}/rollback")
    def rollback_manual_screenshot(
        job_id: str,
        screenshot_key: str,
        payload: RollbackRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.rollback(
                job_id, screenshot_key, payload.version
            )
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}/archive")
    def archive_manual_screenshot(
        job_id: str,
        screenshot_key: str,
        payload: ScreenshotArchiveRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_screenshot_service.set_archived(
                job_id, screenshot_key, payload.archived
            )
        except ManualScreenshotError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_screenshot_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/screenshots/{screenshot_key}.png")
    def get_manual_screenshot(
        job_id: str,
        screenshot_key: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return Response(content=manual_screenshot_service.read_image(
                job_id, screenshot_key), media_type="image/png")
        except ManualScreenshotError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_screenshot_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/documents")
    def assemble_manual_document(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_document_service.assemble(job_id)
        except ManualDocumentError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_document_error", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/documents/{version}/finalize")
    def finalize_manual_document(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            document = manual_document_service.finalize(job_id, version)
            return manual_qa_service.execute(job_id, document["version"])
        except (ManualDocumentError, ManualQaError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_document_finalize_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents")
    def list_manual_documents(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return {"items": manual_document_service.list(job_id)}
        except ManualDocumentError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_document_not_found", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}")
    def get_manual_document(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_document_service.get(job_id, version)
        except ManualDocumentError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_document_not_found", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}/preview")
    def preview_manual_document(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_document_service.preview(job_id, version)
        except ManualDocumentError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_document_not_found", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}/download")
    def download_manual_document(
        job_id: str,
        version: int,
        review: bool = False,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            item = manual_document_service.get(job_id, version)
            if not review and item["document_kind"] != "final_document":
                return JSONResponse(status_code=409, content={
                    "error": {"code": "manual_document_not_final",
                              "message": "审阅稿不能作为终稿导出，请先由人工点击生成终稿"}
                })
            if not review and item["freshness"]["status"] != "current":
                return JSONResponse(status_code=409, content={
                    "error": {"code": "manual_document_outdated",
                              "message": "正文、图表或截图已有更新，请重新装配并质检后导出"}
                })
            return Response(
                content=manual_document_service.read(job_id, version),
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''{0}".format(
                        quote(item["filename"])
                    ),
                    "X-Artifact-SHA256": item["sha256"],
                },
            )
        except ManualDocumentError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_document_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/exports")
    def record_manual_export(
        job_id: str,
        payload: ManualExportRecordRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_export_service.record(
                job_id, payload.document_version, payload.export_kind,
                payload.destination_path, payload.size_bytes, payload.sha256,
            )
        except ManualExportError as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_export_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/exports")
    def list_manual_exports(
        job_id: str,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_export_service.list(job_id)}

    @app.post("/api/v1/manual-jobs/{job_id}/documents/{version}/qa")
    def run_manual_document_qa(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_qa_service.execute(job_id, version)
        except (ManualQaError, ManualDocumentError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_qa_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}/qa")
    def get_manual_document_qa(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_qa_service.get(job_id, version)
        except (ManualQaError, ManualDocumentError) as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_qa_not_found", "message": str(error)}
            })

    @app.post("/api/v1/manual-jobs/{job_id}/documents/{version}/qa/decisions")
    def defer_manual_document_qa_check(
        job_id: str,
        version: int,
        payload: ManualQaDecisionRequest,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return manual_qa_service.defer_check(
                job_id, version, payload.check_key, payload.reason
            )
        except (ManualQaError, ManualDocumentError) as error:
            return JSONResponse(status_code=400, content={
                "error": {"code": "manual_qa_decision_error", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}/qa/pages/{page_number}.png")
    def get_manual_document_qa_page(
        job_id: str,
        version: int,
        page_number: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return Response(
                content=manual_qa_service.read_page(job_id, version, page_number),
                media_type="image/png",
            )
        except (ManualQaError, ManualDocumentError) as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_qa_page_not_found", "message": str(error)}
            })

    @app.get("/api/v1/manual-jobs/{job_id}/documents/{version}/qa/preview.pdf")
    def get_manual_document_qa_pdf(
        job_id: str,
        version: int,
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            return Response(
                content=manual_qa_service.read_pdf(job_id, version),
                media_type="application/pdf",
            )
        except (ManualQaError, ManualDocumentError) as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_qa_pdf_not_found", "message": str(error)}
            })

    @app.get("/api/v1/tasks/{task_id}/diagram-assets")
    def workspace(task_id: str, request: Request,
                  token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("GET", request.url.path, None, token)

    @app.get("/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions")
    def revisions(task_id: str, diagram_key: str, request: Request,
                  token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("GET", request.url.path, None, token)

    @app.post("/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/revisions")
    def save_revision(task_id: str, diagram_key: str, payload: SaveRevisionRequest,
                      request: Request,
                      token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("POST", request.url.path, payload.model_dump(mode="json"), token)

    @app.post("/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rebase")
    def rebase(task_id: str, diagram_key: str, request: Request,
               token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("POST", request.url.path, {}, token)

    @app.post("/api/v1/tasks/{task_id}/diagram-assets/{diagram_key}/rollback")
    def rollback(task_id: str, diagram_key: str, payload: RollbackRequest,
                 request: Request,
                 token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("POST", request.url.path, payload.model_dump(mode="json"), token)

    @app.get("/api/v1/diagram-revisions/{revision_id}")
    def revision(revision_id: str, request: Request,
                 token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("GET", request.url.path, None, token)

    @app.post("/api/v1/diagram-revisions/{revision_id}/resolve")
    def resolve(revision_id: str, payload: ResolveRevisionRequest, request: Request,
                token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("POST", request.url.path, payload.model_dump(mode="json"), token)

    @app.get("/api/v1/diagram-revisions/{revision_id}/preview.svg")
    def preview(revision_id: str, request: Request,
                token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return bridge("GET", request.url.path, None, token)

    return app


def _to_fastapi(response: ApiResponse):
    if response.content_type == "image/svg+xml":
        return Response(content=response.body, status_code=response.status,
                        media_type="image/svg+xml")
    return JSONResponse(status_code=response.status, content=response.body)


def serve(data_dir: Path, session_token: str) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    handshake = {"event": "sidecar.ready", "protocol_version": SIDECAR_PROTOCOL_VERSION,
                 "version": SIDECAR_VERSION, "host": "127.0.0.1", "port": port,
                 "pid": os.getpid()}
    print(json.dumps(handshake, ensure_ascii=False, sort_keys=True), flush=True)
    config = uvicorn.Config(create_app(data_dir, session_token), host="127.0.0.1",
                            port=port, log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="copyright-agent-sidecar")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("COPYRIGHT_AGENT_SESSION_TOKEN")
    if not token or len(token) < 32:
        print("COPYRIGHT_AGENT_SESSION_TOKEN must contain at least 32 characters",
              file=sys.stderr)
        return 2
    parent_pid = _parent_pid_from_environment()
    if parent_pid is not None:
        threading.Thread(
            target=_monitor_parent_process,
            args=(parent_pid,),
            name="desktop-parent-monitor",
            daemon=True,
        ).start()
    try:
        serve(args.data_dir.expanduser().resolve(), token)
    except KeyboardInterrupt:
        return 0
    return 0


def _parent_pid_from_environment() -> Optional[int]:
    raw = os.environ.get("COPYRIGHT_AGENT_PARENT_PID", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 1 else None


def _parent_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 1:
        return False
    if sys.platform == "win32":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _monitor_parent_process(parent_pid: int, interval_seconds: float = 1.0) -> None:
    while _parent_is_alive(parent_pid):
        time.sleep(interval_seconds)
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
