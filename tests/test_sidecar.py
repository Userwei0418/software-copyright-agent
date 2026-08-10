import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from software_copyright_agent.sidecar import (
    MAX_REQUEST_BYTES, SESSION_HEADER, SIDECAR_PROTOCOL_VERSION, create_app,
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

    def test_model_config_metadata_never_contains_api_key(self) -> None:
        config_id = "11111111-1111-4111-8111-111111111111"
        saved = self.client.post("/api/v1/model-configs", headers=self.headers, json={
            "id": config_id, "name": "Local model", "protocol_id": "openai_compatible",
            "base_url": "https://models.example.test/v1", "model_name": "writer-v1",
            "credential_ref": config_id,
        })
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["has_credential"])
        self.assertNotIn("credential_ref", saved.json())
        listed = self.client.get("/api/v1/model-configs", headers=self.headers)
        self.assertEqual(listed.json()["items"][0]["model_name"], "writer-v1")
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
        cleared = self.client.get("/api/v1/settings", headers=self.headers).json()
        self.assertIsNone(cleared["manual_model_id"])
        self.assertIsNone(cleared["diagram_model_id"])

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
