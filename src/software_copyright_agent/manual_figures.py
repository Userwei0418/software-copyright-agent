import base64
import binascii
import hashlib
import io
import json
import os
import re
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Callable, Optional
from uuid import uuid4

from PIL import Image

from .app_settings import AppSettingsService, style_prompt
from .credential_vault import CredentialVault
from .diagram_asset import DiagramAssetError, DiagramOverlayEngine
from .drawio_document import (
    DrawioDocumentError, DrawioDocumentInspector, GenericDrawioDocumentBuilder,
    InternalPngRenderer, InternalSvgRenderer,
)
from .manual_generation import ManualGenerationError, ManualGenerationService
from .manual_execution import ManualExecutionNodeService, manual_job_slot
from .service import utc_now
from .storage import Database


class ManualFigureError(ValueError):
    pass


class FigureGenerationFailure(ManualFigureError):
    def __init__(self, message: str, *, category: str, stage: str,
                 retryable: bool, attempt: int = 1,
                 semantic: Optional[dict] = None) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.retryable = retryable
        self.attempt = attempt
        self.semantic = semantic


class EditorOperationParseError(ManualFigureError):
    """The provider response could not be normalized into operation JSON."""


# One initial semantic JSON request plus at most one structural repair.  Any
# remaining failure is retained as an independently retryable figure node.
MAX_FIGURE_ATTEMPTS = 2
MAX_EDITOR_XML_BYTES = 4 * 1024 * 1024
MAX_EDITOR_SVG_BYTES = 6 * 1024 * 1024
MAX_EDITOR_PNG_BYTES = 10 * 1024 * 1024
MAX_EDITOR_PIXELS = 80_000_000
DRAWIO_AI_PROMPT_VERSION = "drawio-xml-editor-v4"
MAX_DRAWIO_AI_CONTEXTS = 64
MAX_DRAWIO_AI_HISTORY_ITEMS = 8
MAX_DRAWIO_AI_MANIFEST_CELLS = 180
DRAWIO_AI_ALLOWED_ACTIONS = {
    "node.move", "node.resize", "node.style", "node.label", "node.hide",
    "edge.route", "edge.style", "edge.label",
}


_READER_LABEL_OVERRIDES = {
    "userauthpage": "用户认证页面",
    "waterfullpage": "首页瀑布流页面",
    "waterfallpage": "首页瀑布流页面",
    "loginuserstore": "登录用户状态",
    "userloginbysessionusingpost": "用户登录接口",
    "dosearch": "搜索与筛选处理",
    "dressentity": "服饰信息",
    "dress实体": "服饰信息",
}
_READER_TOKEN_LABELS = {
    "user": "用户", "auth": "认证", "login": "登录", "session": "会话",
    "search": "搜索", "review": "审核", "upload": "上传", "picture": "图片",
    "image": "图片", "model": "模型", "water": "瀑布", "full": "流",
    "waterfall": "瀑布流", "dress": "服饰", "task": "任务", "project": "项目",
    "document": "文档", "manual": "说明书", "diagram": "图表", "source": "源码",
    "asset": "资产", "config": "配置", "setting": "设置", "settings": "设置",
    "page": "页面", "controller": "控制器", "service": "服务",
    "store": "状态", "entity": "信息", "repository": "数据访问",
    "manager": "管理", "handler": "处理", "process": "处理", "processor": "处理",
    "create": "创建", "update": "更新", "delete": "删除", "list": "列表",
    "detail": "详情", "export": "导出", "generate": "生成", "preview": "预览",
}
_READER_IGNORED_TOKENS = {
    "do", "by", "using", "use", "api", "post", "get", "put", "patch", "http",
}
_READER_TECH_LABELS = {
    "mysql", "redis", "sqlite", "cos", "vue", "vue.js", "react", "react.js",
    "node.js", "spring boot", "fastapi", "draw.io", "docker", "nginx",
}


def _reader_label(label: str, kind: str = "component") -> str:
    """Return a concise reader-facing label while retaining raw evidence elsewhere."""
    original = (label or "").strip()
    if not original or original.lower() in _READER_TECH_LABELS:
        return original
    if original.lower() in _READER_LABEL_OVERRIDES:
        return _READER_LABEL_OVERRIDES[original.lower()]
    # Already-readable Chinese labels should not be mechanically rewritten.
    if re.search(r"[\u4e00-\u9fff]", original) and not re.search(
        r"[A-Za-z][A-Za-z0-9]*(?:Page|Store|Service|Controller|Entity)", original
    ):
        return original

    leaf = original.strip("/").split("/")[-1]
    compact = re.sub(r"[^a-z0-9]", "", leaf.lower())
    if compact in _READER_LABEL_OVERRIDES:
        return _READER_LABEL_OVERRIDES[compact]

    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", leaf)
    words = re.sub(r"[^A-Za-z0-9]+", " ", words).lower().split()
    translated, unknown = [], []
    for word in words:
        if word in _READER_IGNORED_TOKENS:
            continue
        mapped = _READER_TOKEN_LABELS.get(word)
        if mapped:
            translated.append(mapped)
        else:
            unknown.append(word)
    if not translated or unknown:
        return original
    result = "".join(translated)
    if original.startswith("/") and not result.endswith("接口"):
        result += "接口"
    elif kind in {"service", "external"} and not result.endswith(("服务", "接口")):
        result += "服务"
    return result or original


