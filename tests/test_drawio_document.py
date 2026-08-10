import json
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from software_copyright_agent.cli import main
from software_copyright_agent.drawio_document import (
    DrawioDocumentBuilder, DrawioDocumentInspector, InternalSvgRenderer,
)
from software_copyright_agent.drawio_service import DrawioGenerationService
from software_copyright_agent.diagram_asset_service import DiagramAssetService
from software_copyright_agent.storage import Database


class FakeRenderer:
    def render(self, drawio_path: Path, svg_path: Path) -> None:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")


class DrawioDocumentTests(unittest.TestCase):
    def test_builder_creates_editable_uncompressed_xml_with_waypoints(self) -> None:
        diagram = {
            "key": "system_architecture", "title": "系统总体架构图", "status": "ready",
            "nodes": [
                {"key": "module-a", "label": "demo.a", "kind": "module"},
                {"key": "module-b", "label": "demo.b", "kind": "module"},
            ],
            "edges": [{"key": "edge-a-b", "source": "module-a", "target": "module-b"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "architecture.drawio"
            result = DrawioDocumentBuilder().build(diagram, path)
            report = DrawioDocumentInspector().require_valid(path)
            root = ET.parse(path).getroot()
            self.assertEqual(root.get("compressed"), "false")
            self.assertEqual(result["node_count"], 2)
            self.assertEqual(report["edge_count"], 1)
            self.assertEqual(len(root.findall(".//mxCell[@edge='1']/mxGeometry/Array/mxPoint")), 2)

    def test_service_versions_and_persists_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            package = project / "src" / "demo"
            package.mkdir(parents=True)
            (project / "package.json").write_text(
                '{"name":"demo","version":"1.0.0"}', encoding="utf-8"
            )
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "state.py").write_text(
                """class TaskStatus:
    CREATED = 'created'
    RUNNING = 'running'
    COMPLETED = 'completed'

ALLOWED_TRANSITIONS = {
    TaskStatus.CREATED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED}),
}
""",
                encoding="utf-8",
            )
            (package / "service.py").write_text(
                """from .state import ALLOWED_TRANSITIONS

class DemoService:
    def transitions(self):
        return ALLOWED_TRANSITIONS
""", encoding="utf-8"
            )
            data = root / "data"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--data-dir", str(data), "scan", str(project), "--json"]), 0)
            task_id = json.loads(output.getvalue())["task_id"]
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["--data-dir", str(data), "manual-plan", task_id, "--json"]), 0)
                self.assertEqual(main(["--data-dir", str(data), "diagram-plan", task_id, "--json"]), 0)
            connection = sqlite3.connect(str(data / "app.db"))
            try:
                relative = connection.execute(
                    "SELECT artifact_relative_path FROM diagram_plan_runs WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            plan_path = data / "tasks" / task_id / relative
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            architecture = next(item for item in plan["diagrams"]
                                if item["key"] == "system_architecture")
            architecture.update({
                "status": "ready", "missing_evidence": [],
                "nodes": [
                    {"key": "module-service", "label": "demo.service", "kind": "module",
                     "fact_id": "fact", "evidence_ids": ["evidence"]},
                    {"key": "module-state", "label": "demo.state", "kind": "module",
                     "fact_id": "fact", "evidence_ids": ["evidence"]},
                ],
                "edges": [{"key": "dependency-1", "source": "module-service",
                           "target": "module-state", "label": "内部导入", "kind": "dependency",
                           "fact_id": "fact", "evidence_ids": ["evidence"],
                           "source_locator": {"relative_path": "src/demo/service.py", "line": 1}}],
            })
            workflow = next(item for item in plan["diagrams"]
                            if item["key"] == "core_business_flow")
            workflow.update({
                "status": "ready", "missing_evidence": [],
                "nodes": [
                    {"key": "state-created", "label": "created", "kind": "state",
                     "fact_id": "fact", "evidence_ids": ["evidence"]},
                    {"key": "state-running", "label": "running", "kind": "state",
                     "fact_id": "fact", "evidence_ids": ["evidence"]},
                ],
                "edges": [{"key": "transition-1", "source": "state-created",
                           "target": "state-running", "label": "状态转换", "kind": "transition",
                           "fact_id": "fact", "evidence_ids": ["evidence"],
                           "source_locator": {"relative_path": "src/demo/state.py", "line": 6}}],
            })
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            generated = DrawioGenerationService(
                Database(data / "app.db"), data, renderer=FakeRenderer()
            ).execute(task_id)
            self.assertTrue(all(path.is_file() for path in generated.paths.values()))
            self.assertTrue(generated.summary["architecture"]["validation"]["passed"])
            connection = sqlite3.connect(str(data / "app.db"))
            try:
                row = connection.execute(
                    "SELECT version, generator_version FROM diagram_artifact_runs"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, (1, "drawio-generator-v3"))

            asset_service = DiagramAssetService(Database(data / "app.db"), data)
            revision = asset_service.create_revision(
                task_id, "system_architecture", [{
                    "action": "node.move", "target": "module-service",
                    "payload": {"x": 140, "y": 90},
                }], "manual",
            )
            self.assertEqual(revision.status, "clean")
            self.assertTrue(revision.artifact_path.is_file())
            self.assertTrue(all(path.is_file() for path in revision.preview_paths.values()))
            snapshot = asset_service.workspace_snapshot(task_id)
            architecture_asset = next(item for item in snapshot["assets"]
                                      if item["diagram_key"] == "system_architecture")
            self.assertEqual(architecture_asset["revision_count"], 1)
            self.assertIn("node.move", architecture_asset["supported_actions"])
            connection = sqlite3.connect(str(data / "app.db"))
            try:
                stored = connection.execute(
                    """SELECT version, edit_source, status
                    FROM diagram_asset_revisions WHERE task_id = ?""", (task_id,)
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(stored, (1, "manual", "clean"))
            self.assertEqual(asset_service.list_revisions(
                task_id, "system_architecture"
            )[0]["operation_count"], 1)
            self.assertEqual(asset_service.get_revision(revision.revision_id)["version"], 1)

            rollback = asset_service.rollback_to(task_id, "system_architecture", 1)
            self.assertEqual((rollback.version, rollback.status), (2, "clean"))
            changed_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            changed_architecture = next(item for item in changed_plan["diagrams"]
                                        if item["key"] == "system_architecture")
            changed_architecture["nodes"][0]["label"] = "demo.application_service"
            plan_path.write_text(json.dumps(changed_plan, ensure_ascii=False), encoding="utf-8")
            rebased = asset_service.rebase_latest(task_id, "system_architecture")
            self.assertEqual(rebased.status, "conflicted")
            self.assertEqual(rebased.result.conflicts[0]["reason"], "target_changed")
            resolved = asset_service.resolve_conflicts(rebased.revision_id, [{
                "operation_index": 0, "resolution": "accept_current",
            }])
            self.assertEqual((resolved.version, resolved.status), (4, "clean"))
            self.assertEqual(len(asset_service.list_revisions(
                task_id, "system_architecture"
            )), 4)

    def test_internal_svg_renderer_and_visual_overrides(self) -> None:
        diagram = {
            "key": "system_architecture", "title": "系统总体架构图", "status": "ready",
            "nodes": [
                {"key": "module-a", "label": "demo.a", "kind": "module",
                 "display_label": "业务入口",
                 "visual_override": {"move": {"x": 150, "y": 90},
                                     "resize": {"width": 260, "height": 70},
                                     "style": {"fillColor": "#f0fdf4"}}},
                {"key": "module-b", "label": "demo.b", "kind": "module"},
            ],
            "edges": [{"key": "edge-a-b", "source": "module-a", "target": "module-b",
                       "visual_override": {"route": {"points": [[280, 200], [500, 200]]}}}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawio, svg = root / "diagram.drawio", root / "diagram.svg"
            DrawioDocumentBuilder().build(diagram, drawio)
            InternalSvgRenderer().render(drawio, svg)
            xml = ET.parse(drawio).getroot()
            geometry = xml.find(".//mxCell[@id='module-a']/mxGeometry")
            self.assertEqual((geometry.get("x"), geometry.get("width")), ("150.0", "260.0"))
            rendered = svg.read_text(encoding="utf-8")
            self.assertIn("业务入口", rendered)
            self.assertIn("#f0fdf4", rendered)
            self.assertIn("<polyline", rendered)
            svg_root = ET.parse(svg).getroot()
            draggable = next(item for item in svg_root.iter()
                             if item.get("data-node-key") == "module-a")
            self.assertEqual((draggable.get("data-x"), draggable.get("data-y")),
                             ("150", "90"))
            polyline = next(item for item in svg_root.iter() if item.tag.endswith("polyline"))
            points = [tuple(float(value) for value in item.split(","))
                      for item in polyline.get("points").split()]
            self.assertTrue(all(left[0] == right[0] or left[1] == right[1]
                                for left, right in zip(points, points[1:])))


if __name__ == "__main__":
    unittest.main()
