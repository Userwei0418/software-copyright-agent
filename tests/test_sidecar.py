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
        denied = self.client.get(
            "/api/v1/health", headers={**self.headers, "origin": "https://example.com"}
        )
        self.assertEqual(allowed.headers["access-control-allow-origin"], "tauri://localhost")
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
        blocked = self.client.post(
            f"/api/v1/tasks/{payload['task_id']}/source-materials/source-docx",
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["error"]["code"], "source_material_error")


if __name__ == "__main__":
    unittest.main()
