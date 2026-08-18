import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from fastapi.testclient import TestClient

from software_copyright_agent.sidecar import (
    MAX_DRAWIO_EDITOR_REQUEST_BYTES, MAX_REQUEST_BYTES, SESSION_HEADER, SIDECAR_PROTOCOL_VERSION,
    _parent_is_alive, _parent_pid_from_environment, create_app,
)


class SidecarFastApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.token = "s" * 48
        self.client = TestClient(create_app(self.data_dir, self.token))
        self.headers = {SESSION_HEADER: self.token}

    def tearDown(self) -> None:
        self.client.close()
        self.temporary.cleanup()

    def test_health_and_workspace_require_session_token(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").status_code, 401)
        health = self.client.get("/api/v1/health", headers=self.headers)
        workspace = self.client.get(
            "/api/v1/tasks/task-1/diagram-assets", headers=self.headers
        )
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["protocol_version"], SIDECAR_PROTOCOL_VERSION)
        self.assertEqual(workspace.status_code, 200)
        self.assertNotIn("access-control-allow-origin", workspace.headers)
        allowed = self.client.get(
            "/api/v1/health", headers={**self.headers, "origin": "tauri://localhost"}
        )
        development = self.client.get(
            "/api/v1/health",
            headers={**self.headers, "origin": "http://127.0.0.1:1420"},
        )
        denied = self.client.get(
            "/api/v1/health", headers={**self.headers, "origin": "https://example.com"}
        )
        self.assertEqual(allowed.headers["access-control-allow-origin"], "tauri://localhost")
        self.assertEqual(
            development.headers["access-control-allow-origin"], "http://127.0.0.1:1420"
        )
        self.assertNotIn("access-control-allow-origin", denied.headers)

    def test_quick_start_routes_require_session_and_validate_configuration(self) -> None:
        self.assertEqual(self.client.get("/api/v1/quick-start").status_code, 401)
        listed = self.client.get("/api/v1/quick-start", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"], [])
        invalid = self.client.post("/api/v1/quick-start", headers=self.headers, json={
            "project_path": str(self.data_dir / "missing"), "software_name": "演示软件",
            "version": "V1.0", "screenshot_folder": str(self.data_dir),
            "manual_model_id": "manual-model", "diagram_model_id": "diagram-model",
            "vision_model_id": "vision-model", "sensitive_confirmed": True,
            "auto_adopt_confirmed": True,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "quick_start_error")
        missing = self.client.delete(
            "/api/v1/quick-start/missing", headers=self.headers
        )
        self.assertEqual(missing.status_code, 400)

    def test_run_diagnostics_is_session_protected_and_empty_by_default(self) -> None:
        self.assertEqual(self.client.get("/api/v1/run-diagnostics").status_code, 401)
        response = self.client.get("/api/v1/run-diagnostics?limit=3", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_count"], 0)
        self.assertEqual(response.json()["runs"], [])

    def test_source_document_qa_capability_is_session_protected(self) -> None:
        endpoint = "/api/v1/tasks/task-1/source-materials/source-docx/qa-capability"
        self.assertEqual(self.client.get(endpoint).status_code, 401)
        response = self.client.get(endpoint, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("available", response.json())
        self.assertEqual(response.json()["renderer"], "libreoffice")
        self.assertIsInstance(response.json()["missing"], list)

    def test_figure_list_returns_stable_error_before_sections_exist(self) -> None:
        response = self.client.get(
            "/api/v1/manual-jobs/not-started/figures", headers=self.headers
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "manual_figure_error")

    def test_manual_document_finalize_route_is_registered(self) -> None:
        response = self.client.post(
            "/api/v1/manual-jobs/not-started/documents/1/finalize",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["code"], "manual_document_finalize_error"
        )

    def test_ui_operations_retry_uses_screenshot_workflow(self) -> None:
        endpoint = "/api/v1/manual-jobs/job-1/sections/ui_operations/regenerate"
        with patch(
            "software_copyright_agent.sidecar.ManualUiWorkflowService.regenerate_section",
            return_value={"section_key": "ui_operations", "version": 2},
        ) as regenerate_ui, patch(
            "software_copyright_agent.sidecar.ManualDraftingService.regenerate",
        ) as regenerate_regular:
            response = self.client.post(endpoint, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["section_key"], "ui_operations")
        regenerate_ui.assert_called_once_with("job-1")
        regenerate_regular.assert_not_called()

    def test_figure_asset_route_returns_drawio_source_bytes(self) -> None:
        source = b'<?xml version="1.0"?><mxfile><diagram id="a"/></mxfile>'
        with patch(
            "software_copyright_agent.sidecar.ManualFigureService.read_asset",
            return_value=(source, "application/vnd.jgraph.mxfile"),
        ) as read_asset:
            unauthorized = self.client.get(
                "/api/v1/manual-jobs/job-1/figures/architecture.drawio"
            )
            response = self.client.get(
                "/api/v1/manual-jobs/job-1/figures/architecture.drawio",
                headers=self.headers,
            )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, source)
        self.assertEqual(
            response.headers["content-type"], "application/vnd.jgraph.mxfile"
        )
        read_asset.assert_called_once_with("job-1", "architecture", "drawio")

    def test_project_screenshot_routes_import_and_persist_explicit_ui_decision(self) -> None:
        project = self.data_dir / "screenshot-project"
        project.mkdir()
        (project / "package.json").write_text(
            '{"name":"screenshot-demo","dependencies":{"react":"1"}}', encoding="utf-8")
        task_id = self.client.post(
            "/api/v1/projects/scan", headers=self.headers, json={"path": str(project)}
        ).json()["task_id"]
        screenshot = self.data_dir / "dashboard.png"
        Image.new("RGB", (1280, 720), "#dbeafe").save(screenshot)
        imported = self.client.post(
            f"/api/v1/tasks/{task_id}/screenshots/import-batch", headers=self.headers,
            json={"paths": [str(screenshot)], "source": "user"},
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["imported_count"], 1)
        asset_id = imported.json()["results"][0]["asset"]["id"]
        history = self.client.get(
            f"/api/v1/tasks/{task_id}/screenshots/{asset_id}/history", headers=self.headers,
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["image_revisions"][0]["version"], 1)
        restored = self.client.post(
            f"/api/v1/tasks/{task_id}/screenshots/{asset_id}/rollback", headers=self.headers,
            json={"image_version": 1},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["version"], 2)
        self.assertEqual(restored.json()["analysis_status"], "outdated")
        decision = self.client.put(
            f"/api/v1/tasks/{task_id}/screenshots/ui-evidence-decision",
            headers=self.headers, json={"decision": "source_inferred",
                                       "reason": "用户确认暂时没有真实截图"},
        )
        self.assertEqual(decision.status_code, 200)
        workspace = self.client.get(
            f"/api/v1/tasks/{task_id}/screenshots/workspace", headers=self.headers,
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(len(workspace.json()["assets"]), 1)
        self.assertEqual(workspace.json()["ui_evidence_decision"]["decision"],
                         "source_inferred")

    def test_desktop_parent_pid_is_validated_and_monitored(self) -> None:
        with patch.dict("os.environ", {"COPYRIGHT_AGENT_PARENT_PID": "invalid"}):
            self.assertIsNone(_parent_pid_from_environment())
        with patch.dict("os.environ", {"COPYRIGHT_AGENT_PARENT_PID": "4321"}):
            self.assertEqual(_parent_pid_from_environment(), 4321)
        with patch("software_copyright_agent.sidecar.os.kill") as signal:
            self.assertTrue(_parent_is_alive(4321))
            signal.assert_called_once_with(4321, 0)
        with patch("software_copyright_agent.sidecar.os.kill",
                   side_effect=ProcessLookupError):
            self.assertFalse(_parent_is_alive(4321))

    def test_pydantic_schema_errors_use_stable_error_contract(self) -> None:
        response = self.client.post(
            "/api/v1/tasks/task-1/diagram-assets/system_architecture/revisions",
            headers=self.headers,
            json={"edit_source": "manual", "operations": [{
                "action": "shell.execute", "target": "module-a", "payload": {},
            }]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_request_body_limit_is_enforced_before_route_execution(self) -> None:
        response = self.client.post(
            "/api/v1/tasks/task-1/diagram-assets/system_architecture/revisions",
            headers={**self.headers, "content-length": str(MAX_REQUEST_BYTES + 1)},
            content=b"{}",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "request_too_large")
        editor_endpoint = "/api/v1/manual-jobs/job/figures/figure/editor-revision"
        editor_schema_error = self.client.post(
            editor_endpoint,
            headers={**self.headers, "content-length": str(MAX_REQUEST_BYTES + 1)},
            content=b"{}",
        )
        self.assertEqual(editor_schema_error.status_code, 400)
        ai_patch_schema_error = self.client.post(
            "/api/v1/manual-jobs/job/figures/figure/ai-patch",
            headers={**self.headers, "content-length": str(MAX_REQUEST_BYTES + 1)},
            content=b"{}",
        )
        self.assertEqual(ai_patch_schema_error.status_code, 400)
        ai_stream_schema_error = self.client.post(
            "/api/v1/manual-jobs/job/figures/figure/ai-patch-stream",
            headers={**self.headers, "content-length": str(MAX_REQUEST_BYTES + 1)},
            content=b"{}",
        )
        self.assertEqual(ai_stream_schema_error.status_code, 400)
        editor_too_large = self.client.post(
            editor_endpoint,
            headers={**self.headers,
                     "content-length": str(MAX_DRAWIO_EDITOR_REQUEST_BYTES + 1)},
            content=b"{}",
        )
        self.assertEqual(editor_too_large.status_code, 413)

    def test_model_config_metadata_never_contains_api_key(self) -> None:
        config_id = "11111111-1111-4111-8111-111111111111"
        saved = self.client.post("/api/v1/model-configs", headers=self.headers, json={
            "id": config_id, "name": "Local model", "protocol_id": "openai_compatible",
            "base_url": "https://models.example.test/v1", "model_name": "writer-v1",
            "credential_ref": config_id,
        })
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["has_credential"])
        self.assertEqual(saved.json()["provider_id"], config_id)
        self.assertNotIn("credential_ref", saved.json())
        second_id = "22222222-2222-4222-8222-222222222222"
        second = self.client.post("/api/v1/model-configs", headers=self.headers, json={
            "id": second_id, "name": "Local model", "protocol_id": "openai_compatible",
            "base_url": "https://models.example.test/v1", "model_name": "writer-v2",
            "credential_ref": config_id,
        })
        self.assertEqual(second.json()["provider_id"], config_id)
        endpoint = self.client.post(
            f"/api/v1/model-configs/{second_id}/endpoint-mode", headers=self.headers,
            json={"endpoint_mode": "chat_completions"},
        )
        self.assertEqual(endpoint.status_code, 200)
        self.assertEqual(endpoint.json()["endpoint_mode"], "chat_completions")
        self.assertIsNotNone(endpoint.json()["verified_at"])
        with patch("software_copyright_agent.manual_screenshot_evidence.secrets.token_hex",
                   return_value="abcd"), patch(
            "software_copyright_agent.manual_screenshot_evidence.ScreenshotEvidenceService._vision_request",
            return_value='{"code":"ABCD"}',
        ):
            vision = self.client.post(
                f"/api/v1/model-configs/{second_id}/vision-capability", headers=self.headers,
                json={"supports_vision": True},
            )
        self.assertEqual(vision.status_code, 200)
        self.assertTrue(vision.json()["supports_vision"])
        listed = self.client.get("/api/v1/model-configs", headers=self.headers)
        self.assertEqual({item["model_name"] for item in listed.json()["items"]},
                         {"writer-v1", "writer-v2"})
        verified = self.client.post(
            f"/api/v1/model-configs/{config_id}/verified", headers=self.headers
        )
        self.assertIsNotNone(verified.json()["verified_at"])
        defaults = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(defaults.json()["source_strategy"], "standard")
        settings = self.client.post("/api/v1/settings", headers=self.headers, json={
            "manual_model_id": config_id, "diagram_model_id": config_id,
            "temperature": 0.2, "max_output_tokens": 16384,
            "source_strategy": "relaxed", "auto_preview": False,
        })
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json()["manual_model_id"], config_id)
        self.assertFalse(settings.json()["auto_preview"])
        deleted = self.client.delete(
            f"/api/v1/model-configs/{config_id}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204)
        self.client.delete(f"/api/v1/model-configs/{second_id}", headers=self.headers)
        cleared = self.client.get("/api/v1/settings", headers=self.headers).json()
        self.assertIsNone(cleared["manual_model_id"])
        self.assertIsNone(cleared["diagram_model_id"])

    def test_model_credentials_are_encrypted_and_session_protected(self) -> None:
        provider_id = "33333333-3333-4333-8333-333333333333"
        secret = "sk-sidecar-encrypted-secret"
        path = f"/api/v1/model-credentials/{provider_id}"
        self.assertEqual(self.client.put(path, json={"api_key": secret}).status_code, 401)
        stored = self.client.put(path, headers=self.headers, json={"api_key": secret})
        self.assertEqual(stored.status_code, 200)
        self.assertTrue(self.client.get(f"{path}/status", headers=self.headers).json()["available"])
        self.assertEqual(self.client.get(path, headers=self.headers).json()["api_key"], secret)
        self.assertNotIn(secret.encode(), (self.data_dir / "app.db").read_bytes())
        self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 204)
        self.assertFalse(self.client.get(f"{path}/status", headers=self.headers).json()["available"])

    def test_scan_project_returns_summary_facts_and_confirmations(self) -> None:
        project = self.data_dir / "project"
        (project / "src").mkdir(parents=True)
        (project / "package.json").write_text(
            '{"name":"desktop-demo"}', encoding="utf-8"
        )
        (project / "src" / "main.ts").write_text(
            "export const ready = true;\n", encoding="utf-8"
        )
        response = self.client.post(
            "/api/v1/projects/scan", headers=self.headers, json={"path": str(project)}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["file_count"], 2)
        self.assertIn("TypeScript", payload["summary"]["languages"])
        self.assertEqual(payload["inspection"]["task"]["id"], payload["task_id"])
        self.assertTrue(any(item["key"] == "project.name"
                            for item in payload["inspection"]["facts"]))
        inspection = self.client.get(
            f"/api/v1/tasks/{payload['task_id']}/inspection", headers=self.headers
        )
        self.assertEqual(inspection.status_code, 200)
        recent = self.client.get("/api/v1/tasks?limit=5", headers=self.headers)
        self.assertEqual(recent.status_code, 200)
        self.assertEqual(recent.json()["items"][0]["task_id"], payload["task_id"])
        confirmed = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/confirmations/project.version",
            headers=self.headers, json={"value": "V1.2.3"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["remaining_required"], 0)
        self.assertEqual(confirmed.json()["task_status"], "completed")
        self.assertTrue(any(
            item["key"] == "project.version" and item["status"] == "confirmed"
            for item in confirmed.json()["inspection"]["facts"]
        ))
        materials = self.client.get(
            f"/api/v1/tasks/{payload['task_id']}/source-materials", headers=self.headers
        )
        self.assertEqual(materials.status_code, 200)
        self.assertEqual(materials.json()["project"], {
            "name": "desktop-demo", "version": "V1.2.3"
        })
        self.assertTrue(materials.json()["actions"]["source_plan"])
        planned = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/source-materials/source-plan",
            headers=self.headers,
        )
        self.assertEqual(planned.status_code, 200)
        self.assertGreater(planned.json()["source_plan"]["summary"]["selected_files"], 0)
        previewed = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/source-materials/code-preview",
            headers=self.headers,
        )
        self.assertEqual(previewed.status_code, 200)
        self.assertFalse(previewed.json()["code_preview"]["summary"]["sufficient"])
        page_preview = self.client.get(
            f"/api/v1/tasks/{payload['task_id']}/source-materials/code-preview/pages",
            headers=self.headers,
        )
        self.assertEqual(page_preview.status_code, 200)
        self.assertGreater(page_preview.json()["total_pages"], 0)
        self.assertIn("export const ready", "\n".join(
            entry["text"] for page in page_preview.json()["pages"]
            for entry in page["entries"]
        ))
        self.assertLessEqual(len(page_preview.json()["pages"]), 3)
        blocked = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/source-materials/source-docx",
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["error"]["code"], "source_material_error")
        manual = self.client.get(
            f"/api/v1/tasks/{payload['task_id']}/manual-workspace", headers=self.headers
        )
        self.assertEqual(manual.status_code, 200)
        self.assertTrue(manual.json()["actions"]["manual_plan"])
        manual_planned = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/manual-workspace/manual-plan",
            headers=self.headers,
        )
        self.assertEqual(manual_planned.status_code, 200)
        self.assertEqual(len(manual_planned.json()["manual_plan"]["sections"]), 9)
        self.assertGreater(
            manual_planned.json()["manual_plan"]["summary"]["missing_information_count"], 0
        )
        diagram_planned = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/manual-workspace/diagram-plan",
            headers=self.headers,
        )
        self.assertEqual(diagram_planned.status_code, 200)
        self.assertEqual(len(diagram_planned.json()["diagram_plan"]["diagrams"]), 2)
        self.assertIn("missing_information",
                      diagram_planned.json()["diagram_plan"]["diagrams"][0])
        (project / "src" / "extra.ts").write_text(
            "export const addedLater = true;\n", encoding="utf-8"
        )
        rescanned = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/rescan", headers=self.headers
        )
        self.assertEqual(rescanned.status_code, 200)
        self.assertNotEqual(rescanned.json()["task_id"], payload["task_id"])
        self.assertEqual(rescanned.json()["summary"]["file_count"], 3)
        self.assertEqual(rescanned.json()["inspection"]["task"]["status"], "completed")
        self.assertTrue(any(
            item["key"] == "project.version" and item["value"] == "V1.2.3"
            for item in rescanned.json()["inspection"]["facts"]
        ))
        deleted = self.client.delete(
            f"/api/v1/tasks/{payload['task_id']}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(
            f"/api/v1/tasks/{payload['task_id']}/inspection", headers=self.headers
        ).status_code, 404)
        self.assertTrue((project / "src" / "main.ts").is_file())


if __name__ == "__main__":
    unittest.main()
