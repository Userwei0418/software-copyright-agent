import hashlib
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple


DRAWIO_GENERATOR_VERSION = "drawio-generator-v2"


class DrawioDocumentError(RuntimeError):
    pass


NODE_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#eff6ff;strokeColor=#3b82f6;"
    "fontColor=#1e3a8a;fontSize=12;strokeWidth=1.5;arcSize=12;spacing=8;"
)
STATE_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8fafc;strokeColor=#64748b;"
    "fontColor=#1e293b;fontSize=12;strokeWidth=1.5;arcSize=18;"
)
CONTEXT_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#fff7ed;strokeColor=#f97316;"
    "fontColor=#9a3412;fontSize=12;strokeWidth=1.5;dashed=1;spacing=8;"
)
EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "html=1;strokeColor=#64748b;strokeWidth=1.5;endArrow=block;endFill=1;"
)

DOMAIN_STYLE = NODE_STYLE.replace("#eff6ff", "#f5f3ff").replace(
    "#3b82f6", "#8b5cf6").replace("#1e3a8a", "#5b21b6")
PERSISTENCE_STYLE = NODE_STYLE.replace("#eff6ff", "#f0fdf4").replace(
    "#3b82f6", "#22c55e").replace("#1e3a8a", "#166534")
DOCUMENT_STYLE = NODE_STYLE.replace("#eff6ff", "#fff7ed").replace(
    "#3b82f6", "#f97316").replace("#1e3a8a", "#9a3412")
SUCCESS_STATE_STYLE = STATE_STYLE.replace("#f8fafc", "#f0fdf4").replace(
    "#64748b", "#22c55e").replace("#1e293b", "#166534")
WARNING_STATE_STYLE = STATE_STYLE.replace("#f8fafc", "#fffbeb").replace(
    "#64748b", "#f59e0b").replace("#1e293b", "#92400e")
ERROR_STATE_STYLE = STATE_STYLE.replace("#f8fafc", "#fef2f2").replace(
    "#64748b", "#ef4444").replace("#1e293b", "#991b1b")
RETURN_EDGE_STYLE = EDGE_STYLE.replace("#64748b", "#f59e0b") + "dashed=1;"
FAILURE_EDGE_STYLE = EDGE_STYLE.replace("#64748b", "#ef4444")