class ManualFigureService:
    """Turns section figure requests into editable, evidence-bound figure assets."""

    def __init__(self, database: Database, data_root: Path,
                 model_call: Optional[Callable] = None,
                 model_stream_call: Optional[Callable] = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._vault = CredentialVault(database, data_root)
        generation = ManualGenerationService(database, data_root)
        self._model_call = model_call or generation._call_model
        self._model_stream_call = model_stream_call or generation._call_model_stream
        self._builder = GenericDrawioDocumentBuilder()
        self._inspector = DrawioDocumentInspector()
        self._svg = InternalSvgRenderer()
        # Keep the Draw.io canvas compact for legibility while exporting a
        # high-resolution PNG suitable for Word/WPS insertion.
        self._png = InternalPngRenderer(scale=3)
        self._overlay = DiagramOverlayEngine()
        # The sidecar owns a small, bounded context cache.  Each entry is
        # isolated by job, figure and the fingerprint of the live editor XML;
        # a manual canvas edit therefore starts a fresh context automatically.
        self._editor_ai_cache = OrderedDict()
        self._editor_ai_cache_lock = Lock()

    def generate_all(self, job_id: str) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        sections = self._sections(job_id)
        requests = self._requests(sections)
        step_id = self._start_step(job_id)
        generated, errors = [], []
        concurrency = AppSettingsService(self._database).effective_concurrency(
            context["model_id"]
        )
        state_lock = Lock()
        execution = ManualExecutionNodeService(self._database)
        completed = 0
        states = {
            request["figure_key"]: {
                "key": request["figure_key"], "title": request["title"],
                "status": "queued", "attempt": 0, "started_at": None,
                "finished_at": None, "error": None, "stage": "model",
            } for request in requests
        }
        for request in requests:
            execution.prepare(
                job_id, "figure:{0}".format(request["figure_key"]), "diagrams", "figure",
                request["title"],
                dependencies=["section:{0}".format(request["section_key"])],
                model_config_id=context["model_id"], max_attempts=MAX_FIGURE_ATTEMPTS,
                input_value={"figure_key": request["figure_key"],
                             "section_key": request["section_key"],
                             "figure_type": request["figure_type"]},
            )

        def publish(current_title: str) -> None:
            self._update_progress(
                job_id, step_id, completed, len(requests), current_title,
                list(states.values()), concurrency,
            )

        def generate_one(request: dict) -> dict:
            node_key = "figure:{0}".format(request["figure_key"])
            with manual_job_slot(job_id, concurrency):
                execution.running(job_id, node_key, 1)
                with state_lock:
                    states[request["figure_key"]].update(
                        status="running", attempt=1, started_at=utc_now(), error=None,
                    )
                    publish(request["title"])
                return self._generate(
                    context, request,
                    on_retry=lambda attempt, reason: execution.heartbeat(
                        job_id, node_key, attempt, reason
                    ),
                )

        publish("准备并发生成图表")
        with ThreadPoolExecutor(max_workers=concurrency,
                                thread_name_prefix="manual-figure") as executor:
            futures = {executor.submit(generate_one, request): request
                       for request in requests}
            for future in as_completed(futures):
                request = futures[future]
                detail = None
                try:
                    result = future.result()
                    generated.append(result)
                    execution.complete(
                        job_id, "figure:{0}".format(request["figure_key"]),
                        {"version": result["version"], "elapsed_ms": result["elapsed_ms"],
                         "drawio_relative_path": result["drawio_relative_path"],
                         "svg_relative_path": result["svg_relative_path"],
                         "png_relative_path": result["png_relative_path"],
                         "next_action": "查看或编辑图表资产"},
                    )
                except Exception as error:
                    detail = self._failure_detail(request, error)
                    errors.append(detail)
                    self._record_failed(context, request, error)
                    execution.fail(
                        job_id, "figure:{0}".format(request["figure_key"]),
                        detail["message"], detail["category"],
                    )
                with state_lock:
                    completed += 1
                    states[request["figure_key"]].update(
                        status="failed" if detail else "completed",
                        attempt=detail.get("attempt", 1) if detail else 1,
                        stage=detail.get("stage", "complete") if detail else "complete",
                        finished_at=utc_now(),
                        error=detail.get("message") if detail else None,
                    )
                    publish("已完成 {0}/{1} 张图".format(completed, len(requests)))
        self._finish_step(job_id, step_id, generated, errors)
        if not generated and requests:
            raise ManualFigureError(errors[0]["message"])
        return {"job_id": job_id,
                "status": "completed_with_warnings" if errors else "completed",
                "generated": generated, "errors": errors, "figures": self.list(job_id)}

    def begin_incremental(self, job_id: str) -> dict:
        """Open the diagram stage without creating an all-sections barrier."""
        self._database.initialize()
        return {"step_id": self._start_step(job_id, update_job=False)}

    def generate_for_section(self, job_id: str, section_key: str) -> dict:
        """Generate only requests owned by one completed chapter."""
        context = self._context(job_id)
        sections = [item for item in self._sections(job_id)
                    if item["section_key"] == section_key]
        requests = self._requests(sections)
        generated, errors = [], []
        concurrency = AppSettingsService(self._database).effective_concurrency(
            context["model_id"]
        )
        execution = ManualExecutionNodeService(self._database)
        for request in requests:
            node_key = "figure:{0}".format(request["figure_key"])
            execution.prepare(
                job_id, node_key, "diagrams", "figure", request["title"],
                dependencies=["section:{0}".format(request["section_key"])],
                model_config_id=context["model_id"], max_attempts=MAX_FIGURE_ATTEMPTS,
                input_value={"figure_key": request["figure_key"],
                             "section_key": request["section_key"],
                             "figure_type": request["figure_type"]},
            )
            try:
                with manual_job_slot(job_id, concurrency):
                    execution.running(job_id, node_key, 1)
                    result = self._generate(
                        context, request,
                        on_retry=lambda attempt, reason=None, key=node_key:
                            execution.heartbeat(job_id, key, attempt, reason),
                    )
                generated.append(result)
                execution.complete(job_id, node_key, {
                    "version": result["version"], "elapsed_ms": result["elapsed_ms"],
                    "drawio_relative_path": result["drawio_relative_path"],
                    "svg_relative_path": result["svg_relative_path"],
                    "png_relative_path": result["png_relative_path"],
                    "next_action": "查看或编辑图表资产",
                })
            except Exception as error:
                detail = self._failure_detail(request, error)
                errors.append(detail)
                self._record_failed(context, request, error)
                execution.fail(job_id, node_key, detail["message"], detail["category"])
        return {"generated": generated, "errors": errors}

    def finish_incremental(self, job_id: str, stream: dict, results: list) -> dict:
        generated = [item for result in results for item in result.get("generated", [])]
        errors = [item for result in results for item in result.get("errors", [])]
        self._finish_step(job_id, stream["step_id"], generated, errors)
        return {"job_id": job_id,
                "status": "completed_with_warnings" if errors else "completed",
                "generated": generated, "errors": errors, "figures": self.list(job_id)}

    def regenerate(self, job_id: str, figure_key: str) -> dict:
        context = self._context(job_id)
        request = next((item for item in self._requests(self._sections(job_id))
                        if item["figure_key"] == figure_key), None)
        if request is None:
            raise ManualFigureError("图表请求不存在")
        execution = ManualExecutionNodeService(self._database)
        node_key = "figure:{0}".format(figure_key)
        current_node = next(
            (item for item in execution.list(job_id) if item["key"] == node_key), None
        )
        first_attempt = (current_node["attempt"] if current_node else 0) + 1
        execution.prepare(
            job_id, node_key, "diagrams", "figure", request["title"],
            dependencies=["section:{0}".format(request["section_key"])],
            model_config_id=context["model_id"],
            max_attempts=first_attempt + MAX_FIGURE_ATTEMPTS - 1,
            input_value={"figure_key": figure_key, "section_key": request["section_key"],
                         "figure_type": request["figure_type"]},
        )
        concurrency = AppSettingsService(self._database).effective_concurrency(
            context["model_id"]
        )
        execution.queued(job_id, node_key)
        with self._database.connect() as connection:
            failed = connection.execute(
                """SELECT semantic_json, qa_json FROM manual_figure_artifacts
                WHERE job_id=? AND figure_key=? AND status='failed'""",
                (job_id, figure_key),
            ).fetchone()
        try:
            with manual_job_slot(job_id, concurrency):
                execution.running(job_id, node_key, first_attempt)
                if failed is not None:
                    semantic = json.loads(failed["semantic_json"] or "{}")
                    qa = json.loads(failed["qa_json"] or "{}")
                    if (qa.get("stage") in {"drawio", "render"}
                            and isinstance(semantic.get("nodes"), list)
                            and semantic["nodes"]):
                        source = {**request, "semantic": semantic,
                                  "evidence_refs": semantic.get("evidence_refs", [])}
                        result = self._persist_revision(
                            context, source, semantic, "ai_generation", 0, [], [],
                        )
                        execution.complete(job_id, node_key, {
                            "version": result["version"], "local_retry": True,
                            "next_action": "查看或编辑图表资产",
                        })
                        return result
                fallback = self._deterministic_semantic(request)
                if fallback is not None and failed is not None:
                    qa = json.loads(failed["qa_json"] or "{}")
                    if qa.get("category") == "semantic_validation":
                        source = {**request, "semantic": fallback,
                                  "evidence_refs": fallback["evidence_refs"]}
                        result = self._persist_revision(
                            context, source, fallback, "ai_generation", 0, [], [],
                        )
                        execution.complete(job_id, node_key, {
                            "version": result["version"], "local_fallback": True,
                            "next_action": "查看或编辑图表资产",
                        })
                        return result
                result = self._generate(
                    context, request,
                    on_retry=lambda attempt, reason: execution.heartbeat(
                        job_id, node_key, first_attempt + attempt - 1, reason
                    ),
                )
            execution.complete(job_id, node_key, {
                "version": result["version"], "elapsed_ms": result["elapsed_ms"],
                "next_action": "查看或编辑图表资产",
            })
            return result
        except Exception as error:
            detail = self._failure_detail(request, error)
            execution.fail(job_id, node_key, detail["message"], detail["category"])
            raise

    @staticmethod
    def _deterministic_semantic(request: dict) -> Optional[dict]:
        """Build a conservative architecture graph from explicit chapter facts.

        This is used only after the model repeatedly returns nodes without any
        valid relationship. It extracts named technologies from the already
        generated evidence-bound chapter instead of asking the same model again.
        """
        if request.get("figure_type") != "architecture":
            return None
        text = json.dumps(request.get("section_blocks", []), ensure_ascii=False)
        definitions = [
            ("user", "用户终端", "actor", 0, ("用户", "终端")),
            ("frontend", "Vue 3 前端应用", "component", 1, ("Vue 3", "TypeScript")),
            ("web", "Nginx / Web 接入", "component", 1, ("Nginx", "Web服务器")),
            ("backend", "Spring Boot 后端服务", "service", 1, ("Spring Boot",)),
            ("mysql", "MySQL 主数据库", "datastore", 2, ("MySQL",)),
            ("redis", "Redis 缓存与会话", "datastore", 2, ("Redis",)),
            ("cos", "腾讯云 COS 对象存储", "external", 2, ("COS", "对象存储")),
        ]
        refs = list(request.get("section_evidence_refs", []))
        nodes = [{"key": key, "label": label, "kind": kind, "layer": layer,
                  "evidence_refs": refs[:2]}
                 for key, label, kind, layer, markers in definitions
                 if any(marker in text for marker in markers)]
        keys = {item["key"] for item in nodes}
        edge_specs = [
            ("access", "user", "web", "HTTPS 访问"),
            ("static", "web", "frontend", "静态资源"),
            ("api", "frontend", "backend", "JSON API"),
            ("persist", "backend", "mysql", "业务数据"),
            ("cache", "backend", "redis", "缓存会话"),
            ("media", "backend", "cos", "预签名地址"),
        ]
        edges = [{"key": key, "source": source, "target": target,
                  "label": label, "evidence_refs": refs[:2]}
                 for key, source, target, label in edge_specs
                 if source in keys and target in keys]
        if len(nodes) < 3 or not edges:
            return None
        return {"figure_key": request["figure_key"], "title": request["title"],
                "figure_type": request["figure_type"], "layout": "layered-vertical",
                "nodes": nodes, "edges": edges, "evidence_refs": refs}

    def create_revision(self, job_id: str, figure_key: str, operations: list,
                        edit_source: str = "manual") -> dict:
        if edit_source not in {"manual", "ai"}:
            raise ManualFigureError("图表修改来源无效")
        context = self._context(job_id)
        current = self._current(job_id, figure_key)
        try:
            result = self._overlay.prepare(current["semantic"], operations)
        except DiagramAssetError as error:
            raise ManualFigureError(str(error)) from error
        if result.conflicts:
            raise ManualFigureError("图表已经变化，请刷新后重新执行修改")
        return self._persist_revision(
            context, current, result.diagram, edit_source, 0,
            list(result.operations), list(result.conflicts),
        )

    def save_editor_revision(self, job_id: str, figure_key: str, xml: str,
                             svg: str, png: str) -> dict:
        """Persist a lossless revision returned by the official Draw.io editor.

        The embedded editor owns advanced shapes, grouping and connector styling.
        We therefore save its XML and rendered SVG/PNG as one atomic revision instead
        of rebuilding the result from the application's smaller semantic schema.
        """
        context = self._context(job_id)
        current = self._current(job_id, figure_key)
        xml_bytes = self._validate_editor_xml(xml)
        svg_bytes = self._validate_editor_svg(svg)
        png_bytes, png_summary = self._validate_editor_png(png)
        return self._persist_editor_assets(
            context, current, xml_bytes, svg_bytes, png_bytes, png_summary,
            operation={"action": "drawio.full_edit", "target": figure_key,
                       "payload": {"editor": "embed.diagrams.net"}},
        )

    def ai_preview(self, job_id: str, figure_key: str, instruction: str) -> dict:
        result = self._ai_operations(job_id, figure_key, instruction)
        operations = result["operations"]
        prepared = result.pop("prepared")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawio, svg = root / "preview.drawio", root / "preview.svg"
            try:
                self._builder.build(prepared.diagram, drawio)
                self._inspector.require_valid(drawio)
                self._svg.render(drawio, svg)
            except (DrawioDocumentError, TypeError, ValueError) as error:
                raise ManualFigureError("模型返回的图表操作参数无效") from error
            preview_svg = svg.read_text(encoding="utf-8")
        return {"figure_key": figure_key, "edit_source": "ai",
                "operations": operations, "preview_svg": preview_svg,
                "elapsed_ms": result["elapsed_ms"], "model_name": result["model_name"]}

    def ai_patch_editor_xml(self, job_id: str, figure_key: str,
                            instruction: str, xml: str,
                            model_config_id: Optional[str] = None,
                            on_event: Optional[Callable] = None) -> dict:
        """Apply AI-approved local operations to the live Draw.io XML.

        The model still returns only the short operation JSON.  The application
        patches matching semantic cells in the current official-editor XML, so
        manually added layers, groups, shapes and styles remain intact.
        """
        emit = on_event or (lambda event: None)
        emit({"type": "phase", "phase": "xml", "message": "正在解析当前 Draw.io XML"})
        xml_bytes = self._validate_editor_xml(xml)
        result = self._ai_operations(
            job_id, figure_key, instruction, xml_bytes,
            model_config_id=model_config_id,
            on_delta=lambda text: emit({"type": "delta", "text": text}),
            on_phase=lambda phase, message: emit(
                {"type": "phase", "phase": phase, "message": message}
            ),
            stream=on_event is not None,
        )
        emit({"type": "phase", "phase": "validate", "message": "正在校验并回写 XML"})
        patched = self._apply_editor_operations(xml_bytes, result["operations"])
        self._remember_editor_ai_context(
            job_id, figure_key, result["model_config_id"], xml_bytes, patched,
            instruction, result["operations"]
        )
        return {"figure_key": figure_key, "edit_source": "ai",
                "operations": result["operations"], "xml": patched.decode("utf-8"),
                "elapsed_ms": result["elapsed_ms"], "model_name": result["model_name"],
                "model_config_id": result["model_config_id"],
                "prompt_version": DRAWIO_AI_PROMPT_VERSION,
                "context_cache_hit": result["context_cache_hit"]}

    def _ai_operations(self, job_id: str, figure_key: str, instruction: str,
                       editor_xml: Optional[bytes] = None,
                       model_config_id: Optional[str] = None,
                       on_delta: Optional[Callable] = None,
                       on_phase: Optional[Callable] = None,
                       stream: bool = False) -> dict:
        if not instruction.strip():
            raise ManualFigureError("请输入具体的图表修改要求")
        context = self._context(job_id, model_config_id)
        current = self._current(job_id, figure_key)
        if editor_xml:
            targets = self._editor_xml_manifest(editor_xml)
            history = self._editor_ai_history(
                job_id, figure_key, context["model_id"], editor_xml
            )
        else:
            targets = [{"id": item["key"], "value": item.get("label", ""),
                        "kind": "edge" if kind == "edges" else "node"}
                       for kind in ("nodes", "edges")
                       for item in current["semantic"].get(kind, [])]
            history = []
        allowed_actions, operation_limit = self._editor_operation_policy(instruction)
        allowed_action_text = "、".join(sorted(allowed_actions))
        prompt = """[系统预设版本：{0}]
你是只服务于软件说明书 Draw.io 的资深信息架构师和排版工程师。
当前编辑器 XML 是唯一事实基线；必须依据 XML 清单中的真实 id、文字、父子关系、
源/目标、坐标、尺寸、样式与折点提出局部修改。保留用户手工新增的图层、分组、节点、
连线和未知样式；不要回退到首次生成语义，不得凭空新增、删除或整体重画。
优先改善阅读顺序、对齐、间距、字号层级、颜色一致性与连线避让；未被用户要求的内容不动。
只返回 JSON，第一个字符必须是 {{，最后一个字符必须是 }}：
{{"operations":[{{"action":"白名单动作","target":"现有 key","payload":{{}}}}]}}。
不得输出解释、思考过程、Markdown 代码围栏或 JSON 之外的任何文字。
本次白名单动作仅限 {5}；不得返回白名单以外的动作。
颜色使用 #RRGGBB；node.move 使用绝对 x/y；node.resize 使用 width/height；改名使用 value；
样式仅使用 fillColor、strokeColor、fontColor、rounded、dashed、strokeWidth、fontStyle；
edge.route 使用正交折点 points:[{{"x":数值,"y":数值}}]，每条线最多 4 个折点；
整理连线时不得移动、缩放或改名节点，不得让连线穿过无关节点，优先减少交叉与折返。
不得新增或删除语义节点；本次最多 {6} 项。
当前 XML 指纹：{1}
当前 XML 单元清单：{2}
同一 XML 上下文的最近对话：{3}
用户要求：{4}""".format(
            DRAWIO_AI_PROMPT_VERSION,
            self._editor_xml_fingerprint(editor_xml) if editor_xml else "semantic-preview",
            json.dumps(targets, ensure_ascii=False, separators=(",", ":")),
            json.dumps(history, ensure_ascii=False, separators=(",", ":")),
            instruction.strip(),
            allowed_action_text,
            operation_limit,
        )
        api_key = None if context["protocol_id"] == "ollama" else self._vault.read(
            context["credential_ref"] or context["model_id"]
        )
        started = time.monotonic()

        def call_model(call_prompt: str) -> str:
            if stream:
                return self._model_stream_call(
                    context["model"], context["endpoint_mode"], api_key, call_prompt,
                    on_delta or (lambda text: None),
                )
            return self._model_call(
                context["model"], context["endpoint_mode"], api_key, call_prompt
            )

        def parse_and_validate(raw_response: str):
            payload = (self._parse_editor_payload(raw_response)
                       if editor_xml else self._parse(raw_response))
            operations = payload.get("operations", [])
            if not isinstance(operations, list) or not operations:
                raise ManualFigureError("模型没有返回可应用的 Draw.io 修改操作")
            if editor_xml:
                if len(operations) > operation_limit:
                    raise ManualFigureError(
                        "模型一次返回的 Draw.io 修改过多（最多 {0} 项）".format(
                            operation_limit
                        )
                    )
                return None, self._validate_editor_operations(
                    operations, targets, allowed_actions
                )
            try:
                prepared_result = self._overlay.prepare(
                    current["semantic"], operations[:12]
                )
            except DiagramAssetError as error:
                raise ManualFigureError(
                    "模型返回了不安全的图表操作：{0}".format(error)
                ) from error
            if prepared_result.conflicts:
                raise ManualFigureError("模型修改引用了已经变化的图表目标")
            return prepared_result, list(prepared_result.operations)

        try:
            raw = call_model(prompt)
        except ManualGenerationError as error:
            raise ManualFigureError(str(error)) from error
        try:
            prepared, normalized = parse_and_validate(raw)
        except EditorOperationParseError as first_error:
            if not editor_xml:
                raise
            if on_phase:
                on_phase("repair", "首次返回格式无法解析，正在仅修复一次 JSON")
            repair_prompt = self._editor_operation_repair_prompt(
                instruction, targets, raw, str(first_error),
                allowed_actions, operation_limit,
            )
            try:
                repaired_raw = call_model(repair_prompt)
                prepared, normalized = parse_and_validate(repaired_raw)
            except ManualGenerationError as error:
                raise ManualFigureError(str(error)) from error
            except EditorOperationParseError as second_error:
                raise ManualFigureError(
                    "模型连续两次未返回可应用的 Draw.io 操作 JSON；"
                    "请换一个模型，或将修改要求拆成更小的一步后重试"
                ) from second_error
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return {"prepared": prepared, "operations": normalized, "elapsed_ms": elapsed_ms,
                "model_name": context["model_name"], "model_config_id": context["model_id"],
                "context_cache_hit": bool(history)}

    @classmethod
    def _parse_editor_payload(cls, raw: str) -> dict:
        """Accept common provider wrappers while keeping operations JSON constrained."""
        text = (raw or "").lstrip("\ufeff").strip()
        candidates = [match.group(1).strip() for match in re.finditer(
            r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE
        )]
        candidates.extend(cls._balanced_json_fragments(text))
        candidates.append(text)
        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                value = json.loads(candidate)
                if isinstance(value, str):
                    value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, list):
                return cls._normalize_editor_payload({"operations": value})
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("operations"), list):
                return cls._normalize_editor_payload(value)
            for key in ("actions", "changes", "edits"):
                if isinstance(value.get(key), list):
                    return cls._normalize_editor_payload(
                        {"operations": value[key]}
                    )
            for key in ("result", "data", "output"):
                nested = value.get(key)
                if isinstance(nested, dict):
                    for operation_key in ("operations", "actions", "changes", "edits"):
                        if isinstance(nested.get(operation_key), list):
                            return cls._normalize_editor_payload(
                                {"operations": nested[operation_key]}
                            )
            if any(key in value for key in ("action", "op", "type")):
                return cls._normalize_editor_payload({"operations": [value]})
        raise EditorOperationParseError("模型返回的 Draw.io 操作 JSON 无法解析")

    @staticmethod
    def _normalize_editor_payload(payload: dict) -> dict:
        aliases = {
            "move_node": "node.move", "node_move": "node.move",
            "resize_node": "node.resize", "node_resize": "node.resize",
            "style_node": "node.style", "node_style": "node.style",
            "rename_node": "node.label", "label_node": "node.label",
            "hide_node": "node.hide", "node_hide": "node.hide",
            "route_edge": "edge.route", "edge_route": "edge.route",
            "style_edge": "edge.style", "edge_style": "edge.style",
            "rename_edge": "edge.label", "label_edge": "edge.label",
        }
        normalized = []
        for item in payload.get("operations", []):
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            action = item.get("action", item.get("op", item.get("type")))
            canonical = str(action or "").strip().lower().replace("-", "_")
            action = aliases.get(canonical, action)
            target = item.get("target", item.get("id", item.get("cell_id")))
            operation_payload = item.get(
                "payload", item.get("params", item.get("data", {}))
            )
            normalized.append({"action": action, "target": target,
                               "payload": operation_payload})
        return {"operations": normalized}

    @staticmethod
    def _balanced_json_fragments(text: str) -> list:
        fragments, start, stack = [], None, []
        in_string, escaped = False, False
        pairs = {"{": "}", "[": "]"}
        for index, character in enumerate(text):
            if start is None:
                if character in pairs:
                    start, stack = index, [pairs[character]]
                    in_string = escaped = False
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in pairs:
                stack.append(pairs[character])
            elif stack and character == stack[-1]:
                stack.pop()
                if not stack:
                    fragments.append(text[start:index + 1])
                    start = None
        return fragments

    @staticmethod
    def _editor_operation_policy(instruction: str) -> tuple:
        """Constrain line-only requests so the model cannot rearrange the canvas."""
        text = (instruction or "").lower()
        route_request = re.search(
            r"连线|线条|线路|走线|折线|路径|交叉|重叠|connector|routing|\bedge\b",
            text,
        )
        node_request = re.search(
            r"节点|模块|方框|框图|位置|移动|尺寸|大小|间距|对齐|布局|层级|"
            r"结构|重排|重新整理|重新布局|层次|条理|整体|"
            r"\bnode\b|\bmodule\b|\blayout\b",
            text,
        )
        if route_request and not node_request:
            return {"edge.route", "edge.style", "edge.label"}, 6
        return set(DRAWIO_AI_ALLOWED_ACTIONS), 12

    @staticmethod
    def _editor_operation_repair_prompt(instruction: str, targets: list,
                                        raw: str, reason: str,
                                        allowed_actions: set,
                                        operation_limit: int) -> str:
        target_kinds = [{"id": item["id"], "kind": item["kind"]}
                        for item in targets]
        return """你是 Draw.io 局部操作 JSON 格式修复器。这是本次唯一一次修复。
仅输出一个 JSON 对象，不得输出解释、代码围栏或思考过程：
{{"operations":[{{"action":"白名单动作","target":"现有 id","payload":{{}}}}]}}
本次白名单：{4}。
节点动作只能指向 node，连线动作只能指向 edge，最多 {5} 项。
可用目标：{0}
用户要求：{1}
首次失败原因：{2}
首次原始输出：{3}
""".format(
            json.dumps(target_kinds, ensure_ascii=False, separators=(",", ":")),
            instruction.strip()[:2000], reason[:500], (raw or "")[:12000],
            "、".join(sorted(allowed_actions)), operation_limit,
        )

    @staticmethod
    def _editor_xml_fingerprint(xml_bytes: bytes) -> str:
        return hashlib.sha256(xml_bytes).hexdigest()

    @classmethod
    def _editor_xml_manifest(cls, xml_bytes: bytes) -> list:
        root = ET.fromstring(xml_bytes)
        manifest = []
        for cell in root.findall(".//mxCell"):
            cell_id = cell.get("id")
            is_edge, is_vertex = cell.get("edge") == "1", cell.get("vertex") == "1"
            if not cell_id or not (is_edge or is_vertex):
                continue
            current = {"id": cell_id, "kind": "edge" if is_edge else "node",
                       "value": re.sub(r"<[^>]+>", "", cell.get("value", ""))[:160],
                       "parent": cell.get("parent", ""),
                       "style": cell.get("style", "")[:360]}
            if is_edge:
                current.update({"source": cell.get("source", ""),
                                "target": cell.get("target", "")})
            geometry = cell.find("mxGeometry")
            if geometry is not None:
                for key in ("x", "y", "width", "height"):
                    if geometry.get(key) is not None:
                        current[key] = geometry.get(key)
                points = geometry.findall("./Array[@as='points']/mxPoint")[:12]
                if points:
                    current["points"] = [{"x": item.get("x"), "y": item.get("y")}
                                         for item in points]
            manifest.append(current)
            if len(manifest) >= MAX_DRAWIO_AI_MANIFEST_CELLS:
                break
        return manifest

    @staticmethod
    def _validate_editor_operations(operations: list, manifest: list,
                                    allowed_actions: Optional[set] = None) -> list:
        targets = {item["id"]: item for item in manifest}
        allowed = allowed_actions or DRAWIO_AI_ALLOWED_ACTIONS
        normalized = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise ManualFigureError("模型返回了不安全的图表操作")
            action, target = operation.get("action"), operation.get("target")
            payload = operation.get("payload", {})
            if action not in allowed:
                raise ManualFigureError("模型返回了超出本次请求范围的 Draw.io 动作")
            if target not in targets:
                raise ManualFigureError("模型修改引用了当前 XML 中不存在的目标")
            if not isinstance(payload, dict):
                raise ManualFigureError("模型返回的图表操作参数无效")
            expected_kind = "edge" if action.startswith("edge.") else "node"
            if targets[target]["kind"] != expected_kind:
                raise ManualFigureError("模型返回的动作与 Draw.io 单元类型不匹配")
            if (action == "edge.route" and allowed != DRAWIO_AI_ALLOWED_ACTIONS
                    and (not isinstance(payload.get("points"), list)
                         or len(payload["points"]) > 4)):
                raise ManualFigureError("整理连线时每条路径最多允许 4 个正交折点")
            normalized.append({"action": action, "target": target, "payload": payload})
        return normalized

    def _editor_ai_history(self, job_id: str, figure_key: str, model_config_id: str,
                           xml_bytes: bytes) -> list:
        key = (job_id, figure_key, model_config_id,
               self._editor_xml_fingerprint(xml_bytes),
               DRAWIO_AI_PROMPT_VERSION)
        with self._editor_ai_cache_lock:
            history = list(self._editor_ai_cache.get(key, []))
            if key in self._editor_ai_cache:
                self._editor_ai_cache.move_to_end(key)
        return history

    def _remember_editor_ai_context(self, job_id: str, figure_key: str,
                                    model_config_id: str,
                                    source_xml: bytes, patched_xml: bytes,
                                    instruction: str, operations: list) -> None:
        source_key = (job_id, figure_key, model_config_id,
                      self._editor_xml_fingerprint(source_xml),
                      DRAWIO_AI_PROMPT_VERSION)
        patched_key = (job_id, figure_key, model_config_id,
                       self._editor_xml_fingerprint(patched_xml),
                       DRAWIO_AI_PROMPT_VERSION)
        with self._editor_ai_cache_lock:
            history = list(self._editor_ai_cache.get(source_key, []))
            history.extend([
                {"role": "user", "content": instruction.strip()[:2000]},
                {"role": "assistant", "content": {"operations": operations}},
            ])
            history = history[-MAX_DRAWIO_AI_HISTORY_ITEMS:]
            self._editor_ai_cache[source_key] = history
            self._editor_ai_cache[patched_key] = history
            self._editor_ai_cache.move_to_end(source_key)
            self._editor_ai_cache.move_to_end(patched_key)
            while len(self._editor_ai_cache) > MAX_DRAWIO_AI_CONTEXTS:
                self._editor_ai_cache.popitem(last=False)

    @classmethod
    def _apply_editor_operations(cls, xml_bytes: bytes, operations: list) -> bytes:
        root = ET.fromstring(xml_bytes)
        cells = {cell.get("id"): cell for cell in root.findall(".//mxCell")
                 if cell.get("id")}
        for operation in operations:
            target = cells.get(operation["target"])
            if target is None:
                raise ManualFigureError(
                    "当前 Draw.io 图中已找不到 AI 修改目标：{0}".format(operation["target"])
                )
            action = operation["action"]
            payload = operation.get("payload") or {}
            if action in {"node.label", "edge.label"}:
                value = str(payload.get("value", "")).strip()
                if not value or len(value) > 120:
                    raise ManualFigureError("AI 返回的图表文字无效")
                target.set("value", value)
            elif action == "node.move":
                geometry = target.find("mxGeometry")
                if geometry is None:
                    raise ManualFigureError("AI 修改目标缺少位置数据")
                geometry.set("x", cls._bounded_number(payload.get("x"), -10000, 10000))
                geometry.set("y", cls._bounded_number(payload.get("y"), -10000, 10000))
            elif action == "node.resize":
                geometry = target.find("mxGeometry")
                if geometry is None:
                    raise ManualFigureError("AI 修改目标缺少尺寸数据")
                geometry.set("width", cls._bounded_number(payload.get("width"), 20, 5000))
                geometry.set("height", cls._bounded_number(payload.get("height"), 20, 5000))
            elif action in {"node.style", "edge.style"}:
                target.set("style", cls._merge_editor_style(
                    target.get("style", ""), payload
                ))
            elif action == "node.hide":
                visible = not bool(payload.get("value", True))
                target.set("visible", "1" if visible else "0")
            elif action == "edge.route":
                cls._replace_editor_route(target, payload.get("points"))
            else:
                raise ManualFigureError("AI 返回了不支持的 Draw.io 修改动作")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _bounded_number(value: object, minimum: float, maximum: float) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ManualFigureError("AI 返回的图表坐标或尺寸无效") from error
        if number < minimum or number > maximum:
            raise ManualFigureError("AI 返回的图表坐标或尺寸超出范围")
        return str(round(number, 2)).rstrip("0").rstrip(".")

    @classmethod
    def _merge_editor_style(cls, current: str, payload: dict) -> str:
        if not isinstance(payload, dict):
            raise ManualFigureError("AI 返回的图表样式无效")
        allowed = {"fillColor", "strokeColor", "fontColor", "rounded", "dashed",
                   "strokeWidth", "fontStyle"}
        styles = {}
        for item in current.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                styles[key] = value
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key.endswith("Color"):
                value = str(value).lower()
                if not re.fullmatch(r"#[0-9a-f]{6}", value):
                    raise ManualFigureError("AI 返回的图表颜色无效")
            elif key in {"rounded", "dashed"}:
                value = "1" if bool(value) else "0"
            elif key == "strokeWidth":
                value = cls._bounded_number(value, 0.5, 8)
            elif key == "fontStyle":
                value = cls._bounded_number(value, 0, 7)
            styles[key] = str(value)
        return ";".join("{0}={1}".format(key, value) for key, value in styles.items()) + ";"

    @classmethod
    def _replace_editor_route(cls, target: ET.Element, points: object) -> None:
        if not isinstance(points, list) or len(points) > 12:
            raise ManualFigureError("AI 返回的连线路径无效")
        geometry = target.find("mxGeometry")
        if geometry is None:
            raise ManualFigureError("AI 修改目标缺少连线路径")
        array = geometry.find("Array[@as='points']")
        if array is None:
            array = ET.SubElement(geometry, "Array", {"as": "points"})
        for child in list(array):
            array.remove(child)
        for point in points:
            if not isinstance(point, dict):
                raise ManualFigureError("AI 返回的连线路径无效")
            ET.SubElement(array, "mxPoint", {
                "x": cls._bounded_number(point.get("x"), -10000, 10000),
                "y": cls._bounded_number(point.get("y"), -10000, 10000),
            })

    def revisions(self, job_id: str, figure_key: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id, version, edit_source, parent_revision_id, operations_json,
                semantic_fingerprint, revision_status, model_name, elapsed_ms, created_at
                FROM manual_figure_revisions WHERE job_id=? AND figure_key=?
                ORDER BY version DESC""", (job_id, figure_key),
            ).fetchall()
        return [{"revision_id": row["id"], "version": row["version"],
                 "edit_source": row["edit_source"],
                 "parent_revision_id": row["parent_revision_id"],
                 "operations": json.loads(row["operations_json"]),
                 "operation_count": len(json.loads(row["operations_json"])),
                 "semantic_fingerprint": row["semantic_fingerprint"],
                 "status": row["revision_status"], "model_name": row["model_name"],
                 "elapsed_ms": row["elapsed_ms"], "created_at": row["created_at"]}
                for row in rows]

    def rollback(self, job_id: str, figure_key: str, version: int) -> dict:
        context = self._context(job_id)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT section_key, title, figure_type, semantic_json,
                evidence_refs_json, drawio_relative_path, svg_relative_path,
                png_relative_path FROM manual_figure_revisions
                WHERE job_id=? AND figure_key=? AND version=?""",
                (job_id, figure_key, version),
            ).fetchone()
        if row is None:
            raise ManualFigureError("指定的图表历史版本不存在")
        source = {"figure_key": figure_key, "section_key": row["section_key"],
                  "title": row["title"], "figure_type": row["figure_type"],
                  "semantic": json.loads(row["semantic_json"]),
                  "evidence_refs": json.loads(row["evidence_refs_json"])}
        task_root = (self._data_root / "tasks" / context["task_id"]).resolve()
        paths = []
        for key in ("drawio_relative_path", "svg_relative_path", "png_relative_path"):
            path = (task_root / row[key]).resolve()
            if task_root not in path.parents or not path.is_file():
                raise ManualFigureError("图表历史版本资产缺失")
            paths.append(path)
        xml_bytes = self._validate_editor_xml(paths[0].read_text(encoding="utf-8"))
        svg_bytes = self._validate_editor_svg(paths[1].read_text(encoding="utf-8"))
        png_value = "data:image/png;base64," + base64.b64encode(
            paths[2].read_bytes()).decode("ascii")
        png_bytes, png_summary = self._validate_editor_png(png_value)
        return self._persist_editor_assets(
            context, source, xml_bytes, svg_bytes, png_bytes, png_summary,
            operation={"action": "drawio.rollback", "target": figure_key,
                       "payload": {"version": version}},
        )

    def list(self, job_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT mfa.*, COALESCE(MAX(mfr.version), 0) revision_version
                FROM manual_figure_artifacts mfa
                LEFT JOIN manual_figure_revisions mfr ON mfr.job_id = mfa.job_id
                    AND mfr.figure_key = mfa.figure_key
                WHERE mfa.job_id = ? GROUP BY mfa.id ORDER BY mfa.section_key, mfa.figure_key""",
                (job_id,),
            ).fetchall()
        requested_keys = {
            item["figure_key"] for item in self._requests(self._sections(job_id))
        }
        if requested_keys:
            rows = [row for row in rows if row["figure_key"] in requested_keys]
        items = []
        for row in rows:
            semantic = json.loads(row["semantic_json"] or "{}")
            if not isinstance(semantic, dict):
                semantic = {}
            semantic.setdefault("figure_key", row["figure_key"])
            semantic.setdefault("title", row["title"])
            semantic.setdefault("figure_type", row["figure_type"])
            semantic.setdefault("layout", "")
            semantic["nodes"] = semantic.get("nodes") if isinstance(semantic.get("nodes"), list) else []
            semantic["edges"] = semantic.get("edges") if isinstance(semantic.get("edges"), list) else []
            available = bool(row["revision_version"] and row["status"] in {"rendered", "verified"}
                             and row["svg_relative_path"] and row["drawio_relative_path"])
            qa = json.loads(row["qa_json"] or "{}")
            items.append({"id": row["id"], "figure_key": row["figure_key"],
                 "section_key": row["section_key"], "figure_type": row["figure_type"],
                 "title": row["title"], "status": row["status"],
                 "available": available, "version": row["revision_version"],
                 "semantic": semantic,
                 "drawio_relative_path": row["drawio_relative_path"],
                 "svg_relative_path": row["svg_relative_path"],
                 "png_relative_path": row["png_relative_path"],
                 "qa": qa, "editor_managed": bool(qa.get("editor_managed")),
                 "error": qa.get("error") or qa.get("message") or "",
                 "updated_at": row["updated_at"]})
        return items

    @staticmethod
    def _decode_editor_data(value: str, media_type: str, maximum: int) -> bytes:
        if not isinstance(value, str) or not value:
            raise ManualFigureError("完整 Draw.io 编辑结果缺少 {0}".format(media_type))
        if value.startswith("data:"):
            header, separator, body = value.partition(",")
            if not separator or media_type not in header.lower():
                raise ManualFigureError("Draw.io 返回的 {0} 格式无效".format(media_type))
            try:
                payload = (base64.b64decode(body, validate=True)
                           if ";base64" in header.lower()
                           else urllib.parse.unquote_to_bytes(body))
            except (ValueError, binascii.Error) as error:
                raise ManualFigureError("Draw.io 返回的 {0} 无法解码".format(
                    media_type)) from error
        else:
            payload = value.encode("utf-8")
        if not payload or len(payload) > maximum:
            raise ManualFigureError("Draw.io 返回的 {0} 超出安全大小限制".format(
                media_type))
        return payload

    @classmethod
    def _validate_editor_xml(cls, value: str) -> bytes:
        payload = cls._decode_editor_data(value, "xml", MAX_EDITOR_XML_BYTES)
        lowered = payload[:4096].lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise ManualFigureError("Draw.io XML 不允许 DOCTYPE 或外部实体")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise ManualFigureError("Draw.io XML 结构无效") from error
        root_name = root.tag.rsplit("}", 1)[-1]
        if root_name not in {"mxfile", "mxGraphModel"}:
            raise ManualFigureError("Draw.io XML 根节点无效")
        models = [root] if root_name == "mxGraphModel" else root.findall(".//mxGraphModel")
        if not models:
            raise ManualFigureError("Draw.io XML 必须使用未压缩的 mxGraphModel")
        cells = root.findall(".//mxCell")
        identifiers = {cell.get("id") for cell in cells}
        if "0" not in identifiers or "1" not in identifiers:
            raise ManualFigureError("Draw.io XML 缺少必要的根图层")
        return payload

    @classmethod
    def _validate_editor_svg(cls, value: str) -> bytes:
        payload = cls._decode_editor_data(value, "svg", MAX_EDITOR_SVG_BYTES)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise ManualFigureError("Draw.io SVG 结构无效") from error
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise ManualFigureError("Draw.io SVG 根节点无效")
        for element in root.iter():
            name = element.tag.rsplit("}", 1)[-1].lower()
            if name == "script":
                raise ManualFigureError("Draw.io SVG 不允许脚本")
            for key, item in element.attrib.items():
                local_key = key.rsplit("}", 1)[-1].lower()
                lowered = item.strip().lower()
                if local_key.startswith("on") or lowered.startswith("javascript:"):
                    raise ManualFigureError("Draw.io SVG 包含不安全的交互属性")
        return payload

    @classmethod
    def _validate_editor_png(cls, value: str) -> tuple:
        payload = cls._decode_editor_data(value, "image/png", MAX_EDITOR_PNG_BYTES)
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                image_format = image.format
        except Exception as error:
            raise ManualFigureError("Draw.io PNG 图像无效") from error
        if image_format != "PNG" or width < 16 or height < 16:
            raise ManualFigureError("Draw.io PNG 图像格式或尺寸无效")
        if width * height > MAX_EDITOR_PIXELS:
            raise ManualFigureError("Draw.io PNG 图像像素超出安全限制")
        return payload, {"width": width, "height": height,
                         "size_bytes": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest()}

    def _persist_editor_assets(self, context: dict, source: dict, xml_bytes: bytes,
                               svg_bytes: bytes, png_bytes: bytes, png_summary: dict,
                               operation: dict) -> dict:
        with self._database.connect() as connection:
            current = connection.execute(
                """SELECT id, version FROM manual_figure_revisions
                WHERE job_id=? AND figure_key=? ORDER BY version DESC LIMIT 1""",
                (context["job_id"], source["figure_key"]),
            ).fetchone()
        version = (current["version"] if current else 0) + 1
        parent_revision_id = current["id"] if current else None
        relative_root = (Path("artifacts") / "manual" / "jobs" /
                         "job-v{0}".format(context["job_version"]) / "diagrams" /
                         source["figure_key"])
        stem = "v{0}".format(version)
        drawio_rel, svg_rel, png_rel = (relative_root / (stem + suffix)
                                        for suffix in (".drawio", ".svg", ".png"))
        task_root = self._data_root / "tasks" / context["task_id"]
        paths = [task_root / item for item in (drawio_rel, svg_rel, png_rel)]
        try:
            for path, payload in zip(paths, (xml_bytes, svg_bytes, png_bytes)):
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix="drawio-editor-", suffix=".tmp", dir=str(path.parent))
                os.close(descriptor)
                temporary = Path(temporary_name)
                try:
                    temporary.write_bytes(payload)
                    os.replace(str(temporary), str(path))
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise
        except Exception as error:
            self._discard_partial_assets(*paths)
            raise ManualFigureError("Draw.io 编辑结果写入失败") from error

        semantic = source["semantic"]
        refs = source.get("evidence_refs") or semantic.get("evidence_refs", [])
        now = utc_now()
        qa = {
            "editor_managed": True,
            "structure": {"passed": True, "editor": "embed.diagrams.net",
                          "sha256": hashlib.sha256(xml_bytes).hexdigest()},
            "png": png_summary,
            "visual_review": "pending_final_document_qa",
        }
        semantic_json = json.dumps(semantic, ensure_ascii=False, separators=(",", ":"))
        refs_json = json.dumps(refs, ensure_ascii=False, separators=(",", ":"))
        qa_json = json.dumps(qa, ensure_ascii=False, separators=(",", ":"))
        operations = [operation]
        operations_json = json.dumps(operations, ensure_ascii=False, separators=(",", ":"))
        revision_id = str(uuid4())
        fingerprint = self._overlay.semantic_fingerprint(semantic)
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_figure_revisions(id, job_id, figure_key, version,
                section_key, title, figure_type, semantic_json, evidence_refs_json,
                drawio_relative_path, svg_relative_path, png_relative_path, qa_json,
                model_name, elapsed_ms, created_at, edit_source, parent_revision_id,
                operations_json, semantic_fingerprint, revision_status) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'manual', ?, ?, ?, 'clean')""",
                (revision_id, context["job_id"], source["figure_key"], version,
                 source["section_key"], source["title"], source["figure_type"],
                 semantic_json, refs_json, drawio_rel.as_posix(), svg_rel.as_posix(),
                 png_rel.as_posix(), qa_json, context["model_name"], now,
                 parent_revision_id, operations_json, fingerprint),
            )
            connection.execute(
                """UPDATE manual_figure_artifacts SET status='rendered',
                semantic_json=?, drawio_relative_path=?, svg_relative_path=?,
                png_relative_path=?, qa_json=?, updated_at=?
                WHERE job_id=? AND figure_key=?""",
                (semantic_json, drawio_rel.as_posix(), svg_rel.as_posix(),
                 png_rel.as_posix(), qa_json, now, context["job_id"], source["figure_key"]),
            )
        return {"revision_id": revision_id, "figure_key": source["figure_key"],
                "section_key": source["section_key"], "version": version,
                "title": source["title"], "status": "clean", "edit_source": "manual",
                "operations": operations, "conflicts": [], "editor_managed": True,
                "drawio_relative_path": drawio_rel.as_posix(),
                "svg_relative_path": svg_rel.as_posix(),
                "png_relative_path": png_rel.as_posix(), "qa": qa, "elapsed_ms": 0}

    def _current(self, job_id: str, figure_key: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT mfa.figure_key, mfa.section_key, mfa.title, mfa.figure_type,
                mfa.semantic_json, mfr.id revision_id, mfr.version,
                mfr.evidence_refs_json FROM manual_figure_artifacts mfa
                JOIN manual_figure_revisions mfr ON mfr.job_id=mfa.job_id
                    AND mfr.figure_key=mfa.figure_key
                WHERE mfa.job_id=? AND mfa.figure_key=?
                ORDER BY mfr.version DESC LIMIT 1""", (job_id, figure_key),
            ).fetchone()
        if row is None:
            raise ManualFigureError("正式说明书图表尚未生成")
        return {"figure_key": row["figure_key"], "section_key": row["section_key"],
                "title": row["title"], "figure_type": row["figure_type"],
                "semantic": json.loads(row["semantic_json"]),
                "revision_id": row["revision_id"], "version": row["version"],
                "evidence_refs": json.loads(row["evidence_refs_json"])}

    def read_asset(self, job_id: str, figure_key: str, asset_format: str) -> tuple:
        if asset_format not in {"drawio", "svg", "png"}:
            raise ManualFigureError("不支持的图表资产格式")
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.task_id, mfa.drawio_relative_path, mfa.svg_relative_path,
                mfa.png_relative_path FROM manual_figure_artifacts mfa
                JOIN manual_generation_jobs j ON j.id = mfa.job_id
                WHERE mfa.job_id = ? AND mfa.figure_key = ?
                AND mfa.status IN ('rendered', 'verified')""",
                (job_id, figure_key),
            ).fetchone()
        if row is None:
            raise ManualFigureError("图表资产不存在或尚未完成")
        relative = row[asset_format + "_relative_path"]
        task_root = (self._data_root / "tasks" / row["task_id"]).resolve()
        path = (task_root / relative).resolve()
        if task_root not in path.parents or not path.is_file():
            raise ManualFigureError("图表资产路径无效或文件缺失")
        media = {"drawio": "application/vnd.jgraph.mxfile",
                 "svg": "image/svg+xml", "png": "image/png"}[asset_format]
        return path.read_bytes(), media

    def _context(self, job_id: str, model_config_id: Optional[str] = None) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.id job_id, j.task_id, j.version job_version,
                ps.display_name project_name, j.model_config_id default_model_id
                FROM manual_generation_jobs j JOIN tasks t ON t.id = j.task_id
                JOIN project_sources ps ON ps.id = t.source_id
                WHERE j.id = ?""", (job_id,),
            ).fetchone()
            chosen_model_id = model_config_id or (row["default_model_id"] if row else None)
            model = connection.execute(
                """SELECT id model_id, protocol_id, base_url, model_name,
                credential_ref, settings_json FROM model_configs
                WHERE id=? AND enabled=1""", (chosen_model_id,),
            ).fetchone() if chosen_model_id else None
        if row is None or model is None:
            raise ManualFigureError("说明书任务或模型配置不存在")
        result = {**dict(row), **dict(model)}
        result.pop("default_model_id", None)
        settings = json.loads(result.pop("settings_json") or "{}")
        result["endpoint_mode"] = settings.get("endpoint_mode") or (
            ManualGenerationService._default_mode(result["protocol_id"])
        )
        result["model"] = {"id": result["model_id"], "protocol_id": result["protocol_id"],
                           "base_url": result["base_url"], "model_name": result["model_name"]}
        return result

    def _sections(self, job_id: str) -> list:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT section_key, title, content_json, evidence_refs_json,
                figure_requests_json FROM manual_section_artifacts
                WHERE job_id = ? ORDER BY ordinal""", (job_id,),
            ).fetchall()
        if not rows:
            raise ManualFigureError("请先生成结构化说明书正文")
        return [{"section_key": row["section_key"], "title": row["title"],
                 "blocks": json.loads(row["content_json"]),
                 "evidence_refs": json.loads(row["evidence_refs_json"]),
                 "figure_requests": json.loads(row["figure_requests_json"])} for row in rows]

    @staticmethod
    def _requests(sections: list) -> list:
        requests, seen = [], set()
        for section in sections:
            for item in section["figure_requests"]:
                key = item["figure_key"]
                if key in seen:
                    continue
                seen.add(key)
                requests.append({**item, "section_key": section["section_key"],
                                 "section_title": section["title"],
                                 "section_blocks": section["blocks"],
                                 "section_evidence_refs": section["evidence_refs"]})
        defaults = (("architecture", "system_architecture", "architecture", "系统总体架构图"),
                    ("modules", "module_collaboration", "module", "核心模块协作图"))
        by_section = {item["section_key"]: item for item in sections}
        for section_key, figure_key, figure_type, title in defaults:
            if section_key in by_section and not any(
                item["section_key"] == section_key and item["figure_type"] == figure_type
                for item in requests
            ):
                section = by_section[section_key]
                requests.append({"figure_key": figure_key, "figure_type": figure_type,
                                 "title": title, "purpose": "呈现本章核心结构与协作关系",
                                 "evidence_refs": section["evidence_refs"],
                                 "section_key": section_key, "section_title": section["title"],
                                 "section_blocks": section["blocks"],
                                 "section_evidence_refs": section["evidence_refs"]})
        return requests[:8]

    def _start_step(self, job_id: str, update_job: bool = True) -> str:
        now = utc_now()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT id, status, attempt FROM manual_generation_steps WHERE job_id = ?
                AND step_key = 'diagrams' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if row is None:
                raise ManualFigureError("说明书任务缺少图表阶段")
            if row["status"] == "running":
                raise ManualFigureError("图表正在生成，请勿重复提交")
            if row["status"] in {"completed", "completed_with_warnings"}:
                step_id = str(uuid4())
                connection.execute(
                    """INSERT INTO manual_generation_steps(id, job_id, step_key, status,
                    attempt, summary_json, started_at) VALUES
                    (?, ?, 'diagrams', 'running', ?, '{}', ?)""",
                    (step_id, job_id, row["attempt"] + 1, now),
                )
            else:
                step_id = row["id"]
                connection.execute(
                    """UPDATE manual_generation_steps SET status='running', started_at=?,
                    finished_at=NULL, safe_error_message=NULL WHERE id=?""", (now, step_id),
                )
            if update_job:
                connection.execute(
                    """UPDATE manual_generation_jobs SET status='running', current_step='diagrams',
                    updated_at=?, safe_error_message=NULL WHERE id=?""", (now, job_id),
                )
        return step_id

    def _update_progress(self, job_id: str, step_id: str, completed_items: int,
                         total_items: int, current_title: str,
                         items: Optional[list] = None, concurrency: int = 1) -> None:
        """Persist per-figure progress for recovery after page navigation or reload."""
        fraction = completed_items / total_items if total_items else 1
        percent = round((2 + fraction) / 6 * 100)
        summary = {
            "completed_items": completed_items,
            "total_items": total_items,
            "current_title": current_title,
            "concurrency": concurrency,
        }
        if items is not None:
            summary["items"] = items
        progress = {
            "completed": 2,
            "total": 6,
            "percent": percent,
            "stage_completed": completed_items,
            "stage_total": total_items,
            "current_title": current_title,
        }
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE manual_generation_steps SET summary_json = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False, separators=(",", ":")), step_id),
            )
            connection.execute(
                """UPDATE manual_generation_jobs SET progress_json = ?, updated_at = ?
                WHERE id = ?""",
                (json.dumps(progress, ensure_ascii=False, separators=(",", ":")), now, job_id),
            )

    def _generate(self, context: dict, request: dict, on_retry=None) -> dict:
        prompt = self._prompt(context["project_name"], request)
        started = time.monotonic()
        api_key = None if context["protocol_id"] == "ollama" else self._vault.read(
            context["credential_ref"] or context["model_id"]
        )
        errors = []
        for attempt in range(1, MAX_FIGURE_ATTEMPTS + 1):
            current_prompt = prompt if attempt == 1 else self._repair_prompt(
                prompt, errors[-1]
            )
            try:
                raw = self._model_call(
                    context["model"], context["endpoint_mode"], api_key, current_prompt
                )
                semantic = self._normalize(self._parse(raw), request)
                source = {**request, "semantic": semantic,
                          "evidence_refs": semantic["evidence_refs"]}
                elapsed_ms = round((time.monotonic() - started) * 1000)
                return self._persist_revision(
                    context, source, semantic, "ai_generation", elapsed_ms, [], [],
                )
            except ManualGenerationError as error:
                failure = FigureGenerationFailure(
                    str(error), category="model_request", stage="model",
                    retryable=self._model_error_retryable(error), attempt=attempt,
                )
                errors.append(str(failure))
                if not failure.retryable or attempt >= MAX_FIGURE_ATTEMPTS:
                    raise failure from error
                if on_retry:
                    on_retry(attempt + 1, errors[-1])
            except FigureGenerationFailure as error:
                error.attempt = attempt
                errors.append(str(error))
                if not error.retryable:
                    raise
                if attempt >= MAX_FIGURE_ATTEMPTS:
                    fallback = self._deterministic_semantic(request)
                    if fallback is not None and error.category == "semantic_validation":
                        source = {**request, "semantic": fallback,
                                  "evidence_refs": fallback["evidence_refs"]}
                        elapsed_ms = round((time.monotonic() - started) * 1000)
                        return self._persist_revision(
                            context, source, fallback, "ai_generation",
                            elapsed_ms, [], [],
                        )
                    raise FigureGenerationFailure(
                        "AI 首次生成及一次结构修复后仍未生成可渲染图表：{0}".format(
                            errors[-1]
                        ), category=error.category, stage=error.stage,
                        retryable=True, attempt=attempt,
                    ) from error
                if on_retry:
                    on_retry(attempt + 1, errors[-1])
        raise FigureGenerationFailure(
            "AI 未生成可用图表", category="semantic_validation",
            stage="semantic", retryable=True, attempt=MAX_FIGURE_ATTEMPTS,
        )

    def _persist_revision(self, context: dict, source: dict, semantic: dict,
                          edit_source: str, elapsed_ms: int, operations: list,
                          conflicts: list) -> dict:
        with self._database.connect() as connection:
            current = connection.execute(
                """SELECT id, version FROM manual_figure_revisions
                WHERE job_id=? AND figure_key=? ORDER BY version DESC LIMIT 1""",
                (context["job_id"], source["figure_key"]),
            ).fetchone()
        version = (current["version"] if current else 0) + 1
        parent_revision_id = current["id"] if current else None
        # Figure revision numbers are scoped to a generation job. Namespace files
        # by job version to prevent two jobs with the same figure key overwriting
        # one another's editable source and previews.
        relative_root = (Path("artifacts") / "manual" / "jobs" /
                         "job-v{0}".format(context["job_version"]) / "diagrams" /
                         source["figure_key"])
        stem = "v{0}".format(version)
        drawio_rel, svg_rel, png_rel = (relative_root / (stem + suffix)
                                        for suffix in (".drawio", ".svg", ".png"))
        task_root = self._data_root / "tasks" / context["task_id"]
        drawio, svg, png = (task_root / item for item in (drawio_rel, svg_rel, png_rel))
        try:
            build = self._builder.build(semantic, drawio)
        except (DrawioDocumentError, TypeError, ValueError) as error:
            self._discard_partial_assets(drawio, svg, png)
            raise FigureGenerationFailure(
                "Draw.io 构建失败，语义已保留但未创建资产",
                category="drawio_build", stage="drawio", retryable=False,
                semantic=semantic,
            ) from error
        try:
            validation = self._inspector.require_valid(drawio)
        except (DrawioDocumentError, TypeError, ValueError) as error:
            self._discard_partial_assets(drawio, svg, png)
            raise FigureGenerationFailure(
                "Draw.io 本地结构校验失败：{0}".format(str(error)[:220]),
                category="drawio_validation", stage="drawio", retryable=False,
                semantic=semantic,
            ) from error
        try:
            self._svg.render(drawio, svg)
        except (DrawioDocumentError, TypeError, ValueError) as error:
            self._discard_partial_assets(drawio, svg, png)
            raise FigureGenerationFailure(
                "SVG 渲染失败，Draw.io 语义无需重新生成",
                category="svg_render", stage="render", retryable=False,
                semantic=semantic,
            ) from error
        try:
            png_report = self._png.render(drawio, png)
        except (DrawioDocumentError, TypeError, ValueError) as error:
            self._discard_partial_assets(drawio, svg, png)
            raise FigureGenerationFailure(
                "PNG 渲染失败，Draw.io 语义无需重新生成",
                category="png_render", stage="render", retryable=False,
                semantic=semantic,
            ) from error
        qa = {"structure": validation, "build": build, "png": png_report,
              "visual_review": "pending_final_document_qa"}
        now = utc_now()
        semantic_json = json.dumps(semantic, ensure_ascii=False, separators=(",", ":"))
        qa_json = json.dumps(qa, ensure_ascii=False, separators=(",", ":"))
        refs_json = json.dumps(semantic["evidence_refs"], ensure_ascii=False,
                               separators=(",", ":"))
        operations_json = json.dumps(operations, ensure_ascii=False, separators=(",", ":"))
        fingerprint = self._overlay.semantic_fingerprint(semantic)
        revision_status = "conflicted" if conflicts else "clean"
        revision_id = str(uuid4())
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_figure_revisions(id, job_id, figure_key, version,
                section_key, title, figure_type, semantic_json, evidence_refs_json,
                drawio_relative_path, svg_relative_path, png_relative_path, qa_json,
                model_name, elapsed_ms, created_at, edit_source, parent_revision_id,
                operations_json, semantic_fingerprint, revision_status) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (revision_id, context["job_id"], source["figure_key"], version,
                 source["section_key"], source["title"], source["figure_type"],
                 semantic_json, refs_json, drawio_rel.as_posix(), svg_rel.as_posix(),
                 png_rel.as_posix(), qa_json, context["model_name"], elapsed_ms, now,
                 edit_source, parent_revision_id, operations_json, fingerprint,
                 revision_status),
            )
            connection.execute(
                """INSERT INTO manual_figure_artifacts(id, job_id, figure_key, section_key,
                figure_type, title, status, semantic_json, drawio_relative_path,
                svg_relative_path, png_relative_path, qa_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'rendered', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, figure_key) DO UPDATE SET section_key=excluded.section_key,
                figure_type=excluded.figure_type, title=excluded.title, status='rendered',
                semantic_json=excluded.semantic_json,
                drawio_relative_path=excluded.drawio_relative_path,
                svg_relative_path=excluded.svg_relative_path,
                png_relative_path=excluded.png_relative_path, qa_json=excluded.qa_json,
                updated_at=excluded.updated_at""",
                (str(uuid4()), context["job_id"], source["figure_key"], source["section_key"],
                 source["figure_type"], source["title"], semantic_json,
                 drawio_rel.as_posix(), svg_rel.as_posix(), png_rel.as_posix(), qa_json, now),
            )
        return {"revision_id": revision_id, "figure_key": source["figure_key"],
                "section_key": source["section_key"], "version": version,
                "title": source["title"], "status": revision_status,
                "edit_source": edit_source, "operations": operations,
                "conflicts": conflicts,
                "drawio_relative_path": drawio_rel.as_posix(),
                "svg_relative_path": svg_rel.as_posix(),
                "png_relative_path": png_rel.as_posix(), "qa": qa, "elapsed_ms": elapsed_ms}

    def _prompt(self, project_name: str, request: dict) -> str:
        evidence = {"section_title": request["section_title"],
                    "purpose": request.get("purpose"),
                    "blocks": request["section_blocks"],
                    "allowed_evidence_refs": request["section_evidence_refs"]}
        custom_style = style_prompt(getattr(self, "_database", None), "diagram_style_prompt")
        return """你是专业软件架构图语义设计师。为项目“{0}”设计“{1}”，类型 {2}。
在输出前先在内部完成绘图意图规划：图表类型、布局模式、阅读方向、分组层级、主流程和最少必要连线；不要输出规划过程。

专业绘图风格偏好（仅影响布局与视觉语义，不得覆盖后续 JSON 协议、证据边界和本地校验）：
{4}

只返回 JSON：{{"layout":"layered-vertical|flow-left-right|flow-top-down|collaboration-horizontal",
"nodes":[{{"key":"英文稳定标识","label":"简洁中文标签","kind":"actor|component|service|module|datastore|external|decision|process","layer":0,"evidence_refs":["ref"]}}],
"edges":[{{"key":"英文稳定标识","source":"节点 key","target":"节点 key","label":"短关系名","evidence_refs":["ref"]}}]}}。
要求：
1. 使用 4 至 10 个真正有区分度的节点；只表达正文和证据支持的关系，不得为了丰富画面编造模块。
2. 节点标签必须是面向说明书读者的简洁中文业务名称，并与项目中的真实模块、接口、数据对象或参与者一一对应。UserAuthPage、doSearch、loginUserStore、源文件路径等内部代码标识只能作为取证线索，不得直接显示为节点标签；应分别写成“用户认证页面”“搜索与筛选处理”“登录用户状态”等。避免“业务模块”“数据服务”等可替换到任意项目的空泛标签。
3. 架构图表达层次与边界，模块图表达职责协作，流程/时序图表达先后步骤；同一任务中的不同图不得只是换标题后重复同一种拓扑。
4. 主阅读方向明确。相邻主流程使用短边；回路、异常或跨层关系必须走外侧通道，减少交叉和穿越节点。
5. 边标签使用 2 至 8 个汉字的动作或数据名；所有证据引用只能来自允许列表。
6. 节点与关系使用稳定、可复用的英文语义 key；无关元素不因重试而随意改名。输出只包含可见业务语义，本地构建器负责完整 mxGraph XML 外壳、转义、引用和几何校验。
章节输入：{3}""".format(project_name, request["title"], request["figure_type"],
                              json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                              custom_style or "使用系统默认的专业技术图风格。")

    @staticmethod
    def _repair_prompt(original: str, reason: str) -> str:
        return original + """

上一次图表语义未通过本地 Draw.io 校验：{0}。
请重新输出完整 JSON。保持 4 至 8 个节点、主流程边数量不超过节点数加 2；
所有 source/target 必须引用已声明节点 key，节点 key 和边 key 不得重复，避免自环和过密关系。
不要解释，不要 Markdown 代码围栏。
""".format(reason)

    @staticmethod
    def _parse(raw: str) -> dict:
        text = (raw or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            first, last = text.find("{"), text.rfind("}")
            if first >= 0 and last > first:
                text = text[first:last + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise FigureGenerationFailure(
                "模型未返回可解析的图表语义",
                category="semantic_parse", stage="semantic", retryable=True,
            ) from error

    @staticmethod
    def _normalize(payload: dict, request: dict) -> dict:
        allowed_refs = set(request["section_evidence_refs"])
        nodes, seen = [], set()
        for index, item in enumerate(payload.get("nodes", [])[:10]):
            key = re.sub(r"[^a-z0-9_-]", "_", str(item.get("key", "")).lower()).strip("_")
            if not key:
                key = "node-{0}".format(index + 1)
            if key in seen:
                key += "-{0}".format(index + 1)
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            seen.add(key)
            kind = item.get("kind") or "component"
            node = {"key": key, "label": label[:80],
                          "kind": kind,
                          "layer": max(0, min(8, int(item.get("layer", index)))),
                          "evidence_refs": [ref for ref in item.get("evidence_refs", [])
                                            if ref in allowed_refs]}
            display_label = _reader_label(label, kind)
            if display_label != label:
                node["display_label"] = display_label[:40]
            nodes.append(node)
        if len(nodes) < 3:
            raise FigureGenerationFailure(
                "图表语义节点不足", category="semantic_validation",
                stage="semantic", retryable=True,
            )
        keys = {item["key"] for item in nodes}
        edges = []
        # Dense hairballs are not useful in an A4 manual.  Preserve the model's
        # primary relations while bounding secondary/return edges to the number a
        # reader can follow without interactive highlighting.
        maximum_edges = min(12, len(nodes) + 2)
        for index, item in enumerate(payload.get("edges", [])[:maximum_edges]):
            if item.get("source") not in keys or item.get("target") not in keys:
                continue
            edge_label = str(item.get("label", "")).strip()[:48]
            edge = {"key": re.sub(r"[^a-z0-9_-]", "_",
                                         str(item.get("key") or "edge-{0}".format(index + 1)).lower()),
                          "source": item["source"], "target": item["target"],
                          "label": edge_label,
                          "evidence_refs": [ref for ref in item.get("evidence_refs", [])
                                            if ref in allowed_refs]}
            display_label = _reader_label(edge_label, "relation")
            if display_label != edge_label:
                edge["display_label"] = display_label[:24]
            edges.append(edge)
        if not edges:
            raise FigureGenerationFailure(
                "图表语义缺少有效关系", category="semantic_validation",
                stage="semantic", retryable=True,
            )
        layout = payload.get("layout")
        if layout not in {"layered-vertical", "flow-left-right", "flow-top-down",
                          "collaboration-horizontal"}:
            layout = "flow-left-right" if request["figure_type"] in {"workflow", "sequence"} \
                else "layered-vertical"
        refs = sorted({ref for item in nodes + edges for ref in item["evidence_refs"]})
        return {"figure_key": request["figure_key"], "title": request["title"],
                "figure_type": request["figure_type"], "layout": layout,
                "nodes": nodes, "edges": edges, "evidence_refs": refs}

    def _record_failed(self, context: dict, request: dict, error: Exception) -> None:
        now = utc_now()
        detail = self._failure_detail(request, error)
        semantic = error.semantic if isinstance(error, FigureGenerationFailure) else None
        semantic_json = json.dumps(
            semantic if isinstance(semantic, dict) else {},
            ensure_ascii=False, separators=(",", ":"),
        )
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_figure_artifacts(id, job_id, figure_key, section_key,
                figure_type, title, status, semantic_json, qa_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)
                ON CONFLICT(job_id, figure_key) DO UPDATE SET status='failed',
                semantic_json=excluded.semantic_json, qa_json=excluded.qa_json,
                updated_at=excluded.updated_at""",
                (str(uuid4()), context["job_id"], request["figure_key"], request["section_key"],
                 request["figure_type"], request["title"], semantic_json,
                 json.dumps(detail, ensure_ascii=False, separators=(",", ":")), now),
            )

    @staticmethod
    def _failure_detail(request: dict, error: Exception) -> dict:
        if isinstance(error, FigureGenerationFailure):
            category = error.category
            stage = error.stage
            retryable = error.retryable
            attempt = error.attempt
        else:
            category = "unexpected"
            stage = "unknown"
            retryable = True
            attempt = 1
        return {
            "figure_key": request["figure_key"],
            "title": request["title"],
            "message": str(error)[:300],
            "error": str(error)[:300],
            "category": category,
            "stage": stage,
            "retryable": retryable,
            "attempt": attempt,
        }

    @staticmethod
    def _model_error_retryable(error: ManualGenerationError) -> bool:
        message = str(error).lower()
        if any(token in message for token in (
                "http 400", "http 401", "http 403", "http 404", "unsupported_protocol")):
            return False
        return True

    @staticmethod
    def _discard_partial_assets(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Failure cleanup must never hide the actionable build/render error.
                pass

    def _finish_step(self, job_id: str, step_id: str, generated: list, errors: list) -> None:
        now = utc_now()
        if errors and not generated:
            status, job_status, current, completed = "failed", "failed", "diagrams", 2
        else:
            status = "completed_with_warnings" if errors else "completed"
            job_status, current, completed = "running", "screenshots", 3
        progress = {"completed": completed, "total": 6,
                    "percent": round(completed / 6 * 100)}
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT summary_json FROM manual_generation_steps WHERE id = ?", (step_id,),
            ).fetchone()
            summary = json.loads(row["summary_json"] or "{}") if row else {}
            summary.update({"generated_figures": len(generated),
                            "failed_figures": len(errors),
                            "completed_items": len(generated) + len(errors),
                            "total_items": len(generated) + len(errors),
                            "errors": errors,
                            "visual_review": "pending_final_document_qa"})
            connection.execute(
                """UPDATE manual_generation_steps SET status=?, summary_json=?, finished_at=?,
                safe_error_message=? WHERE id=?""",
                (status, json.dumps(summary, ensure_ascii=False, separators=(",", ":")), now,
                 errors[0]["message"] if errors and not generated else None, step_id),
            )
            connection.execute(
                """UPDATE manual_generation_jobs SET status=?, current_step=?, progress_json=?,
                updated_at=?, safe_error_message=? WHERE id=?""",
                (job_status, current, json.dumps(progress, separators=(",", ":")), now,
                 errors[0]["message"] if errors and not generated else None, job_id),
            )
