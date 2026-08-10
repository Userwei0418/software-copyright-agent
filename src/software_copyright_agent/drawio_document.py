import hashlib
import html
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple


DRAWIO_GENERATOR_VERSION = "drawio-generator-v3"


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

        hidden = {node["key"] for node in diagram["nodes"]
                  if node.get("visual_override", {}).get("hidden")}
        visible_nodes = [node for node in diagram["nodes"] if node["key"] not in hidden]
        visible_edges = [edge for edge in diagram["edges"]
                         if edge["source"] not in hidden and edge["target"] not in hidden]
        positions = self._apply_position_overrides(positions, visible_nodes)
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
        for node in visible_nodes:
            x, y, width, height = positions[node["key"]]
            style = self._merge_style(
                self._node_style(node), node.get("visual_override", {}).get("style", {})
            )
            cell = ET.SubElement(root, "mxCell", {
                "id": node["key"], "value": node.get("display_label") or
                self._display_label(node["label"], node["kind"]),
                "vertex": "1", "parent": "1", "style": style,
            })
            ET.SubElement(cell, "mxGeometry", {
                "x": str(x), "y": str(y), "width": str(width),
                "height": str(height), "as": "geometry",
            })
        for edge_index, edge in enumerate(visible_edges):
            route_override = edge.get("visual_override", {}).get("route", {}).get("points")
            points = route_override or self._route(edge, positions, edge_index, diagram["key"])
            cell = ET.SubElement(root, "mxCell", {
                "id": edge["key"], "value": edge.get("display_label", ""),
                "edge": "1", "parent": "1",
                "source": edge["source"], "target": edge["target"],
                "style": self._merge_style(
                    self._edge_style(edge, diagram["key"]),
                    edge.get("visual_override", {}).get("style", {}),
                ),
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
            "node_count": len(visible_nodes), "edge_count": len(visible_edges),
            "canvas": list(canvas),
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }

    @staticmethod
    def _apply_position_overrides(positions: Dict[str, tuple], nodes: List[dict]) -> Dict[str, tuple]:
        result = dict(positions)
        for node in nodes:
            x, y, width, height = result[node["key"]]
            override = node.get("visual_override", {})
            move, resize = override.get("move", {}), override.get("resize", {})
            result[node["key"]] = (
                float(move.get("x", x)), float(move.get("y", y)),
                max(40.0, float(resize.get("width", width))),
                max(30.0, float(resize.get("height", height))),
            )
        return result

    @staticmethod
    def _merge_style(base: str, override: dict) -> str:
        allowed = {"fillColor", "strokeColor", "fontColor", "strokeWidth", "dashed"}
        values = {}
        order = []
        for part in base.split(";"):
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
                values[key] = value
                order.append(key)
            else:
                order.append(part)
        for key, value in override.items():
            if key in allowed and re.fullmatch(r"#[0-9a-fA-F]{6}|[0-9.]+", str(value)):
                values[key] = str(value)
                if key not in order:
                    order.append(key)
        return ";".join("{0}={1}".format(key, values[key]) if key in values else key
                        for key in order) + ";"


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


class InternalSvgRenderer:
    """Deterministic standard-library SVG exporter for the application's core path."""

    SVG_NS = "http://www.w3.org/2000/svg"

    def render(self, drawio_path: Path, svg_path: Path) -> None:
        source = ET.parse(drawio_path).getroot()
        model = source.find(".//mxGraphModel")
        if model is None:
            raise DrawioDocumentError("Draw.io model not found")
        width = float(model.get("pageWidth", "1200"))
        height = float(model.get("pageHeight", "800"))
        cells = source.findall(".//mxCell")
        vertices = {cell.get("id"): cell for cell in cells if cell.get("vertex") == "1"}
        svg = ET.Element("svg", {
            "xmlns": self.SVG_NS, "width": self._number(width),
            "height": self._number(height), "viewBox": "0 0 {0} {1}".format(
                self._number(width), self._number(height)),
            "role": "img", "aria-label": source.find(".//diagram").get("name", "diagram"),
        })
        defs = ET.SubElement(svg, "defs")
        for marker_id, color in (("arrow-slate", "#64748b"),
                                 ("arrow-amber", "#f59e0b"),
                                 ("arrow-red", "#ef4444")):
            marker = ET.SubElement(defs, "marker", {
                "id": marker_id, "markerWidth": "8", "markerHeight": "8",
                "refX": "7", "refY": "4", "orient": "auto",
                "markerUnits": "strokeWidth",
            })
            ET.SubElement(marker, "path", {
                "d": "M 0 0 L 8 4 L 0 8 z", "fill": color,
            })
        ET.SubElement(svg, "rect", {"width": "100%", "height": "100%", "fill": "#ffffff"})
        for cell in (item for item in cells if item.get("edge") == "1"):
            self._draw_edge(svg, cell, vertices)
        for cell in vertices.values():
            self._draw_vertex(svg, cell)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(svg).write(svg_path, encoding="utf-8", xml_declaration=True)
        if not svg_path.is_file() or svg_path.stat().st_size == 0:
            raise DrawioDocumentError("Internal SVG export produced no output")

    def _draw_edge(self, svg: ET.Element, cell: ET.Element, vertices: dict) -> None:
        source_rect = self._rect(vertices[cell.get("source")])
        target_rect = self._rect(vertices[cell.get("target")])
        geometry = cell.find("mxGeometry")
        points = [(float(point.get("x")), float(point.get("y")))
                  for point in geometry.findall("./Array[@as='points']/mxPoint")]
        source_center = self._center(source_rect)
        target_center = self._center(target_rect)
        first = points[0] if points else target_center
        last = points[-1] if points else source_center
        route = self._orthogonal_route(source_rect, target_rect, points)
        style = self._style(cell.get("style", ""))
        stroke = style.get("strokeColor", "#64748b")
        marker_id = "arrow-red" if stroke == "#ef4444" else (
            "arrow-amber" if stroke == "#f59e0b" else "arrow-slate"
        )
        attributes = {
            "points": " ".join("{0},{1}".format(self._number(x), self._number(y))
                               for x, y in route),
            "fill": "none", "stroke": stroke,
            "stroke-width": style.get("strokeWidth", "1.5"),
            "stroke-linejoin": "round", "marker-end": "url(#{0})".format(marker_id),
        }
        if style.get("dashed") == "1":
            attributes["stroke-dasharray"] = "7 5"
        ET.SubElement(svg, "polyline", attributes)

    def _draw_vertex(self, svg: ET.Element, cell: ET.Element) -> None:
        x, y, width, height = self._rect(cell)
        style = self._style(cell.get("style", ""))
        parent = svg
        if cell.get("id") != "diagram-title":
            parent = ET.SubElement(svg, "g", {
                "data-node-key": cell.get("id", ""),
                "data-x": self._number(x), "data-y": self._number(y),
                "data-width": self._number(width), "data-height": self._number(height),
                "tabindex": "0", "role": "button",
            })
            attributes = {
                "x": self._number(x), "y": self._number(y),
                "width": self._number(width), "height": self._number(height),
                "rx": "10", "fill": style.get("fillColor", "#ffffff"),
                "stroke": style.get("strokeColor", "#64748b"),
                "stroke-width": style.get("strokeWidth", "1.5"),
            }
            if style.get("dashed") == "1":
                attributes["stroke-dasharray"] = "7 5"
            ET.SubElement(parent, "rect", attributes)
        lines = self._text_lines(cell.get("value", ""))
        if not lines:
            return
        font_size = float(style.get("fontSize", "12"))
        text = ET.SubElement(parent, "text", {
            "x": self._number(x + (0 if cell.get("id") == "diagram-title" else width / 2)),
            "y": self._number(y + (font_size if cell.get("id") == "diagram-title" else
                                     height / 2 - (len(lines) - 1) * 8 + 4)),
            "text-anchor": "start" if cell.get("id") == "diagram-title" else "middle",
            "font-family": "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif",
            "font-size": self._number(font_size),
            "font-weight": "700" if cell.get("id") == "diagram-title" else "600",
            "fill": style.get("fontColor", "#0f172a"),
        })
        for index, line in enumerate(lines):
            span = ET.SubElement(text, "tspan", {
                "x": text.get("x"), "dy": "0" if index == 0 else "16",
            })
            if index > 0:
                span.set("font-size", "10")
                span.set("font-weight", "400")
                span.set("fill", "#64748b")
            span.text = line

    @staticmethod
    def _style(value: str) -> dict:
        return dict(part.split("=", 1) for part in value.split(";") if "=" in part)

    @staticmethod
    def _rect(cell: ET.Element) -> tuple:
        geometry = cell.find("mxGeometry")
        return tuple(float(geometry.get(key, "0")) for key in ("x", "y", "width", "height"))

    @staticmethod
    def _center(rect: tuple) -> tuple:
        x, y, width, height = rect
        return x + width / 2, y + height / 2

    @classmethod
    def _boundary(cls, rect: tuple, toward: tuple) -> tuple:
        cx, cy = cls._center(rect)
        dx, dy = toward[0] - cx, toward[1] - cy
        if dx == 0 and dy == 0:
            return cx, cy
        _, _, width, height = rect
        scale = min(width / (2 * abs(dx)) if dx else float("inf"),
                    height / (2 * abs(dy)) if dy else float("inf"))
        return cx + dx * scale, cy + dy * scale

    @classmethod
    def _orthogonal_route(cls, source_rect: tuple, target_rect: tuple,
                          points: List[tuple]) -> List[tuple]:
        source_center, target_center = cls._center(source_rect), cls._center(target_rect)
        if not points:
            if abs(source_center[0] - target_center[0]) < 1:
                start = cls._boundary(source_rect, (source_center[0], target_center[1]))
                end = cls._boundary(target_rect, (target_center[0], source_center[1]))
                return [start, end]
            middle_y = (source_center[1] + target_center[1]) / 2
            points = [(source_center[0], middle_y), (target_center[0], middle_y)]
        start = cls._axis_boundary(source_rect, points[0])
        end = cls._axis_boundary(target_rect, points[-1])
        route = [start]
        if start[0] != points[0][0] and start[1] != points[0][1]:
            route.append((points[0][0], start[1]))
        route.extend(points)
        if end[0] != points[-1][0] and end[1] != points[-1][1]:
            route.append((end[0], points[-1][1]))
        route.append(end)
        return [point for index, point in enumerate(route)
                if index == 0 or point != route[index - 1]]

    @classmethod
    def _axis_boundary(cls, rect: tuple, toward: tuple) -> tuple:
        x, y, width, height = rect
        cx, cy = cls._center(rect)
        dx, dy = toward[0] - cx, toward[1] - cy
        if abs(dx) > abs(dy):
            return (x + width if dx > 0 else x, cy)
        return (cx, y + height if dy > 0 else y)

    @staticmethod
    def _text_lines(value: str) -> List[str]:
        return [html.unescape(re.sub(r"<[^>]+>", "", part)).strip()
                for part in re.split(r"(?i)<br\s*/?>", value) if part.strip()]

    @staticmethod
    def _number(value: float) -> str:
        return ("{0:.2f}".format(value)).rstrip("0").rstrip(".")