class DrawioDocumentBuilder:
    def build(self, diagram: dict, output_path: Path) -> dict:
        if diagram.get("status") != "ready":
            raise DrawioDocumentError("Diagram is not ready: {0}".format(diagram.get("key")))
        if diagram.get("key") == "system_architecture":
            positions, canvas = self._architecture_layout(diagram)
        elif diagram.get("key") == "core_business_flow":
            positions, canvas = self._workflow_layout(diagram)
        else:
            raise DrawioDocumentError("Unsupported diagram type: {0}".format(diagram.get("key")))

        mxfile = ET.Element("mxfile", {
            "host": "app.diagrams.net", "modified": "2026-08-10T00:00:00.000Z",
            "agent": "software-copyright-agent", "version": "24.7.17",
            "type": "device", "compressed": "false",
        })
        page = ET.SubElement(mxfile, "diagram", {
            "id": diagram["key"], "name": diagram["title"],
        })
        model = ET.SubElement(page, "mxGraphModel", {
            "dx": "1200", "dy": "800", "grid": "1", "gridSize": "10",
            "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
            "fold": "1", "page": "1", "pageScale": "1",
            "pageWidth": str(canvas[0]), "pageHeight": str(canvas[1]),
            "math": "0", "shadow": "0",
        })
        root = ET.SubElement(model, "root")
        ET.SubElement(root, "mxCell", {"id": "0"})
        ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
        title = ET.SubElement(root, "mxCell", {
            "id": "diagram-title", "value": diagram["title"], "vertex": "1",
            "parent": "1", "style": (
                "text;html=1;strokeColor=none;fillColor=none;align=left;"
                "verticalAlign=middle;whiteSpace=wrap;fontSize=22;fontStyle=1;"
                "fontColor=#0f172a;"
            ),
        })
        ET.SubElement(title, "mxGeometry", {
            "x": "48", "y": "28", "width": str(canvas[0] - 96), "height": "36",
            "as": "geometry",
        })
        for node in diagram["nodes"]:
            x, y, width, height = positions[node["key"]]
            style = self._node_style(node)
            cell = ET.SubElement(root, "mxCell", {
                "id": node["key"], "value": self._display_label(node["label"], node["kind"]),
                "vertex": "1", "parent": "1", "style": style,
            })
            ET.SubElement(cell, "mxGeometry", {
                "x": str(x), "y": str(y), "width": str(width),
                "height": str(height), "as": "geometry",
            })
        for edge_index, edge in enumerate(diagram["edges"]):
            points = self._route(edge, positions, edge_index, diagram["key"])
            cell = ET.SubElement(root, "mxCell", {
                "id": edge["key"], "value": "", "edge": "1", "parent": "1",
                "source": edge["source"], "target": edge["target"],
                "style": self._edge_style(edge, diagram["key"]),
            })
            geometry = ET.SubElement(cell, "mxGeometry", {
                "relative": "1", "as": "geometry", "x": "0", "y": "0",
            })
            array = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", {"x": str(x), "y": str(y)})

        output_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="drawio-", suffix=".tmp", dir=str(output_path.parent)
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            ET.ElementTree(mxfile).write(
                temporary, encoding="utf-8", xml_declaration=True, short_empty_elements=True
            )
            os.replace(str(temporary), str(output_path))
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "node_count": len(diagram["nodes"]), "edge_count": len(diagram["edges"]),
            "canvas": list(canvas),
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }


    def _architecture_layout(self, diagram: dict) -> Tuple[Dict[str, tuple], tuple]:
        module_nodes = [node for node in diagram["nodes"] if node["kind"] == "module"]
        context_nodes = [node for node in diagram["nodes"] if node["kind"] != "module"]
        adjacency = {node["key"]: [] for node in module_nodes}
        for edge in diagram["edges"]:
            if edge["source"] in adjacency and edge["target"] in adjacency:
                adjacency[edge["source"]].append(edge["target"])
                adjacency[edge["target"]].append(edge["source"])
        positions = {}
        visited = set()
        leaf_cursor = 0

        def place(node_key: str, parent: str, depth: int) -> float:
            nonlocal leaf_cursor
            visited.add(node_key)
            children = sorted(item for item in adjacency[node_key]
                              if item != parent and item not in visited)
            child_x = [place(child, node_key, depth + 1) for child in children]
            if child_x:
                x = sum(child_x) / len(child_x)
            else:
                x = 60 + leaf_cursor * 220
                leaf_cursor += 1
            positions[node_key] = (round(x), 110 + depth * 150, 190, 62)
            return x

        for node in sorted(module_nodes, key=lambda item: (-len(adjacency[item["key"]]), item["key"])):
            if node["key"] not in visited:
                place(node["key"], "", 0)
                leaf_cursor += 1
        max_y = max((value[1] for value in positions.values()), default=110)
        for index, node in enumerate(context_nodes):
            positions[node["key"]] = (60 + index * 220, max_y + 150, 190, 58)
        width = max((x + w for x, _, w, _ in positions.values()), default=1200) + 80
        height = max((y + h for _, y, _, h in positions.values()), default=800) + 80
        return positions, (max(1200, width), max(800, height))

    def _workflow_layout(self, diagram: dict) -> Tuple[Dict[str, tuple], tuple]:
        preferred = {
            "created": (600, 100), "running": (600, 250),
            "waiting_for_user": (250, 420), "cancel_requested": (250, 590),
            "canceled": (250, 760), "completed": (600, 590),
            "failed": (950, 420), "completed_with_warnings": (950, 590),
        }
        positions = {}
        fallback_index = 0
        for node in diagram["nodes"]:
            x, y = preferred.get(node["label"], (600, 100 + fallback_index * 150))
            if node["label"] not in preferred:
                fallback_index += 1
            positions[node["key"]] = (x, y, 220, 58)
        return positions, (1300, 930)

    @staticmethod
    def _route(edge: dict, positions: Dict[str, tuple], index: int,
               diagram_key: str) -> List[Tuple[int, int]]:
        sx, sy, sw, sh = positions[edge["source"]]
        tx, ty, tw, th = positions[edge["target"]]
        source_center = (sx + sw // 2, sy + sh // 2)
        target_center = (tx + tw // 2, ty + th // 2)
        if diagram_key == "system_architecture":
            middle_y = (source_center[1] + target_center[1]) // 2
            return [(source_center[0], middle_y), (target_center[0], middle_y)]
        source_key, target_key = edge["source"], edge["target"]
        if source_key == "state-waiting-for-user" and target_key == "state-running":
            return [(90, source_center[1]), (90, target_center[1])]
        if source_key in {"state-failed", "state-completed-with-warnings"} \
                and target_key == "state-running":
            gutter = 1210 if source_key == "state-failed" else 1240
            return [(gutter, source_center[1]), (gutter, target_center[1])]
        if source_key == "state-completed" and target_key == "state-running":
            return [(850, source_center[1]), (850, target_center[1])]
        if abs(source_center[0] - target_center[0]) < 10 and sy < ty:
            offset = 18 * (index % 3 - 1)
            x = source_center[0] + offset
            return [(x, sy + sh + 22), (x, ty - 22)]
        side = -1 if target_center[0] < source_center[0] else 1
        gutter = (90 if side < 0 else 1210) + side * (index % 4) * 18
        return [(gutter, source_center[1]), (gutter, target_center[1])]

    @staticmethod
    def _node_style(node: dict) -> str:
        label, kind = node["label"], node["kind"]
        if kind == "state":
            if label in {"completed", "canceled"}:
                return SUCCESS_STATE_STYLE
            if label == "completed_with_warnings" or label == "waiting_for_user":
                return WARNING_STATE_STYLE
            if label == "failed":
                return ERROR_STATE_STYLE
            return STATE_STYLE
        if kind in {"entrypoint", "storage"}:
            return CONTEXT_STYLE
        if label.endswith(("domain", "state_machine")):
            return DOMAIN_STYLE
        if label.endswith(("storage", "unit_of_work", "repositories")):
            return PERSISTENCE_STYLE
        if any(marker in label for marker in ("document", "preview", "diagram_plan", "manual_plan")):
            return DOCUMENT_STYLE
        return NODE_STYLE

    @staticmethod
    def _edge_style(edge: dict, diagram_key: str) -> str:
        if diagram_key != "core_business_flow":
            return EDGE_STYLE
        source = edge["source"].replace("state-", "")
        target = edge["target"].replace("state-", "")
        if target == "running" and source != "created":
            return RETURN_EDGE_STYLE
        if target == "failed":
            return FAILURE_EDGE_STYLE
        return EDGE_STYLE

    @staticmethod
    def _display_label(label: str, kind: str) -> str:
        state_labels = {
            "created": "已创建", "running": "处理中",
            "waiting_for_user": "等待用户确认", "cancel_requested": "请求取消",
            "canceled": "已取消", "completed": "已完成",
            "completed_with_warnings": "完成（有警告）", "failed": "执行失败",
        }
        if kind == "state" and label in state_labels:
            return "<b>{0}</b><br><font color=\"#64748b\" style=\"font-size:10px\">{1}</font>".format(
                state_labels[label], label
            )
        technical = label.replace("software_copyright_agent.", "")
        role_labels = {
            "cli": "命令入口", "service": "任务编排", "domain": "领域模型",
            "storage": "本地存储", "state_machine": "状态机",
            "unit_of_work": "事务工作单元", "diagram_plan_service": "图表规划",
            "source_document_service": "源码文档生成",
            "code_preview_service": "代码分页预览", "confirmation": "信息确认",
            "manual_plan_service": "说明书规划",
            "source_document_qa_service": "文档质量检查",
            "copyright-agent": "本地命令入口", "SQLite": "SQLite 数据库",
        }
        title = role_labels.get(technical, technical.replace("_", " "))
        if title == technical:
            return "<b>{0}</b>".format(title)
        return "<b>{0}</b><br><font color=\"#64748b\" style=\"font-size:10px\">{1}</font>".format(
            title, technical
        )


class DrawioDocumentInspector:
    """Small runtime quality gate; development also uses the stricter skill validator."""

    def inspect(self, path: Path) -> dict:
        root = ET.parse(path).getroot()
        cells = root.findall(".//mxCell")
        ids = [cell.get("id") for cell in cells]
        errors = []
        if root.get("compressed") != "false":
            errors.append("document must use uncompressed XML")
        if len(ids) != len(set(ids)):
            errors.append("cell ids must be unique")
        known = set(ids)
        vertices = [cell for cell in cells if cell.get("vertex") == "1"]
        edges = [cell for cell in cells if cell.get("edge") == "1"]
        rectangles = []
        for cell in vertices:
            geometry = cell.find("mxGeometry")
            if geometry is None:
                errors.append("vertex without geometry: {0}".format(cell.get("id")))
                continue
            if cell.get("id") != "diagram-title":
                rectangle = tuple(float(geometry.get(key, "0"))
                                  for key in ("x", "y", "width", "height"))
                rectangles.append((cell.get("id"), rectangle))
        for index, (left_id, left) in enumerate(rectangles):
            lx, ly, lw, lh = left
            for right_id, right in rectangles[index + 1:]:
                rx, ry, rw, rh = right
                if lx < rx + rw and lx + lw > rx and ly < ry + rh and ly + lh > ry:
                    errors.append("overlapping nodes: {0}, {1}".format(left_id, right_id))
        for edge in edges:
            if edge.get("source") not in known or edge.get("target") not in known:
                errors.append("edge endpoint missing: {0}".format(edge.get("id")))
            if not edge.findall("./mxGeometry/Array[@as='points']/mxPoint"):
                errors.append("edge lacks explicit waypoints: {0}".format(edge.get("id")))
        return {"passed": not errors, "errors": errors,
                "vertex_count": len(vertices) - 1, "edge_count": len(edges)}

    def require_valid(self, path: Path) -> dict:
        report = self.inspect(path)
        if not report["passed"]:
            raise DrawioDocumentError("Draw.io validation failed: {0}".format(
                "; ".join(report["errors"])
            ))
        return report


class DrawioSvgRenderer:
    def render(self, drawio_path: Path, svg_path: Path) -> None:
        configured = os.environ.get("COPYRIGHT_AGENT_DRAWIO")
        executable = configured or shutil.which("drawio") or shutil.which("draw.io")
        if not executable:
            mac_path = Path("/Applications/draw.io.app/Contents/MacOS/draw.io")
            executable = str(mac_path) if mac_path.is_file() else None
        if not executable or not Path(executable).is_file():
            raise DrawioDocumentError("Draw.io Desktop CLI is required for SVG export")
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [executable, "--export", "--format", "svg", "--crop",
             "--output", str(svg_path), str(drawio_path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0 or not svg_path.is_file() or svg_path.stat().st_size == 0:
            raise DrawioDocumentError(
                "Draw.io SVG export failed: {0}".format(
                    (result.stderr or result.stdout or "unknown error").strip()[-500:]
                )
            )
