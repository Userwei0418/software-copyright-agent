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
    DrawioDocumentBuilder, DrawioDocumentInspector, GenericDrawioDocumentBuilder,
    InternalPngRenderer, InternalSvgRenderer,
)
from software_copyright_agent.drawio_service import DrawioGenerationService
from software_copyright_agent.diagram_asset_service import DiagramAssetService
from software_copyright_agent.storage import Database


class FakeRenderer:
    def render(self, drawio_path: Path, svg_path: Path) -> None:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")


class DrawioDocumentTests(unittest.TestCase):
    def test_short_terminal_edge_label_is_clamped_inside_canvas(self) -> None:
        figure = {
            "figure_key": "short-terminal-label", "title": "短边标签回归",
            "figure_type": "data_flow", "layout": "flow-left-right",
            "nodes": [
                {"key": "source", "label": "分析服务", "kind": "external", "layer": 0},
                {"key": "target", "label": "缓存服务", "kind": "datastore", "layer": 0},
            ],
            "edges": [{"key": "ai_result", "source": "source", "target": "target",
                       "label": "分析结果回写"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "short.drawio"
            GenericDrawioDocumentBuilder().build(figure, path)
            report = DrawioDocumentInspector().require_valid(path)
        self.assertTrue(report["passed"])

    def test_impossible_edge_label_is_suppressed_without_losing_semantics(self) -> None:
        figure = {
            "figure_key": "dense-data-flow", "title": "核心数据流图",
            "figure_type": "data_flow", "layout": "flow-left-right",
            "nodes": [
                {"key": "user", "label": "系统用户", "kind": "actor", "layer": 0},
                {"key": "auth_ui", "label": "用户认证页面", "kind": "component", "layer": 1},
                {"key": "dress_api", "label": "服饰资源接口", "kind": "component", "layer": 1},
                {"key": "auth", "label": "身份验证与权限处理", "kind": "process", "layer": 2},
                {"key": "dress", "label": "服饰数据管理处理", "kind": "process", "layer": 2},
                {"key": "db", "label": "关系型数据库", "kind": "datastore", "layer": 3},
                {"key": "cos", "label": "对象存储服务", "kind": "datastore", "layer": 3},
                {"key": "cache", "label": "缓存与会话存储", "kind": "datastore", "layer": 3},
            ],
            "edges": [
                {"key": "user_auth", "source": "user", "target": "auth_ui", "label": "输入登录凭证"},
                {"key": "auth_svc", "source": "auth_ui", "target": "auth", "label": "转发认证请求"},
                {"key": "auth_db", "source": "auth", "target": "db", "label": "读写用户信息"},
                {"key": "auth_cache", "source": "auth", "target": "cache", "label": "存储会话令牌"},
                {"key": "user_dress", "source": "user", "target": "dress_api", "label": "提交业务请求"},
                {"key": "dress_svc", "source": "dress_api", "target": "dress", "label": "解析请求参数"},
                {"key": "dress_db", "source": "dress", "target": "db", "label": "保存服饰元数据"},
                {"key": "dress_cos", "source": "dress", "target": "cos", "label": "上传多媒体文件"},
                {"key": "cache_user", "source": "cache", "target": "user", "label": "推送实时消息"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            drawio = Path(temporary) / "dense.drawio"
            GenericDrawioDocumentBuilder().build(figure, drawio)
            report = DrawioDocumentInspector().require_valid(drawio)
            cells = ET.parse(drawio).getroot().findall(".//mxCell[@edge='1']")
        self.assertTrue(report["passed"])
        suppressed = [cell for cell in cells if cell.get("dataLabelSuppressed") == "true"]
        self.assertTrue(suppressed)
        self.assertTrue(all(cell.get("dataSemanticLabel") for cell in suppressed))

    def test_real_module_graph_routes_user_to_database_without_crossing_nodes(self) -> None:
        """Regression for the 2026-08-11 霓裳云枢 module figure failure."""
        figure = {
            "figure_key": "module_architecture_interaction",
            "title": "核心业务模块交互架构图",
            "figure_type": "module", "layout": "layered-vertical",
            "nodes": [
                {"key": "dress_management_module", "label": "服饰管理模块",
                 "kind": "module", "layer": 0},
                {"key": "user_permission_module", "label": "用户与权限模块",
                 "kind": "module", "layer": 0},
                {"key": "cache_analytics_module", "label": "缓存分析模块",
                 "kind": "module", "layer": 0},
                {"key": "file_manager", "label": "文件管理器",
                 "kind": "component", "layer": 1},
                {"key": "message_service", "label": "系统消息服务",
                 "kind": "service", "layer": 1},
                {"key": "relational_database", "label": "关系型数据库",
                 "kind": "datastore", "layer": 2},
                {"key": "object_storage", "label": "对象存储",
                 "kind": "datastore", "layer": 2},
                {"key": "redis_cache", "label": "Redis缓存",
                 "kind": "datastore", "layer": 2},
            ],
            "edges": [
                {"key": "dress_to_user_auth", "source": "dress_management_module",
                 "target": "user_permission_module", "label": "角色权限校验"},
                {"key": "dress_to_file_manager", "source": "dress_management_module",
                 "target": "file_manager", "label": "生成预签名地址"},
                {"key": "dress_to_db", "source": "dress_management_module",
                 "target": "relational_database", "label": "查询服饰记录"},
                {"key": "file_manager_to_storage", "source": "file_manager",
                 "target": "object_storage", "label": "预签发下载链接"},
                {"key": "user_to_message", "source": "user_permission_module",
                 "target": "message_service", "label": "投递通知消息"},
                {"key": "user_to_db", "source": "user_permission_module",
                 "target": "relational_database", "label": "持久化用户实体"},
                {"key": "cache_to_redis", "source": "cache_analytics_module",
                 "target": "redis_cache", "label": "扫描缓存统计键"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "module.drawio"
            result = GenericDrawioDocumentBuilder().build(figure, path)
            report = DrawioDocumentInspector().require_valid(path)
            root = ET.parse(path).getroot()
            user_route = root.findall(
                ".//mxCell[@id='user_to_db']/mxGeometry/Array/mxPoint"
            )
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(len(user_route), 2)
        self.assertGreater(result["canvas"][0] / result["canvas"][1], 1.25)

    def test_wide_module_layer_wraps_into_readable_grid(self) -> None:
        nodes = [
            {"key": "entry", "label": "前端控制器", "kind": "component", "layer": 0},
            *[
                {"key": "module-{0}".format(index), "label": "业务模块 {0}".format(index),
                 "kind": "service", "layer": 1}
                for index in range(5)
            ],
            {"key": "store", "label": "业务数据库", "kind": "datastore", "layer": 2},
        ]
        figure = {
            "figure_key": "module-grid", "title": "模块架构图",
            "figure_type": "module", "layout": "layered-vertical",
            "nodes": nodes, "edges": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "module.drawio"
            result = GenericDrawioDocumentBuilder().build(figure, path)
            geometries = {
                cell.get("id"): tuple(float(cell.find("mxGeometry").get(key))
                                      for key in ("x", "y", "width", "height"))
                for cell in ET.parse(path).getroot().findall(".//mxCell[@vertex='1']")
                if cell.get("id", "").startswith("module-")
            }
        self.assertEqual(len({geometry[1] for geometry in geometries.values()}), 2)
        self.assertLess(result["canvas"][0] / result["canvas"][1], 2.0)

    def test_long_horizontal_flow_uses_compact_two_column_snake(self) -> None:
        nodes = [
            {"key": "n{0}".format(index), "label": "阶段 {0}".format(index),
             "kind": "process", "layer": index}
            for index in range(5)
        ]
        figure = {
            "figure_key": "long-flow", "title": "长流程",
            "figure_type": "workflow", "layout": "flow-left-right", "nodes": nodes,
            "edges": [
                {"key": "e{0}".format(index), "source": "n{0}".format(index),
                 "target": "n{0}".format(index + 1), "label": "下一步"}
                for index in range(4)
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = GenericDrawioDocumentBuilder().build(
                figure, Path(temporary) / "long-flow.drawio"
            )
        self.assertLess(result["canvas"][1], result["canvas"][0])
        self.assertGreater(result["canvas"][1], 400)

    def test_long_data_flow_uses_compact_two_column_snake(self) -> None:
        nodes = [
            {"key": "d{0}".format(index), "label": "数据处理阶段 {0}".format(index),
             "kind": "process", "layer": index}
            for index in range(6)
        ]
        figure = {
            "figure_key": "data-flow", "title": "核心数据流",
            "figure_type": "data_flow", "layout": "flow-left-right", "nodes": nodes,
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data-flow.drawio"
            result = GenericDrawioDocumentBuilder().build(figure, path)
            geometries = {
                cell.get("id"): tuple(float(cell.find("mxGeometry").get(key))
                                      for key in ("x", "y", "width", "height"))
                for cell in ET.parse(path).getroot().findall(".//mxCell[@vertex='1']")
                if cell.get("id", "") in {"d{0}".format(index) for index in range(6)}
            }
        self.assertEqual(len({geometry[0] for geometry in geometries.values()}), 2)
        self.assertEqual(len({geometry[1] for geometry in geometries.values()}), 3)
        self.assertGreater(result["canvas"][0] / result["canvas"][1], 1.25)

    def test_branched_data_flow_uses_compact_ladder(self) -> None:
        nodes = [
            {"key": "start", "label": "操作入口", "kind": "actor", "layer": 0},
            {"key": "image", "label": "图片上传", "kind": "service", "layer": 1},
            {"key": "model", "label": "模型上传", "kind": "service", "layer": 1},
            {"key": "store", "label": "数据实体", "kind": "datastore", "layer": 2},
            {"key": "review", "label": "审核服务", "kind": "service", "layer": 3},
            {"key": "state", "label": "审核状态", "kind": "decision", "layer": 4},
        ]
        figure = {
            "figure_key": "branched-data-flow", "title": "上传审核流程",
            "figure_type": "data_flow", "layout": "flow-top-down", "nodes": nodes,
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = GenericDrawioDocumentBuilder().build(
                figure, Path(temporary) / "branched.drawio"
            )
        self.assertGreater(result["canvas"][0] / result["canvas"][1], 1.05)

    def test_generic_builder_exports_editable_svg_and_high_resolution_png(self) -> None:
        figure = {
            "figure_key": "module_collaboration", "title": "模块协作图",
            "layout": "layered-vertical",
            "nodes": [
                {"key": "entry", "label": "任务入口", "kind": "actor", "layer": 0},
                {"key": "service", "label": "业务服务", "kind": "service", "layer": 1},
                {"key": "store", "label": "本地数据库", "kind": "datastore", "layer": 2},
            ],
            "edges": [
                {"key": "submit", "source": "entry", "target": "service", "label": "提交"},
                {"key": "persist", "source": "service", "target": "store", "label": "持久化"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawio, svg, png = root / "figure.drawio", root / "figure.svg", root / "figure.png"
            result = GenericDrawioDocumentBuilder().build(figure, drawio)
            report = DrawioDocumentInspector().require_valid(drawio)
            InternalSvgRenderer().render(drawio, svg)
            png_result = InternalPngRenderer().render(drawio, png)
            self.assertEqual((result["node_count"], report["edge_count"]), (3, 2))
            self.assertGreater(png_result["width"], 1500)
            self.assertGreater(png.stat().st_size, 1000)
            rendered = svg.read_text(encoding="utf-8")
            self.assertIn("任务入口", rendered)
            self.assertIn('data-edge-source="entry"', rendered)
            self.assertIn('data-edge-target="service"', rendered)
            self.assertIn('data-edge-label="true"', rendered)

    def test_edge_labels_are_placed_outside_node_boxes(self) -> None:
        figure = {
            "figure_key": "label-clearance", "title": "连线标签避让",
            "layout": "layered-vertical",
            "nodes": [
                {"key": "entry", "label": "前端入口", "kind": "actor", "layer": 0},
                {"key": "service", "label": "业务服务", "kind": "service", "layer": 1},
                {"key": "store", "label": "持久化数据库", "kind": "datastore", "layer": 2},
            ],
            "edges": [
                {"key": "submit", "source": "entry", "target": "service",
                 "label": "异步接口请求"},
                {"key": "persist", "source": "service", "target": "store",
                 "label": "持久化业务数据"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawio, svg = root / "figure.drawio", root / "figure.svg"
            GenericDrawioDocumentBuilder().build(figure, drawio)
            report = DrawioDocumentInspector().require_valid(drawio)
            InternalSvgRenderer().render(drawio, svg)
            rendered = ET.parse(svg).getroot()
            node_boxes = [tuple(float(group.find("{*}rect").get(key))
                                for key in ("x", "y", "width", "height"))
                          for group in rendered.findall(".//{*}g")
                          if group.get("data-node-key") and group.find("{*}rect") is not None]
            label_boxes = [tuple(float(rect.get(key)) for key in
                                 ("x", "y", "width", "height"))
                           for rect in rendered.findall(".//{*}rect")
                           if rect.get("rx") == "5"]
            self.assertTrue(report["passed"])
            self.assertEqual(len(label_boxes), 2)
            self.assertFalse(any(InternalSvgRenderer._rectangles_overlap(label, node, 1)
                                 for label in label_boxes for node in node_boxes))
            self.assertFalse(any(
                InternalSvgRenderer._rectangles_overlap(left, right, 4)
                for index, left in enumerate(label_boxes)
                for right in label_boxes[index + 1:]
            ))

    def test_dense_return_and_branch_labels_find_a_clear_local_lane(self) -> None:
        figures = [
            {
                "figure_key": "download-flow", "title": "下载数据流",
                "figure_type": "data_flow", "layout": "flow-left-right",
                "nodes": [
                    {"key": "client", "label": "前端客户端", "kind": "actor", "layer": 0},
                    {"key": "api", "label": "下载接口", "kind": "service", "layer": 1},
                    {"key": "query", "label": "资源查询", "kind": "process", "layer": 2},
                    {"key": "sign", "label": "凭证生成", "kind": "service", "layer": 2},
                    {"key": "store", "label": "对象存储", "kind": "datastore", "layer": 3},
                ],
                "edges": [
                    {"key": "request", "source": "client", "target": "api", "label": "发起下载请求"},
                    {"key": "query-edge", "source": "api", "target": "query", "label": "查询资源信息"},
                    {"key": "return", "source": "query", "target": "api", "label": "返回文件路径"},
                    {"key": "sign-edge", "source": "api", "target": "sign", "label": "请求生成凭证"},
                    {"key": "signed", "source": "sign", "target": "api", "label": "返回预签名地址"},
                    {"key": "deliver", "source": "api", "target": "client", "label": "下发预签名地址"},
                    {"key": "download", "source": "client", "target": "store", "label": "直连下载文件"},
                ],
            },
            {
                "figure_key": "waterfall-flow", "title": "瀑布流加载流程",
                "figure_type": "workflow", "layout": "flow-top-down",
                "nodes": [
                    {"key": "search", "label": "用户检索", "kind": "actor", "layer": 0},
                    {"key": "validate", "label": "参数校验", "kind": "process", "layer": 1},
                    {"key": "request", "label": "分页请求", "kind": "service", "layer": 2},
                    {"key": "state", "label": "加载状态", "kind": "component", "layer": 2},
                    {"key": "render", "label": "瀑布流渲染", "kind": "component", "layer": 3},
                    {"key": "feedback", "label": "结果提示", "kind": "component", "layer": 4},
                ],
                "edges": [
                    {"key": "e1", "source": "search", "target": "validate", "label": "触发检索"},
                    {"key": "e2", "source": "validate", "target": "request", "label": "传递参数"},
                    {"key": "e3", "source": "validate", "target": "feedback", "label": "空词提示"},
                    {"key": "e4", "source": "request", "target": "state", "label": "返回分页数据"},
                    {"key": "e5", "source": "request", "target": "feedback", "label": "网络异常"},
                    {"key": "e6", "source": "state", "target": "render", "label": "追加列表"},
                    {"key": "e7", "source": "state", "target": "feedback", "label": "更新状态"},
                    {"key": "e8", "source": "render", "target": "feedback", "label": "结束标识"},
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for figure in figures:
                drawio = root / (figure["figure_key"] + ".drawio")
                GenericDrawioDocumentBuilder().build(figure, drawio)
                report = DrawioDocumentInspector().require_valid(drawio)
                self.assertTrue(report["passed"], figure["figure_key"])

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
            self.assertEqual(row, (1, "drawio-generator-v11"))

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

    def test_manual_architecture_has_layer_labels_and_role_subtitles(self) -> None:
        figure = {
            "figure_key": "architecture", "title": "系统架构",
            "figure_type": "architecture", "layout": "layered-vertical",
            "nodes": [
                {"key": "ui", "label": "前端应用", "kind": "component", "layer": 0},
                {"key": "api", "label": "业务服务", "kind": "service", "layer": 1},
                {"key": "db", "label": "本地数据库", "kind": "datastore", "layer": 2},
            ],
            "edges": [
                {"key": "request", "source": "ui", "target": "api", "label": "请求"},
                {"key": "persist", "source": "api", "target": "db", "label": "写入"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawio, svg = root / "figure.drawio", root / "figure.svg"
            GenericDrawioDocumentBuilder().build(figure, drawio)
            report = DrawioDocumentInspector().require_valid(drawio)
            InternalSvgRenderer().render(drawio, svg)
            rendered = svg.read_text(encoding="utf-8")
        self.assertEqual(report["vertex_count"], 3)
        self.assertIn("界面与访问层", rendered)
        self.assertIn("界面 / 组件", rendered)
        self.assertNotIn('data-node-key="layer-label-0"', rendered)

    def test_architecture_uses_page_width_for_three_peer_data_layer(self) -> None:
        figure = {
            "figure_key": "architecture-wide", "title": "系统架构",
            "figure_type": "architecture", "layout": "layered-vertical",
            "nodes": [
                {"key": "ui", "label": "前端", "kind": "component", "layer": 0},
                {"key": "api", "label": "后端", "kind": "service", "layer": 1},
                {"key": "db", "label": "数据库", "kind": "datastore", "layer": 2},
                {"key": "cache", "label": "缓存", "kind": "datastore", "layer": 2},
                {"key": "files", "label": "对象存储", "kind": "external", "layer": 2},
            ],
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "architecture.drawio"
            result = GenericDrawioDocumentBuilder().build(figure, path)
            root = ET.parse(path).getroot()
            data_x = {
                float(root.find(".//mxCell[@id='{0}']/mxGeometry".format(key)).get("x"))
                for key in ("db", "cache", "files")
            }
        self.assertEqual(len(data_x), 3)
        self.assertGreater(result["canvas"][0] / result["canvas"][1], 1.4)

    def test_png_arrowhead_follows_last_segment_direction(self) -> None:
        self.assertEqual(InternalPngRenderer._arrow_polygon((20, 10), (20, 30)),
                         [(20, 30), (15, 22), (25, 22)])
        self.assertEqual(InternalPngRenderer._arrow_polygon((30, 20), (10, 20)),
                         [(10, 20), (18, 15), (18, 25)])

    def test_bidirectional_edges_use_distinct_routes(self) -> None:
        figure = {
            "figure_key": "request-response", "title": "请求响应",
            "figure_type": "architecture", "layout": "layered-vertical",
            "nodes": [
                {"key": "client", "label": "客户端", "kind": "component", "layer": 0},
                {"key": "server", "label": "服务端", "kind": "service", "layer": 1},
            ],
            "edges": [
                {"key": "request", "source": "client", "target": "server", "label": "请求"},
                {"key": "response", "source": "server", "target": "client", "label": "响应"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "figure.drawio"
            GenericDrawioDocumentBuilder().build(figure, path)
            root = ET.parse(path).getroot()
            routes = []
            for key in ("request", "response"):
                routes.append([(point.get("x"), point.get("y")) for point in root.findall(
                    ".//mxCell[@id='{0}']/mxGeometry/Array/mxPoint".format(key))])
        self.assertNotEqual(routes[0], routes[1])

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
