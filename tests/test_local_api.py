import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.diagram_asset import DiagramAssetError
from software_copyright_agent.local_api import DiagramAssetApi


class FakeAssetService:
    def __init__(self, root: Path) -> None:
        self.data_root = root
        self.created = []

    def workspace_snapshot(self, task_id):
        return {"task_id": task_id, "assets": []}

    def list_revisions(self, task_id, diagram_key):
        return [{"revision_id": "revision-1", "version": 1}]

    def create_revision(self, task_id, diagram_key, operations, edit_source):
        self.created.append((task_id, diagram_key, operations, edit_source))
        return FakeRevision(task_id, diagram_key)

    def rebase_latest(self, task_id, diagram_key):
        return FakeRevision(task_id, diagram_key, version=2)

    def rollback_to(self, task_id, diagram_key, version):
        return FakeRevision(task_id, diagram_key, version=version + 1)

    def resolve_conflicts(self, revision_id, resolutions):
        if not resolutions:
            raise DiagramAssetError("Every conflict must have exactly one resolution")
        return FakeRevision("task-1", "system_architecture", version=3)

    def get_revision(self, revision_id):
        return {"revision_id": revision_id, "task_id": "task-1", "version": 1,
                "preview": {"svg_relative_path": "preview.svg"}}


class FakeResult:
    conflicts = ()


class FakeRevision:
    def __init__(self, task_id, diagram_key, version=1):
        self.revision_id = "revision-{0}".format(version)
        self.task_id = task_id
        self.diagram_key = diagram_key
        self.version = version
        self.status = "clean"
        self.result = FakeResult()


class DiagramAssetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "tasks" / "task-1").mkdir(parents=True)
        (self.root / "tasks" / "task-1" / "preview.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8"
        )
        self.token = "a" * 32
        self.service = FakeAssetService(self.root)
        self.api = DiagramAssetApi(self.service, self.token)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_token_is_required_and_compared_before_routing(self) -> None:
        response = self.api.handle("GET", "/api/v1/tasks/task-1/diagram-assets", None, None)
        self.assertEqual(response.status, 401)

    def test_workspace_save_list_and_svg_preview_contracts(self) -> None:
        workspace = self.api.handle(
            "GET", "/api/v1/tasks/task-1/diagram-assets", None, self.token
        )
        saved = self.api.handle(
            "POST", "/api/v1/tasks/task-1/diagram-assets/system_architecture/revisions",
            {"edit_source": "manual", "operations": [{
                "action": "node.move", "target": "module-a", "payload": {"x": 20},
            }]}, self.token,
        )
        listed = self.api.handle(
            "GET", "/api/v1/tasks/task-1/diagram-assets/system_architecture/revisions",
            None, self.token,
        )
        preview = self.api.handle(
            "GET", "/api/v1/diagram-revisions/revision-1/preview.svg", None, self.token
        )
        self.assertEqual((workspace.status, saved.status, listed.status, preview.status),
                         (200, 201, 200, 200))
        self.assertEqual(preview.content_type, "image/svg+xml")
        self.assertTrue(preview.body.startswith(b"<svg"))
        self.assertEqual(self.service.created[0][3], "manual")

    def test_invalid_schema_method_and_diagram_key_are_rejected(self) -> None:
        invalid = self.api.handle(
            "POST", "/api/v1/tasks/task-1/diagram-assets/system_architecture/revisions",
            {"edit_source": "manual", "operations": [{"action": "shell.execute"}]},
            self.token,
        )
        wrong_method = self.api.handle(
            "DELETE", "/api/v1/tasks/task-1/diagram-assets", None, self.token
        )
        missing = self.api.handle(
            "GET", "/api/v1/tasks/task-1/diagram-assets/unknown/revisions", None, self.token
        )
        self.assertEqual((invalid.status, wrong_method.status, missing.status), (400, 405, 404))


if __name__ == "__main__":
    unittest.main()
