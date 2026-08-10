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


if __name__ == "__main__":
    unittest.main()
