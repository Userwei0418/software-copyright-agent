import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .diagram_asset import ALLOWED_ACTIONS, DiagramAssetError
from .diagram_asset_service import DiagramAssetService


API_VERSION = "v1"
MAX_OPERATIONS = 500
DIAGRAM_KEYS = {"system_architecture", "core_business_flow"}


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: object
    content_type: str = "application/json; charset=utf-8"


class DiagramAssetApi:
    """Framework-neutral localhost API contract used by the future sidecar adapter."""

    def __init__(self, service: DiagramAssetService, session_token: str) -> None:
        if not isinstance(session_token, str) or len(session_token) < 32:
            raise ValueError("Session token must contain at least 32 characters")
        self._service = service
        self._token = session_token

    def handle(self, method: str, path: str, body: Optional[object],
               session_token: Optional[str]) -> ApiResponse:
        if not isinstance(session_token, str) or not hmac.compare_digest(
                session_token, self._token):
            return self._error(401, "unauthorized", "Invalid session token")
        try:
            return self._dispatch(method.upper(), path, body)
        except DiagramAssetError as error:
            message = str(error)
            status = 404 if "not found" in message.lower() else 400
            return self._error(status, "diagram_asset_error", message)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._error(400, "invalid_request", "Request schema is invalid")

    def _dispatch(self, method: str, path: str, body: Optional[object]) -> ApiResponse:
        match = re.fullmatch(r"/api/v1/tasks/([^/]+)/diagram-assets", path)
        if match:
            if method != "GET":
                return self._method_not_allowed()
            return ApiResponse(200, self._service.workspace_snapshot(match.group(1)))

        match = re.fullmatch(
            r"/api/v1/tasks/([^/]+)/diagram-assets/([^/]+)/revisions", path
        )
        if match:
            task_id, diagram_key = match.groups()
            self._require_diagram_key(diagram_key)
            if method == "GET":
                return ApiResponse(200, {"items": self._service.list_revisions(
                    task_id, diagram_key
                )})
            if method == "POST":
                payload = self._object(body)
                operations = self._operations(payload.get("operations"))
                persisted = self._service.create_revision(
                    task_id, diagram_key, operations,
                    self._edit_source(payload.get("edit_source")),
                )
                return ApiResponse(201, self._persisted_payload(persisted))
            return self._method_not_allowed()

        match = re.fullmatch(
            r"/api/v1/tasks/([^/]+)/diagram-assets/([^/]+)/(rebase|rollback)", path
        )
        if match:
            if method != "POST":
                return self._method_not_allowed()
            task_id, diagram_key, action = match.groups()
            self._require_diagram_key(diagram_key)
            if action == "rebase":
                persisted = self._service.rebase_latest(task_id, diagram_key)
            else:
                payload = self._object(body)
                version = payload.get("version")
                if not isinstance(version, int) or version < 1:
                    raise ValueError("version")
                persisted = self._service.rollback_to(task_id, diagram_key, version)
            return ApiResponse(201, self._persisted_payload(persisted))

        match = re.fullmatch(r"/api/v1/diagram-revisions/([^/]+)(/resolve|/preview\.svg)?", path)
        if match:
            revision_id, suffix = match.groups()
            if suffix is None:
                if method != "GET":
                    return self._method_not_allowed()
                return ApiResponse(200, self._service.get_revision(revision_id))
            if suffix == "/resolve":
                if method != "POST":
                    return self._method_not_allowed()
                payload = self._object(body)
                resolutions = payload.get("resolutions")
                if not isinstance(resolutions, list) or len(resolutions) > MAX_OPERATIONS:
                    raise ValueError("resolutions")
                persisted = self._service.resolve_conflicts(revision_id, resolutions)
                return ApiResponse(201, self._persisted_payload(persisted))
            if method != "GET":
                return self._method_not_allowed()
            revision = self._service.get_revision(revision_id)
            relative = revision["preview"]["svg_relative_path"]
            task_root = (self._service.data_root / "tasks" / revision["task_id"]).resolve()
            svg_path = (task_root / relative).resolve()
            if task_root not in svg_path.parents:
                return self._error(400, "invalid_artifact_path", "Preview path is invalid")
            return ApiResponse(200, svg_path.read_bytes(), "image/svg+xml")

        return self._error(404, "route_not_found", "Route not found")

    @staticmethod
    def _object(body: Optional[object]) -> dict:
        if not isinstance(body, dict):
            raise ValueError("body")
        return body

    @staticmethod
    def _operations(value: object) -> list:
        if not isinstance(value, list) or len(value) > MAX_OPERATIONS:
            raise ValueError("operations")
        for operation in value:
            if not isinstance(operation, dict) or operation.get("action") not in ALLOWED_ACTIONS:
                raise ValueError("operation")
        return value

    @staticmethod
    def _edit_source(value: object) -> str:
        if value not in {"manual", "ai"}:
            raise ValueError("edit_source")
        return str(value)

    @staticmethod
    def _require_diagram_key(value: str) -> None:
        if value not in DIAGRAM_KEYS:
            raise DiagramAssetError("Diagram not found: {0}".format(value))

    @staticmethod
    def _persisted_payload(persisted) -> dict:
        return {
            "revision_id": persisted.revision_id, "task_id": persisted.task_id,
            "diagram_key": persisted.diagram_key, "version": persisted.version,
            "status": persisted.status, "conflicts": list(persisted.result.conflicts),
            "preview_urls": {
                "svg": "/api/v1/diagram-revisions/{0}/preview.svg".format(
                    persisted.revision_id
                )
            },
        }

    @staticmethod
    def _method_not_allowed() -> ApiResponse:
        return DiagramAssetApi._error(405, "method_not_allowed", "Method not allowed")

    @staticmethod
    def _error(status: int, code: str, message: str) -> ApiResponse:
        return ApiResponse(status, {"error": {"code": code, "message": message}})
