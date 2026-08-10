import argparse
import hmac
import json
import os
import socket
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .diagram_asset_service import DiagramAssetService
from .confirmation import ConfirmationError, ConfirmationService
from .ingestion import IngestionError
from .inspection import InspectionError, InspectionService
from .local_api import ApiResponse, DiagramAssetApi
from .scanner import ScanError
from .project_catalog import ProjectCatalogService
from .service import ScanProjectService
from .source_materials import SourceMaterialsError, SourceMaterialsService
from .source_plan_service import SourcePlanError
from .code_preview import CodePreviewError
from .source_document import SourceDocumentError
from .manual_workspace import ManualWorkspaceError, ManualWorkspaceService
from .manual_plan_service import ManualPlanError
from .manual_generation import ManualGenerationError
from .manual_pipeline import ManualPipelineError, ManualPipelineService
from .manual_research import ManualResearchError, ManualResearchService
from .manual_drafting import ManualDraftingError, ManualDraftingService
from .manual_figures import ManualFigureError, ManualFigureService
from .manual_screenshots import ManualScreenshotError, ManualScreenshotService
from .manual_document import ManualDocumentError, ManualDocumentService
from .manual_qa import ManualQaError, ManualQaService
from .manual_workflow import ManualWorkflowError, ManualWorkflowService
from .diagram_plan_service import DiagramPlanError
from .drawio_service import DrawioGenerationError
from .storage import Database
from .model_config import ModelConfigInput, ModelConfigService
from .credential_vault import CredentialVault
from .app_settings import AppSettingsService


SIDECAR_PROTOCOL_VERSION = 1
SIDECAR_VERSION = "0.1.0"
MAX_REQUEST_BYTES = 1024 * 1024
SESSION_HEADER = "X-Session-Token"
ALLOWED_DESKTOP_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "http://127.0.0.1:1420",
]


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


class RollbackRequest(StrictModel):
    version: int = Field(ge=1)


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


class CredentialRequest(StrictModel):
    api_key: str = Field(min_length=8, max_length=8192)


class EndpointModeRequest(StrictModel):
    endpoint_mode: Literal["messages", "chat_completions", "responses", "ollama_chat"]


class ManualGenerationRequest(StrictModel):
    model_config_id: str = Field(min_length=8, max_length=64)


class ManualSectionEditRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    blocks: List[Dict[str, Any]] = Field(min_length=3, max_length=100)


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


class AppSettingsRequest(StrictModel):
    manual_model_id: Optional[str] = Field(default=None, max_length=64)
    diagram_model_id: Optional[str] = Field(default=None, max_length=64)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1024, le=32768)
    source_strategy: Literal["standard", "relaxed", "maximum"]
    auto_preview: bool


class RequestSizeLimitMiddleware:
    def __init__(self, app, maximum_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    too_large = int(content_length) > self.maximum_bytes
                except ValueError:
                    too_large = True
                if too_large:
                    response = JSONResponse(
                        status_code=413,
                        content={"error": {"code": "request_too_large",
                                           "message": "Request body exceeds 1 MiB"}},
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
    manual_research_service = ManualResearchService(database, data_dir)
    manual_drafting_service = ManualDraftingService(database, data_dir)
    manual_figure_service = ManualFigureService(database, data_dir)
    manual_screenshot_service = ManualScreenshotService(database, data_dir)
    manual_document_service = ManualDocumentService(database, data_dir)
    manual_qa_service = ManualQaService(
        database, data_dir, documents=manual_document_service
    )
    manual_workflow_service = ManualWorkflowService(
        database, data_dir, documents=manual_document_service, qa=manual_qa_service
    )
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
            return manual_workflow_service.generate(task_id, payload.model_config_id)
        except ManualWorkflowError as error:
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
        return {"items": manual_figure_service.list(job_id)}

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
                payload.description.model_dump(mode="json"),
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
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        return {"items": manual_screenshot_service.list(job_id)}

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
        token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    ):
        if not authorized(token):
            return JSONResponse(status_code=401, content={
                "error": {"code": "unauthorized", "message": "Invalid session token"}
            })
        try:
            item = manual_document_service.get(job_id, version)
            if item["status"] != "qa_passed":
                return JSONResponse(status_code=409, content={
                    "error": {"code": "manual_document_not_deliverable",
                              "message": "说明书尚未通过逐页质量检查，暂不能导出"}
                })
            return Response(
                content=manual_document_service.read(job_id, version),
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": "attachment; filename*=UTF-8''{0}".format(
                    quote(item["filename"])
                )},
            )
        except ManualDocumentError as error:
            return JSONResponse(status_code=404, content={
                "error": {"code": "manual_document_not_found", "message": str(error)}
            })

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
    try:
        serve(args.data_dir.expanduser().resolve(), token)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
