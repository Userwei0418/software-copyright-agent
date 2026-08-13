import hashlib
import heapq
import html
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont


DRAWIO_GENERATOR_VERSION = "drawio-generator-v11"


class DrawioDocumentError(RuntimeError):
    pass


NODE_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#eff6ff;strokeColor=#3b82f6;"
    "fontColor=#1e3a8a;fontSize=13;strokeWidth=1.5;arcSize=12;spacing=9;shadow=0;"
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
SERVICE_STYLE = NODE_STYLE.replace("#eff6ff", "#ecfeff").replace(
    "#3b82f6", "#0891b2").replace("#1e3a8a", "#155e75")
DECISION_STYLE = (
    "rhombus;whiteSpace=wrap;html=1;fillColor=#fffbeb;strokeColor=#f59e0b;"
    "fontColor=#92400e;fontSize=12;strokeWidth=1.5;spacing=8;"
)


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
            if edge.get("label"):
                ET.SubElement(geometry, "mxPoint", {
                    "x": "0", "y": "-14", "as": "offset",
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
        model = root.find(".//mxGraphModel")
        canvas = (
            float(model.get("pageWidth", "100000")) if model is not None else 100000.0,
            float(model.get("pageHeight", "100000")) if model is not None else 100000.0,
        )
        cells = root.findall(".//mxCell")
        ids = [cell.get("id") for cell in cells]
        errors = []
        if root.get("compressed") != "false":
            errors.append("document must use uncompressed XML")
        if len(ids) != len(set(ids)):
            errors.append("cell ids must be unique")
        known = set(ids)
        vertices = [cell for cell in cells if cell.get("vertex") == "1"]
        semantic_vertices = [cell for cell in vertices
                             if cell.get("id") != "diagram-title"
                             and cell.get("dataRole") not in {"layer-label", "layer-band"}]
        edges = [cell for cell in cells if cell.get("edge") == "1"]
        rectangles = []
        for cell in vertices:
            geometry = cell.find("mxGeometry")
            if geometry is None:
                errors.append("vertex without geometry: {0}".format(cell.get("id")))
                continue
            if cell.get("id") != "diagram-title" and cell.get("dataRole") not in {
                    "layer-label", "layer-band"}:
                rectangle = tuple(float(geometry.get(key, "0"))
                                  for key in ("x", "y", "width", "height"))
                rectangles.append((cell.get("id"), rectangle))
        for index, (left_id, left) in enumerate(rectangles):
            lx, ly, lw, lh = left
            for right_id, right in rectangles[index + 1:]:
                rx, ry, rw, rh = right
                if lx < rx + rw and lx + lw > rx and ly < ry + rh and ly + lh > ry:
                    errors.append("overlapping nodes: {0}, {1}".format(left_id, right_id))
        label_boxes = []
        title_obstacles = []
        for cell in vertices:
            if cell.get("id") != "diagram-title":
                continue
            geometry = cell.find("mxGeometry")
            if geometry is not None:
                title_obstacles.append(tuple(float(geometry.get(key, "0"))
                                             for key in ("x", "y", "width", "height")))
        for edge in edges:
            if edge.get("source") not in known or edge.get("target") not in known:
                errors.append("edge endpoint missing: {0}".format(edge.get("id")))
            if not edge.findall("./mxGeometry/Array[@as='points']/mxPoint"):
                errors.append("edge lacks explicit waypoints: {0}".format(edge.get("id")))
                continue
            source = next((item for item in vertices if item.get("id") == edge.get("source")), None)
            target = next((item for item in vertices if item.get("id") == edge.get("target")), None)
            if source is None or target is None:
                continue
            points = [(float(point.get("x")), float(point.get("y")))
                      for point in edge.findall("./mxGeometry/Array[@as='points']/mxPoint")]
            route = InternalSvgRenderer._orthogonal_route(
                InternalSvgRenderer._rect(source), InternalSvgRenderer._rect(target), points
            )
            unrelated = [rectangle for cell_id, rectangle in rectangles
                         if cell_id not in {edge.get("source"), edge.get("target")}]
            for start, end in zip(route, route[1:]):
                if any(GenericDrawioDocumentBuilder._segment_hits_box(
                        start, end, rectangle, 8.0) for rectangle in unrelated):
                    errors.append("edge crosses unrelated node: {0}".format(edge.get("id")))
                    break
            label = html.unescape(re.sub(r"<[^>]+>", "", edge.get("value", ""))).strip()
            if label:
                label_box = InternalSvgRenderer._label_layout(
                    route, label,
                    [rectangle for _, rectangle in rectangles] + title_obstacles + label_boxes,
                    canvas,
                )["box"]
                if any(InternalSvgRenderer._rectangles_overlap(label_box, rectangle, 3.0)
                       for _, rectangle in rectangles):
                    errors.append("edge label overlaps node: {0}".format(edge.get("id")))
                if any(InternalSvgRenderer._rectangles_overlap(label_box, other, 4.0)
                       for other in label_boxes):
                    errors.append("edge labels overlap: {0}".format(edge.get("id")))
                x, y, width, height = label_box
                if x < 8 or y < 8 or x + width > canvas[0] - 8 \
                        or y + height > canvas[1] - 8:
                    errors.append("edge label outside canvas: {0}".format(edge.get("id")))
                label_boxes.append(label_box)
        return {"passed": not errors, "errors": errors,
                "vertex_count": len(semantic_vertices), "edge_count": len(edges)}

    def require_valid(self, path: Path) -> dict:
        report = self.inspect(path)
        if not report["passed"]:
            raise DrawioDocumentError("Draw.io validation failed: {0}".format(
                "; ".join(report["errors"])
            ))
        return report


class GenericDrawioDocumentBuilder:
    """Builds evidence-derived manual figures with stable semantic IDs."""

    NODE_STYLES = {
        "component": NODE_STYLE,
        "service": SERVICE_STYLE,
        "module": DOMAIN_STYLE,
        "actor": DOCUMENT_STYLE,
        "external": CONTEXT_STYLE,
        "datastore": PERSISTENCE_STYLE,
        "decision": DECISION_STYLE,
        "process": NODE_STYLE,
    }

    def build(self, figure: dict, output_path: Path) -> dict:
        nodes = [node for node in figure.get("nodes", [])
                 if not node.get("visual_override", {}).get("hidden")]
        visible_keys = {node["key"] for node in nodes}
        edges = [edge for edge in figure.get("edges", [])
                 if edge.get("source") in visible_keys and edge.get("target") in visible_keys]
        if len(nodes) < 2:
            raise DrawioDocumentError("Manual figure requires at least two nodes")
        keys = {node["key"] for node in nodes}
        if len(keys) != len(nodes):
            raise DrawioDocumentError("Manual figure node keys must be unique")
        if any(edge.get("source") not in keys or edge.get("target") not in keys
               for edge in edges):
            raise DrawioDocumentError("Manual figure edge endpoint is missing")
        layout_figure = {**figure, "nodes": nodes, "edges": edges}
        positions, canvas = self._layout(layout_figure)
        positions = DrawioDocumentBuilder._apply_position_overrides(positions, nodes)
        mxfile, root = self._document(figure, canvas)
        ordered_nodes = sorted(enumerate(nodes), key=lambda item: (
            int(item[1].get("layer", 0)), item[0]
        ))
        sequence = {node["key"]: index + 1 for index, (_, node) in enumerate(ordered_nodes)}
        for band in self._layer_bands(layout_figure, positions, canvas):
            cell = ET.SubElement(root, "mxCell", {
                "id": band["key"], "value": "", "vertex": "1", "parent": "1",
                "dataRole": "layer-band",
                "style": "rounded=1;whiteSpace=wrap;html=1;fillColor={0};"
                         "strokeColor=#e2e8f0;strokeWidth=1;arcSize=12;".format(
                             band["fill"]),
            })
            ET.SubElement(cell, "mxGeometry", {
                "x": str(band["x"]), "y": str(band["y"]),
                "width": str(band["width"]), "height": str(band["height"]),
                "as": "geometry",
            })
        for layer in self._layer_labels(layout_figure, positions):
            cell = ET.SubElement(root, "mxCell", {
                "id": layer["key"], "value": html.escape(layer["label"]),
                "vertex": "1", "parent": "1", "dataRole": "layer-label",
                "style": "text;html=1;strokeColor=none;fillColor=none;align=left;"
                         "verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontStyle=1;"
                         "fontColor=#64748b;",
            })
            ET.SubElement(cell, "mxGeometry", {
                "x": str(layer["x"]), "y": str(layer["y"]),
                "width": str(layer["width"]), "height": str(layer["height"]),
                "as": "geometry",
            })
        for node in nodes:
            x, y, width, height = positions[node["key"]]
            base_style = self.NODE_STYLES.get(node.get("kind"), NODE_STYLE)
            if figure.get("figure_type") == "module":
                base_style = base_style.replace("fontSize=13", "fontSize=15")
            cell = ET.SubElement(root, "mxCell", {
                "id": node["key"], "value": self._node_value(
                    node, sequence[node["key"]], figure.get("figure_type", "")),
                "vertex": "1", "parent": "1",
                "dataRole": "semantic-node",
                "dataKind": str(node.get("kind", "component")),
                "dataLayer": str(max(0, int(node.get("layer", 0)))),
                "dataSemanticLabel": str(node.get("label", "")),
                "style": DrawioDocumentBuilder._merge_style(
                    base_style,
                    node.get("visual_override", {}).get("style", {})),
            })
            ET.SubElement(cell, "mxGeometry", {
                "x": str(x), "y": str(y), "width": str(width),
                "height": str(height), "as": "geometry",
            })
        route_points = {}
        routed = []
        routing_order = sorted(enumerate(edges), key=lambda item: self._route_priority(
            item[1], positions, item[0]
        ))
        for route_rank, (index, edge) in enumerate(routing_order):
            points = edge.get("visual_override", {}).get("route", {}).get("points")
            if not isinstance(points, list) or not points:
                points = self._route(edge, positions, canvas, routed, route_rank)
            route_points[index] = points
            routed.append(self._full_route(edge, positions, points))
        placed_label_boxes = []
        label_obstacles = list(positions.values()) + [
            (48.0, 24.0, float(canvas[0]) - 96.0, 40.0)
        ]
        for index, edge in enumerate(edges):
            points = route_points[index]
            display_label = str(edge.get("display_label", edge.get("label", ""))).strip()
            label_suppressed = False
            if display_label:
                label_layout = InternalSvgRenderer._label_layout(
                    self._full_route(edge, positions, points), display_label,
                    label_obstacles + placed_label_boxes, canvas,
                )
                if label_layout["safe"]:
                    placed_label_boxes.append(label_layout["box"])
                else:
                    # A relationship is more important than its annotation.  In
                    # a dense graph there may be no collision-free label lane;
                    # keep the editable edge and original semantic label in XML,
                    # but do not let that optional annotation invalidate the
                    # entire diagram and downstream document workflow.
                    display_label = ""
                    label_suppressed = True
            cell = ET.SubElement(root, "mxCell", {
                "id": edge.get("key") or "edge-{0}".format(index + 1),
                "value": html.escape(display_label),
                "edge": "1",
                "parent": "1", "source": edge["source"], "target": edge["target"],
                "dataRole": "semantic-edge",
                "dataSemanticLabel": str(edge.get("label", "")),
                "dataLabelSuppressed": "true" if label_suppressed else "false",
                "style": DrawioDocumentBuilder._merge_style(
                    EDGE_STYLE + "labelBackgroundColor=#ffffff;",
                    edge.get("visual_override", {}).get("style", {})),
            })
            geometry = ET.SubElement(cell, "mxGeometry", {
                "relative": "1", "as": "geometry", "x": "0", "y": "0",
            })
            if display_label:
                ET.SubElement(geometry, "mxPoint", {
                    "x": "0", "y": "-14", "as": "offset",
                })
            array = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", {"x": str(x), "y": str(y)})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(mxfile).write(output_path, encoding="utf-8", xml_declaration=True)
        return {"node_count": len(nodes), "edge_count": len(edges),
                "canvas": list(canvas),
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}

    @classmethod
    def _route_priority(cls, edge: dict, positions: dict, index: int) -> tuple:
        source = positions[edge["source"]]
        target = positions[edge["target"]]
        source_center = (source[0] + source[2] / 2, source[1] + source[3] / 2)
        target_center = (target[0] + target[2] / 2, target[1] + target[3] / 2)
        unrelated = [rect for key, rect in positions.items()
                     if key not in {edge["source"], edge["target"]}]
        blockers = sum(cls._segment_hits_box(source_center, target_center, rect, 8.0)
                       for rect in unrelated)
        distance = abs(target_center[0] - source_center[0]) + \
            abs(target_center[1] - source_center[1])
        return blockers, distance, index

    @staticmethod
    def _node_value(node: dict, sequence: int, figure_type: str) -> str:
        label = html.escape(node.get("display_label") or node["label"])
        captions = {
            "actor": "操作角色", "component": "界面 / 组件", "service": "接口 / 服务",
            "module": "业务模块", "datastore": "数据存储", "external": "外部服务",
            "decision": "业务判断", "process": "处理步骤",
        }
        caption = captions.get(node.get("kind"), "系统节点")
        if figure_type in {"workflow", "data_flow"}:
            caption = "步骤 {0:02d} · {1}".format(sequence, caption)
        subtitle_size = 11 if figure_type == "module" else 10
        return ("<b>{0}</b><br><font color=\"#64748b\" style=\"font-size:{1}px\">"
                "{2}</font>").format(label, subtitle_size, caption)

    @staticmethod
    def _layer_labels(figure: dict, positions: dict) -> list:
        if figure.get("figure_type") in {"workflow", "data_flow"}:
            return []
        layers = {}
        for node in figure["nodes"]:
            layer = max(0, int(node.get("layer", 0)))
            layers.setdefault(layer, []).append(node)
        names = {
            "architecture": ["界面与访问层", "应用服务层", "数据与外部服务层", "基础设施层"],
            "deployment": ["访问与接入层", "应用服务层", "缓存与会话层", "数据与文件层"],
            "module": ["访问与控制层", "核心业务层", "数据支撑层", "外部协作层"],
        }.get(figure.get("figure_type"))
        if names is None:
            return []
        labels = []
        for order, layer in enumerate(sorted(layers)):
            items = layers[layer]
            top = min(positions[item["key"]][1] for item in items)
            labels.append({
                "key": "layer-label-{0}".format(layer),
                "label": names[order] if order < len(names) else "第 {0} 层".format(order + 1),
                "x": 18, "y": top + 18, "width": 112, "height": 28,
            })
        return labels

    @staticmethod
    def _layer_bands(figure: dict, positions: dict, canvas: tuple) -> list:
        if figure.get("figure_type") not in {"architecture", "module", "deployment"}:
            return []
        layers = {}
        for node in figure["nodes"]:
            layers.setdefault(max(0, int(node.get("layer", 0))), []).append(node)
        fills = ("#f8fafc", "#f5f8fb")
        bands = []
        for order, layer in enumerate(sorted(layers)):
            boxes = [positions[item["key"]] for item in layers[layer]]
            top = min(box[1] for box in boxes) - 18
            bottom = max(box[1] + box[3] for box in boxes) + 18
            bands.append({
                "key": "layer-band-{0}".format(layer), "x": 12, "y": top,
                "width": canvas[0] - 24, "height": bottom - top,
                "fill": fills[order % len(fills)],
            })
        return bands

    @staticmethod
    def _document(figure: dict, canvas: tuple) -> tuple:
        mxfile = ET.Element("mxfile", {
            "host": "app.diagrams.net", "agent": "software-copyright-agent",
            "version": "24.7.17", "type": "device", "compressed": "false",
        })
        page = ET.SubElement(mxfile, "diagram", {
            "id": figure["figure_key"], "name": figure["title"],
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
            "id": "diagram-title", "value": html.escape(figure["title"]),
            "vertex": "1", "parent": "1",
            "style": "text;html=1;strokeColor=none;fillColor=none;align=left;"
                     "verticalAlign=middle;whiteSpace=wrap;fontSize=22;fontStyle=1;"
                     "fontColor=#0f172a;",
        })
        ET.SubElement(title, "mxGeometry", {
            "x": "48", "y": "24", "width": str(canvas[0] - 96),
            "height": "40", "as": "geometry",
        })
        return mxfile, root

    def _layout(self, figure: dict) -> tuple:
        nodes = figure["nodes"]
        layout = figure.get("layout", "layered-vertical")
        layers = {}
        for index, node in enumerate(nodes):
            layer = max(0, int(node.get("layer", index if "flow" in layout else 0)))
            layers.setdefault(layer, []).append(node)
        layers = self._reduce_layer_crossings(figure, layers)
        positions = {}
        # Long single-row workflows become postage stamps when inserted into
        # portrait A4. Keep short flows horizontal, but stack five or more
        # stages vertically so their labels remain readable in the manual.
        use_horizontal = (
            layout in {"flow-left-right", "collaboration-horizontal"}
            and len(nodes) <= 4
            and all(len(items) == 1 for items in layers.values())
        )
        if use_horizontal:
            max_rows = max(len(items) for items in layers.values())
            for column, layer in enumerate(sorted(layers)):
                items = layers[layer]
                for row, node in enumerate(items):
                    positions[node["key"]] = (56 + column * 252, 132 + row * 116, 196, 72)
            canvas = (max(760, 112 + len(layers) * 252), max(344, 178 + max_rows * 116))
        elif figure.get("figure_type") in {"workflow", "data_flow"} \
                and len(layers) >= 4 \
                and max(len(items) for items in layers.values()) <= 3 \
                and any(len(items) > 1 for items in layers.values()):
            # Branching flows are laid out as a portrait-document-friendly ladder.
            # A fixed 912 px canvas gives two- and three-way branches enough room,
            # while the compact layer rhythm prevents a tall strip from being
            # height-limited and printed at postage-stamp size in Word.
            node_width, node_height = 220, 72
            canvas_width, top, row_gap = 912, 82, 124
            for row, layer in enumerate(sorted(layers)):
                items = layers[layer]
                total_width = len(items) * node_width + max(0, len(items) - 1) * 40
                left = (canvas_width - total_width) / 2
                xs = [left + index * (node_width + 40) for index in range(len(items))]
                for node, x in zip(items, xs):
                    positions[node["key"]] = (
                        x, top + row * row_gap, node_width, node_height,
                    )
            rows = len(layers)
            canvas = (canvas_width, top + (rows - 1) * row_gap + node_height + 40)
        elif figure.get("figure_type") in {"workflow", "data_flow"} and len(layers) >= 5 \
                and all(len(items) == 1 for items in layers.values()):
            # A single six-step workflow or data-flow column is too tall and
            # narrow on an A4 page. Lay long sequential figures out as a
            # two-column reading snake:
            # left→right, down, right→left. This preserves step order while
            # materially increasing printed label size and leaving room for prose.
            ordered = [layers[layer][0] for layer in sorted(layers)]
            node_width, node_height = 246, 72
            left_x, right_x, top = 74, 440, 88
            row_gap = 142
            for index, node in enumerate(ordered):
                row = index // 2
                rightward = row % 2 == 0
                column = index % 2 if rightward else 1 - (index % 2)
                positions[node["key"]] = (
                    left_x if column == 0 else right_x,
                    top + row * row_gap,
                    node_width,
                    node_height,
                )
            rows = (len(ordered) + 1) // 2
            canvas = (760, max(420, top + rows * row_gap + 24))
        else:
            # Complex architecture and module layers need a compact three-column
            # grid. A forced two-column grid makes five-node business layers taller
            # than the A4 figure allowance; Word then height-scales the whole image
            # and the labels print smaller than the body text. Three columns keep
            # the canvas closer to landscape and therefore use the available page
            # width. Deployment stays at two columns because its nodes are usually
            # infrastructure pairs with longer labels.
            wrap_limit = {
                "architecture": 3,
                "module": 3,
                "deployment": 2,
            }.get(figure.get("figure_type"))
            layer_layouts = []
            for layer in sorted(layers):
                items = layers[layer]
                columns = min(len(items), wrap_limit or len(items))
                visual_rows = (len(items) + columns - 1) // columns
                layer_layouts.append((layer, items, columns, visual_rows))
            max_columns = max(columns for _, _, columns, _ in layer_layouts)
            show_layer_labels = figure.get("figure_type") not in {"workflow", "data_flow"}
            gutter = 116 if show_layer_labels else 0
            canvas_width = max(760, max_columns * 228 + 112 + gutter)
            node_layers = {
                node["key"]: max(0, int(node.get("layer", 0))) for node in nodes
            }
            top = 88
            for layer, items, columns, visual_rows in layer_layouts:
                bypasses_layer = any(
                    node_layers.get(edge.get("source"), layer) < layer <
                    node_layers.get(edge.get("target"), layer)
                    for edge in figure.get("edges", [])
                )
                for item_index, node in enumerate(items):
                    visual_row = item_index // columns
                    column = item_index % columns
                    row_size = min(columns, len(items) - visual_row * columns)
                    spread_sparse_row = (
                        visual_rows == 1 and bypasses_layer and 1 < row_size < max_columns
                    )
                    if spread_sparse_row:
                        column = round(column * (max_columns - 1) / (row_size - 1))
                        row_size = max_columns
                    total_width = row_size * 196 + max(0, row_size - 1) * 32
                    left = max(56 + gutter,
                               gutter + (canvas_width - gutter - total_width) / 2)
                    positions[node["key"]] = (
                        left + column * 228, top + visual_row * 132, 196, 72
                    )
                top += (visual_rows - 1) * 132 + 152
            canvas = (canvas_width, max(340, top + 20))
        return positions, canvas

    @staticmethod
    def _reduce_layer_crossings(figure: dict, layers: dict) -> dict:
        """Apply a stable barycenter pass before assigning layer coordinates.

        This is the small, deterministic part of the hierarchical layout used by
        diagrams.net-style editors: peers are ordered near the predecessors that
        connect to them.  It materially reduces avoidable crossings without
        changing the AI semantic graph or making layout non-reproducible.
        """
        original = {
            node["key"]: index for index, node in enumerate(figure.get("nodes", []))
        }
        incoming = {}
        outgoing = {}
        for edge in figure.get("edges", []):
            incoming.setdefault(edge.get("target"), []).append(edge.get("source"))
            outgoing.setdefault(edge.get("source"), []).append(edge.get("target"))
        ordered = {layer: list(items) for layer, items in layers.items()}
        layer_numbers = sorted(ordered)
        placed = {}
        for layer in layer_numbers:
            items = ordered[layer]
            def forward_key(node: dict) -> tuple:
                neighbors = [placed[key] for key in incoming.get(node["key"], [])
                             if key in placed]
                return ((sum(neighbors) / len(neighbors)) if neighbors else float("inf"),
                        original[node["key"]])
            items.sort(key=forward_key)
            for index, node in enumerate(items):
                placed[node["key"]] = index
        following = {}
        for layer in reversed(layer_numbers):
            items = ordered[layer]
            def backward_key(item: tuple) -> tuple:
                index, node = item
                neighbors = [following[key] for key in outgoing.get(node["key"], [])
                             if key in following]
                barycenter = sum(neighbors) / len(neighbors) if neighbors else index
                return barycenter, index
            ordered[layer] = [node for _, node in sorted(enumerate(items), key=backward_key)]
            for index, node in enumerate(ordered[layer]):
                following[node["key"]] = index
        return ordered

    @classmethod
    def _route(cls, edge: dict, positions: dict, canvas: tuple,
               routed: list, index: int) -> list:
        grid_route = cls._grid_route(edge, positions, canvas, routed, index)
        if grid_route:
            # Preserve explicit terminal ports as waypoints.  diagrams.net accepts
            # them, and the internal renderers collapse the duplicate endpoints.
            # Keeping them also makes a straight connection an explicit editable
            # route instead of silently falling back to mxGraph auto-routing.
            return grid_route
        return cls._corridor_route(edge, positions, canvas, routed, index)

    @classmethod
    def _corridor_route(cls, edge: dict, positions: dict, canvas: tuple,
                        routed: list, index: int) -> list:
        sx, sy, sw, sh = positions[edge["source"]]
        tx, ty, tw, th = positions[edge["target"]]
        sc, tc = (sx + sw / 2, sy + sh / 2), (tx + tw / 2, ty + th / 2)
        boxes = [value for key, value in positions.items()
                 if key not in {edge["source"], edge["target"]}]
        # Keep long return routes out of the layer-label gutter. Routing through
        # that gutter made labels such as“核心业务层”look crossed out.
        semantic_left = min(value[0] for value in positions.values())
        xs = {max(28.0, semantic_left - 28.0), float(canvas[0]) - 28.0,
              sc[0], tc[0], (sc[0] + tc[0]) / 2}
        ys = {76.0, float(canvas[1]) - 32.0, sc[1], tc[1],
              (sc[1] + tc[1]) / 2}
        for x, y, width, height in positions.values():
            xs.update((max(24.0, x - 18.0), min(canvas[0] - 24.0, x + width + 18.0)))
            ys.update((max(76.0, y - 18.0), min(canvas[1] - 24.0, y + height + 18.0)))
        candidates = []
        if abs(sc[1] - tc[1]) < 1:
            candidates.append([(sc[0], sc[1]), (tc[0], tc[1])])
        if abs(sc[0] - tc[0]) < 1:
            candidates.append([(sc[0], sc[1]), (tc[0], tc[1])])
        candidates.extend([[(sc[0], y), (tc[0], y)] for y in sorted(ys)])
        candidates.extend([[(x, sc[1]), (x, tc[1])] for x in sorted(xs)])
        # A small deterministic offset prevents coincident parallel routes from making
        # labels unreadable while keeping the result stable across regenerations.
        lane = (index % 5 - 2) * 3
        candidates.extend([
            [(sc[0], max(76.0, min(canvas[1] - 24.0, y + lane))),
             (tc[0], max(76.0, min(canvas[1] - 24.0, y + lane)))]
            for y in ((sc[1] + tc[1]) / 2,)
        ])
        return min(candidates, key=lambda points: cls._route_score(
            cls._full_route(edge, positions, points), boxes, routed
        ))

    @classmethod
    def _grid_route(cls, edge: dict, positions: dict, canvas: tuple,
                    routed: list, index: int) -> list:
        """Find a collision-free orthogonal route on an obstacle visibility grid."""
        source = positions[edge["source"]]
        target = positions[edge["target"]]
        clearance, lane_offset = 14.0, 8.0

        def inflate(rect: tuple) -> tuple:
            x, y, width, height = rect
            return (x - clearance, y - clearance,
                    width + clearance * 2, height + clearance * 2)

        obstacles = {key: inflate(rect) for key, rect in positions.items()}
        obstacles["__diagram_title__"] = (36.0, 12.0, float(canvas[0]) - 72.0, 60.0)
        unrelated = [rect for key, rect in positions.items()
                     if key not in {edge["source"], edge["target"]}]

        def ports(rect: tuple) -> list:
            x, y, width, height = rect
            cx, cy = x + width / 2, y + height / 2
            return [
                ((x, cy), (x - clearance - 1, cy), "left"),
                ((x + width, cy), (x + width + clearance + 1, cy), "right"),
                ((cx, y), (cx, y - clearance - 1), "top"),
                ((cx, y + height), (cx, y + height + clearance + 1), "bottom"),
            ]

        source_center = (source[0] + source[2] / 2, source[1] + source[3] / 2)
        target_center = (target[0] + target[2] / 2, target[1] + target[3] / 2)
        routes = []
        for source_port, source_stub, source_side in ports(source):
            for target_port, target_stub, target_side in ports(target):
                if any(cls._segment_hits_box(source_port, source_stub, box, 8.0)
                       for box in unrelated):
                    continue
                if any(cls._segment_hits_box(target_port, target_stub, box, 8.0)
                       for box in unrelated):
                    continue
                middle = cls._visibility_path(
                    source_stub, target_stub, list(obstacles.values()), canvas, routed,
                    lane_offset + (index % 3) * 5.0,
                )
                if not middle:
                    continue
                route = cls._simplify_route([source_port, *middle, target_port])
                if any(cls._segment_hits_box(start, end, box, 8.0)
                       for start, end in zip(route, route[1:]) for box in unrelated):
                    continue
                preferred_source = cls._preferred_side(source_center, target_center)
                preferred_target = cls._preferred_side(target_center, source_center)
                port_penalty = (0 if source_side == preferred_source else 70)
                port_penalty += (0 if target_side == preferred_target else 70)
                routes.append((cls._route_score(route, unrelated, routed) + port_penalty,
                               route))
        return min(routes, key=lambda item: item[0])[1] if routes else []

    @staticmethod
    def _preferred_side(origin: tuple, target: tuple) -> str:
        dx, dy = target[0] - origin[0], target[1] - origin[1]
        if abs(dx) > abs(dy):
            return "right" if dx >= 0 else "left"
        return "bottom" if dy >= 0 else "top"

    @classmethod
    def _visibility_path(cls, start: tuple, end: tuple, obstacles: list,
                         canvas: tuple, routed: list, lane_offset: float) -> list:
        width, height = float(canvas[0]), float(canvas[1])
        xs = {24.0, width - 24.0, start[0], end[0]}
        ys = {76.0, height - 24.0, start[1], end[1]}
        for x, y, box_width, box_height in obstacles:
            xs.update((max(20.0, x - lane_offset), min(width - 20.0,
                                                       x + box_width + lane_offset)))
            ys.update((max(76.0, y - lane_offset), min(height - 20.0,
                                                       y + box_height + lane_offset)))
        xs, ys = sorted(xs), sorted(ys)

        def inside(point: tuple) -> bool:
            return any(x < point[0] < x + box_width and y < point[1] < y + box_height
                       for x, y, box_width, box_height in obstacles)

        points = {(x, y) for x in xs for y in ys if not inside((x, y))}
        points.update((start, end))
        adjacency = {point: [] for point in points}

        def clear(left: tuple, right: tuple) -> bool:
            return not any(cls._segment_hits_box(left, right, box, 0.1)
                           for box in obstacles)

        for x in xs:
            column = sorted((point for point in points if point[0] == x), key=lambda p: p[1])
            for left, right in zip(column, column[1:]):
                if clear(left, right):
                    adjacency[left].append(right); adjacency[right].append(left)
        for y in ys:
            row = sorted((point for point in points if point[1] == y), key=lambda p: p[0])
            for left, right in zip(row, row[1:]):
                if clear(left, right):
                    adjacency[left].append(right); adjacency[right].append(left)

        queue = [(0.0, 0, start, "", [start])]
        best = {(start, ""): 0.0}
        serial = 0
        while queue:
            cost, _, point, direction, path = heapq.heappop(queue)
            if point == end:
                return path
            if cost > best.get((point, direction), float("inf")):
                continue
            for neighbor in adjacency.get(point, []):
                next_direction = "h" if point[1] == neighbor[1] else "v"
                length = abs(neighbor[0] - point[0]) + abs(neighbor[1] - point[1])
                bend = 38.0 if direction and direction != next_direction else 0.0
                crossings = sum(
                    cls._segments_cross(point, neighbor, other_start, other_end)
                    for other in routed
                    for other_start, other_end in zip(other, other[1:])
                )
                # Proper edge crossings are harder to repair by hand than a longer
                # route.  Make the visibility search take an outer gutter whenever
                # one exists instead of accepting a visually confusing shortcut.
                next_cost = cost + length + bend + crossings * 50_000.0
                state = (neighbor, next_direction)
                if next_cost >= best.get(state, float("inf")):
                    continue
                best[state] = next_cost
                serial += 1
                heapq.heappush(queue, (next_cost, serial, neighbor,
                                       next_direction, [*path, neighbor]))
        return []

    @staticmethod
    def _simplify_route(route: list) -> list:
        result = []
        for point in route:
            point = (float(point[0]), float(point[1]))
            if result and point == result[-1]:
                continue
            result.append(point)
            while len(result) >= 3:
                first, middle, last = result[-3:]
                if (first[0] == middle[0] == last[0]) or \
                        (first[1] == middle[1] == last[1]):
                    result.pop(-2)
                else:
                    break
        return result

    @staticmethod
    def _boundary(rect: tuple, toward: tuple) -> tuple:
        x, y, width, height = rect
        cx, cy = x + width / 2, y + height / 2
        dx, dy = toward[0] - cx, toward[1] - cy
        if abs(dx) * height >= abs(dy) * width:
            return (x + width if dx >= 0 else x, cy)
        return (cx, y + height if dy >= 0 else y)

    @classmethod
    def _full_route(cls, edge: dict, positions: dict, points: list) -> list:
        return InternalSvgRenderer._orthogonal_route(
            positions[edge["source"]], positions[edge["target"]],
            [tuple(point) for point in points],
        )

    @classmethod
    def _route_score(cls, route: list, boxes: list, routed: list) -> float:
        segments = list(zip(route, route[1:]))
        collisions = sum(
            cls._segment_hits_box(start, end, box, 11.0)
            for start, end in segments for box in boxes
        )
        crossings = sum(
            cls._segments_cross(start, end, other_start, other_end)
            for start, end in segments
            for other in routed for other_start, other_end in zip(other, other[1:])
        )
        overlaps = sum(
            cls._segments_overlap(start, end, other_start, other_end)
            for start, end in segments
            for other in routed for other_start, other_end in zip(other, other[1:])
        )
        length = sum(abs(end[0] - start[0]) + abs(end[1] - start[1])
                     for start, end in segments)
        bends = sum(1 for first, second in zip(segments, segments[1:])
                    if (first[0][0] == first[1][0]) != (second[0][0] == second[1][0]))
        return (collisions * 2_000_000 + crossings * 1_000_000 + overlaps * 80_000
                + length + bends * 20)

    @staticmethod
    def _segment_hits_box(start: tuple, end: tuple, rect: tuple, clearance: float) -> bool:
        x, y, width, height = rect
        left, right = x - clearance, x + width + clearance
        top, bottom = y - clearance, y + height + clearance
        if abs(start[0] - end[0]) < 0.01:
            return (left <= start[0] <= right and
                    max(min(start[1], end[1]), top) < min(max(start[1], end[1]), bottom))
        if abs(start[1] - end[1]) < 0.01:
            return (top <= start[1] <= bottom and
                    max(min(start[0], end[0]), left) < min(max(start[0], end[0]), right))
        return False

    @staticmethod
    def _segments_cross(a: tuple, b: tuple, c: tuple, d: tuple) -> bool:
        def orientation(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (orientation(a, b, c) * orientation(a, b, d) < 0 and
                orientation(c, d, a) * orientation(c, d, b) < 0)

    @staticmethod
    def _segments_overlap(a: tuple, b: tuple, c: tuple, d: tuple) -> bool:
        if abs(a[0] - b[0]) < 0.01 and abs(c[0] - d[0]) < 0.01:
            if abs(a[0] - c[0]) >= 0.01:
                return False
            return max(min(a[1], b[1]), min(c[1], d[1])) + 8 < min(
                max(a[1], b[1]), max(c[1], d[1]))
        if abs(a[1] - b[1]) < 0.01 and abs(c[1] - d[1]) < 0.01:
            if abs(a[1] - c[1]) >= 0.01:
                return False
            return max(min(a[0], b[0]), min(c[0], d[0])) + 8 < min(
                max(a[0], b[0]), max(c[0], d[0]))
        return False


class InternalPngRenderer:
    """High-resolution Word-compatible renderer for deterministic Draw.io XML."""

    def __init__(self, scale: int = 3) -> None:
        self.scale = max(1, scale)

    def render(self, drawio_path: Path, png_path: Path) -> dict:
        source = ET.parse(drawio_path).getroot()
        model = source.find(".//mxGraphModel")
        width, height = int(float(model.get("pageWidth"))), int(float(model.get("pageHeight")))
        image = Image.new("RGB", (width * self.scale, height * self.scale), "white")
        draw = ImageDraw.Draw(image)
        cells = source.findall(".//mxCell")
        vertices = {cell.get("id"): cell for cell in cells if cell.get("vertex") == "1"}
        for cell in (item for item in vertices.values()
                     if item.get("dataRole") == "layer-band"):
            self._vertex(draw, cell)
        placed_label_boxes = []
        for edge in (cell for cell in cells if cell.get("edge") == "1"):
            self._edge(draw, edge, vertices, (width, height), placed_label_boxes)
        for cell in (item for item in vertices.values()
                     if item.get("dataRole") != "layer-band"):
            self._vertex(draw, cell)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(png_path, format="PNG", optimize=True)
        return {"width": image.width, "height": image.height,
                "size_bytes": png_path.stat().st_size,
                "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest()}

    def _vertex(self, draw: ImageDraw.ImageDraw, cell: ET.Element) -> None:
        x, y, width, height = InternalSvgRenderer._rect(cell)
        style = InternalSvgRenderer._style(cell.get("style", ""))
        box = tuple(int(value * self.scale) for value in (x, y, x + width, y + height))
        is_title = cell.get("id") == "diagram-title"
        is_layer_label = cell.get("dataRole") == "layer-label"
        is_layer_band = cell.get("dataRole") == "layer-band"
        if is_layer_band:
            draw.rounded_rectangle(box, radius=12 * self.scale,
                                   fill=style.get("fillColor", "#f8fafc"),
                                   outline=style.get("strokeColor", "#e2e8f0"),
                                   width=max(1, self.scale))
        elif not is_title and not is_layer_label:
            outline = style.get("strokeColor", "#64748b")
            line_width = max(2, int(float(style.get("strokeWidth", "1.5")) * self.scale))
            if cell.get("style", "").startswith("rhombus;"):
                draw.polygon([
                    (int((x + width / 2) * self.scale), int(y * self.scale)),
                    (int((x + width) * self.scale), int((y + height / 2) * self.scale)),
                    (int((x + width / 2) * self.scale), int((y + height) * self.scale)),
                    (int(x * self.scale), int((y + height / 2) * self.scale)),
                ], fill=style.get("fillColor", "#ffffff"), outline=outline,
                    width=line_width)
            else:
                if style.get("shadow") == "1":
                    shadow = tuple(int(value * self.scale) for value in
                                   (x + 3, y + 4, x + width + 3, y + height + 4))
                    draw.rounded_rectangle(shadow, radius=10 * self.scale,
                                           fill="#e2e8f0")
                draw.rounded_rectangle(box, radius=10 * self.scale,
                                       fill=style.get("fillColor", "#ffffff"),
                                       outline=outline, width=line_width)
        label = "\n".join(InternalSvgRenderer._text_lines(cell.get("value", "")))
        if not label:
            return
        size = int(float(style.get("fontSize", "12")) * self.scale)
        font = self._font(size)
        anchor = "la" if is_title or is_layer_label else "mm"
        point = (int(x * self.scale), int((y + height / 2) * self.scale)) if anchor == "la" else (
            int((x + width / 2) * self.scale), int((y + height / 2) * self.scale))
        draw.multiline_text(point, label, font=font, fill=style.get("fontColor", "#0f172a"),
                            anchor=anchor, align="center", spacing=4 * self.scale)

    def _edge(self, draw: ImageDraw.ImageDraw, cell: ET.Element, vertices: dict,
              canvas: tuple, placed_label_boxes: list = None) -> None:
        source_rect = InternalSvgRenderer._rect(vertices[cell.get("source")])
        target_rect = InternalSvgRenderer._rect(vertices[cell.get("target")])
        geometry = cell.find("mxGeometry")
        points = [(float(item.get("x")), float(item.get("y")))
                  for item in geometry.findall("./Array[@as='points']/mxPoint")]
        route = InternalSvgRenderer._orthogonal_route(source_rect, target_rect, points)
        scaled = [(int(x * self.scale), int(y * self.scale)) for x, y in route]
        draw.line(scaled, fill="#64748b", width=3 * self.scale, joint="curve")
        if len(scaled) >= 2:
            draw.polygon(self._arrow_polygon(scaled[-2], scaled[-1], self.scale),
                         fill="#64748b")
        label = html.unescape(re.sub(r"<[^>]+>", "", cell.get("value", ""))).strip()
        if label and scaled:
            obstacles = [InternalSvgRenderer._rect(vertex) for key, vertex in vertices.items()
                         if vertex.get("dataRole") not in {
                             "layer-label", "layer-band"}]
            obstacles.extend(placed_label_boxes or [])
            layout = InternalSvgRenderer._label_layout(
                route, label,
                obstacles,
                canvas,
            )
            left, top, width, height = layout["box"]
            if placed_label_boxes is not None:
                placed_label_boxes.append(layout["box"])
            x = int((left + width / 2) * self.scale)
            y = int((top + height / 2) * self.scale)
            font = self._font(11 * self.scale)
            box = tuple(int(value * self.scale) for value in
                        (left, top, left + width, top + height))
            draw.rounded_rectangle(box, radius=5 * self.scale, fill="white")
            draw.text((x, y), label, font=font, fill="#475569", anchor="mm")

    @staticmethod
    def _font(size: int):
        bundled = Path(__file__).parent / "assets" / "fonts" / "noto-cjk" / "NotoSansCJKsc-Regular.otf"
        try:
            return ImageFont.truetype(str(bundled), size=size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _arrow_polygon(start: tuple, end: tuple, scale: int = 1) -> list:
        x0, y0 = start
        x, y = end
        dx, dy = x - x0, y - y0
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / length, dy / length
        arrow_length, arrow_width = 8 * scale, 5 * scale
        bx, by = x - ux * arrow_length, y - uy * arrow_length
        px, py = -uy * arrow_width, ux * arrow_width
        return [(round(x), round(y)), (round(bx + px), round(by + py)),
                (round(bx - px), round(by - py))]


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
        for cell in (item for item in vertices.values()
                     if item.get("dataRole") == "layer-band"):
            self._draw_vertex(svg, cell)
        placed_label_boxes = []
        for cell in (item for item in cells if item.get("edge") == "1"):
            self._draw_edge(svg, cell, vertices, (width, height), placed_label_boxes)
        for cell in (item for item in vertices.values()
                     if item.get("dataRole") != "layer-band"):
            self._draw_vertex(svg, cell)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(svg).write(svg_path, encoding="utf-8", xml_declaration=True)
        if not svg_path.is_file() or svg_path.stat().st_size == 0:
            raise DrawioDocumentError("Internal SVG export produced no output")

    def _draw_edge(self, svg: ET.Element, cell: ET.Element, vertices: dict,
                   canvas: tuple, placed_label_boxes: list = None) -> None:
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
        edge_group = ET.SubElement(svg, "g", {
            "data-edge-key": cell.get("id", ""),
            "data-edge-source": cell.get("source", ""),
            "data-edge-target": cell.get("target", ""),
        })
        attributes = {
            "points": " ".join("{0},{1}".format(self._number(x), self._number(y))
                               for x, y in route),
            "fill": "none", "stroke": stroke,
            "stroke-width": style.get("strokeWidth", "1.5"),
            "stroke-linejoin": "round", "marker-end": "url(#{0})".format(marker_id),
        }
        if style.get("dashed") == "1":
            attributes["stroke-dasharray"] = "7 5"
        ET.SubElement(edge_group, "polyline", attributes)
        label = html.unescape(re.sub(r"<[^>]+>", "", cell.get("value", ""))).strip()
        if label and route:
            obstacles = [self._rect(vertex) for key, vertex in vertices.items()
                         if vertex.get("dataRole") not in {
                             "layer-label", "layer-band"}]
            obstacles.extend(placed_label_boxes or [])
            layout = self._label_layout(
                route, label,
                obstacles,
                canvas,
            )
            x, y, width, height = layout["box"]
            if placed_label_boxes is not None:
                placed_label_boxes.append(layout["box"])
            label_group = ET.SubElement(edge_group, "g", {
                "data-edge-label": "true",
                "data-center-x": self._number(x + width / 2),
                "data-center-y": self._number(y + height / 2),
            })
            ET.SubElement(label_group, "rect", {
                "x": self._number(x), "y": self._number(y),
                "width": self._number(width), "height": self._number(height),
                "rx": "5", "fill": "#ffffff", "fill-opacity": "0.96",
            })
            text = ET.SubElement(label_group, "text", {
                "x": self._number(x + width / 2), "y": self._number(y + 14),
                "text-anchor": "middle",
                "font-family": "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif",
                "font-size": "11", "fill": "#475569",
            })
            text.text = label

    @staticmethod
    def _visual_text_width(value: str) -> float:
        columns = sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
                      for character in value)
        return max(52.0, min(220.0, columns * 6.2 + 18.0))

    @staticmethod
    def _rectangles_overlap(left: tuple, right: tuple, clearance: float = 0.0) -> bool:
        lx, ly, lw, lh = left
        rx, ry, rw, rh = right
        return (lx < rx + rw + clearance and lx + lw > rx - clearance and
                ly < ry + rh + clearance and ly + lh > ry - clearance)

    @classmethod
    def _label_layout(cls, route: List[tuple], label: str, rectangles: List[tuple],
                      canvas: tuple = (100000.0, 100000.0)) -> dict:
        """Choose a quiet segment and keep the opaque label outside every node.

        The previous renderer placed labels on the middle route vertex.  That vertex
        is often a node boundary or a bend, which produced visually valid XML with
        unreadable labels.  Candidate boxes are now scored against all node geometry.
        """
        width, height = cls._visual_text_width(label), 24.0
        canvas_width, canvas_height = canvas
        candidates = []
        for segment_index, (start, end) in enumerate(zip(route, route[1:])):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = abs(dx) + abs(dy)
            if length < 8:
                continue
            for fraction in (0.5, 0.35, 0.65):
                mx, my = start[0] + dx * fraction, start[1] + dy * fraction
                # Dense branch and return diagrams often have no safe label
                # position at the old fixed offset. Search progressively wider
                # lanes along the same segment so a local retry can actually
                # recover instead of reproducing the identical collision.
                boxes = []
                for distance in (10.0, 34.0, 58.0, 82.0, 106.0, 130.0):
                    if abs(dx) >= abs(dy):
                        boxes.extend((
                            ((mx - width / 2, my - height - distance, width, height),
                             distance),
                            ((mx - width / 2, my + distance, width, height), distance),
                        ))
                    else:
                        boxes.extend((
                            ((mx + distance, my - height / 2, width, height), distance),
                            ((mx - width - distance, my - height / 2, width, height),
                             distance),
                        ))
                for side_index, (box, distance) in enumerate(boxes):
                    x, y, box_width, box_height = box
                    overlaps = sum(cls._rectangles_overlap(box, rect, 6.0)
                                   for rect in rectangles)
                    outside = max(0.0, 8.0 - x) + max(0.0, 8.0 - y)
                    outside += max(0.0, x + box_width + 8.0 - canvas_width)
                    outside += max(0.0, y + box_height + 8.0 - canvas_height)
                    # Prefer a long, central segment once collision and bounds are safe.
                    score = overlaps * 1_000_000 + outside * 100_000 - length
                    score += distance * 0.2 + abs(fraction - 0.5) * 30
                    score += side_index * 0.1 + segment_index * 0.01
                    candidates.append((score, box))
        if not candidates:
            point = route[0] if route else (24.0, 24.0)
            box = cls._clamp_label_box(
                (point[0] + 12, point[1] + 12, width, height), canvas
            )
        else:
            box = cls._clamp_label_box(
                min(candidates, key=lambda item: item[0])[1], canvas
            )
        x, y, box_width, box_height = box
        safe = not any(cls._rectangles_overlap(box, rectangle, 6.0)
                       for rectangle in rectangles)
        safe = safe and x >= 8.0 and y >= 8.0 \
            and x + box_width <= canvas_width - 8.0 \
            and y + box_height <= canvas_height - 8.0
        return {"box": box, "safe": safe}

    @staticmethod
    def _clamp_label_box(box: tuple, canvas: tuple, margin: float = 8.0) -> tuple:
        """Keep even a no-quiet-segment fallback label inside the page.

        Very short terminal routes can have no regular label candidate. The old
        fallback blindly moved down/right from the endpoint and could place a
        valid edge label beyond the bottom canvas, making every local retry fail
        with identical geometry.
        """
        x, y, width, height = box
        canvas_width, canvas_height = canvas
        return (
            min(max(margin, x), max(margin, canvas_width - margin - width)),
            min(max(margin, y), max(margin, canvas_height - margin - height)),
            width, height,
        )

    def _draw_vertex(self, svg: ET.Element, cell: ET.Element) -> None:
        x, y, width, height = self._rect(cell)
        style = self._style(cell.get("style", ""))
        parent = svg
        is_title = cell.get("id") == "diagram-title"
        is_layer_label = cell.get("dataRole") == "layer-label"
        is_layer_band = cell.get("dataRole") == "layer-band"
        if is_layer_band:
            ET.SubElement(svg, "rect", {
                "x": self._number(x), "y": self._number(y),
                "width": self._number(width), "height": self._number(height),
                "rx": "12", "fill": style.get("fillColor", "#f8fafc"),
                "stroke": style.get("strokeColor", "#e2e8f0"),
                "stroke-width": style.get("strokeWidth", "1"),
            })
        elif not is_title and not is_layer_label:
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
            if style.get("shadow") == "1" and not cell.get("style", "").startswith("rhombus;"):
                ET.SubElement(parent, "rect", {
                    "x": self._number(x + 3), "y": self._number(y + 4),
                    "width": self._number(width), "height": self._number(height),
                    "rx": "10", "fill": "#0f172a", "fill-opacity": ".08",
                })
            if cell.get("style", "").startswith("rhombus;"):
                ET.SubElement(parent, "polygon", {
                    "points": "{0},{1} {2},{3} {4},{5} {6},{7}".format(
                        self._number(x + width / 2), self._number(y),
                        self._number(x + width), self._number(y + height / 2),
                        self._number(x + width / 2), self._number(y + height),
                        self._number(x), self._number(y + height / 2)),
                    "fill": attributes["fill"], "stroke": attributes["stroke"],
                    "stroke-width": attributes["stroke-width"],
                })
            else:
                ET.SubElement(parent, "rect", attributes)
        lines = self._text_lines(cell.get("value", ""))
        if not lines:
            return
        font_size = float(style.get("fontSize", "12"))
        text = ET.SubElement(parent, "text", {
            "x": self._number(x + (0 if is_title or is_layer_label else width / 2)),
            "y": self._number(y + (font_size if is_title or is_layer_label else
                                     height / 2 - (len(lines) - 1) * 8 + 4)),
            "text-anchor": "start" if is_title or is_layer_label else "middle",
            "font-family": "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif",
            "font-size": self._number(font_size),
            "font-weight": "700" if is_title or is_layer_label else "600",
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
