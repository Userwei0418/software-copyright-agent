import argparse
import hmac
import json
import os
import socket
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

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
from .diagram_plan_service import DiagramPlanError
from .drawio_service import DrawioGenerationError
from .storage import Database


SIDECAR_PROTOCOL_VERSION = 1
SIDECAR_VERSION = "0.1.0"
MAX_REQUEST_BYTES = 1024 * 1024
SESSION_HEADER = "X-Session-Token"


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
    catalog_service = ProjectCatalogService(database)
    source_materials_service = SourceMaterialsService(database, data_dir)
    manual_workspace_service = ManualWorkspaceService(database, data_dir)
    api = DiagramAssetApi(service, session_token)
    app = FastAPI(title="Software Copyright Agent Sidecar", version=SIDECAR_VERSION,
                  docs_url=None, redoc_url=None)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
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
                          token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            source_materials_service.build_source_plan, task_id, token
        )

    @app.get("/api/v1/tasks/{task_id}/source-materials/code-preview/pages")
    def source_preview_pages(task_id: str,
                             token: Optional[str] = Header(default=None, alias=SESSION_HEADER)):
        return source_material_response(
            source_materials_service.preview_pages, task_id, token
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
        except (ManualPlanError, DiagramPlanError, DrawioGenerationError) as error:
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
