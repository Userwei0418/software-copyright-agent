import json
import re
import time
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from .credential_vault import CredentialVault
from .drawio_document import (
    DrawioDocumentInspector, GenericDrawioDocumentBuilder,
    InternalPngRenderer, InternalSvgRenderer,
)
from .manual_generation import ManualGenerationService
from .service import utc_now
from .storage import Database


class ManualFigureError(ValueError):
    pass


class ManualFigureService:
    """Turns section figure requests into editable, evidence-bound figure assets."""

    def __init__(self, database: Database, data_root: Path,
                 model_call: Optional[Callable] = None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._vault = CredentialVault(database, data_root)
        self._model_call = model_call or ManualGenerationService(database, data_root)._call_model
        self._builder = GenericDrawioDocumentBuilder()
        self._inspector = DrawioDocumentInspector()
        self._svg = InternalSvgRenderer()
        self._png = InternalPngRenderer(scale=2)

    def generate_all(self, job_id: str) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        sections = self._sections(job_id)
        requests = self._requests(sections)
        step_id = self._start_step(job_id)
        generated, errors = [], []
        for request in requests:
            try:
                generated.append(self._generate(context, request))
            except Exception as error:
                errors.append({"figure_key": request["figure_key"],
                               "message": str(error)[:300]})
                self._record_failed(context, request, error)
        self._finish_step(job_id, step_id, generated, errors)
        if not generated and requests:
            raise ManualFigureError(errors[0]["message"])
        return {"job_id": job_id,
                "status": "completed_with_warnings" if errors else "completed",
                "generated": generated, "errors": errors, "figures": self.list(job_id)}

    def regenerate(self, job_id: str, figure_key: str) -> dict:
        context = self._context(job_id)
        request = next((item for item in self._requests(self._sections(job_id))
                        if item["figure_key"] == figure_key), None)
        if request is None:
            raise ManualFigureError("图表请求不存在")
        return self._generate(context, request)

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
        return [{"id": row["id"], "figure_key": row["figure_key"],
                 "section_key": row["section_key"], "figure_type": row["figure_type"],
                 "title": row["title"], "status": row["status"],
                 "version": row["revision_version"],
                 "semantic": json.loads(row["semantic_json"]),
                 "drawio_relative_path": row["drawio_relative_path"],
                 "svg_relative_path": row["svg_relative_path"],
                 "png_relative_path": row["png_relative_path"],
                 "qa": json.loads(row["qa_json"]), "updated_at": row["updated_at"]}
                for row in rows]

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

    def _context(self, job_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.id job_id, j.task_id, j.version job_version,
                ps.display_name project_name, mc.id model_id, mc.protocol_id, mc.base_url,
                mc.model_name, mc.credential_ref, mc.settings_json
                FROM manual_generation_jobs j JOIN tasks t ON t.id = j.task_id
                JOIN project_sources ps ON ps.id = t.source_id
                JOIN model_configs mc ON mc.id = j.model_config_id AND mc.enabled = 1
                WHERE j.id = ?""", (job_id,),
            ).fetchone()
        if row is None:
            raise ManualFigureError("说明书任务或模型配置不存在")
        result = dict(row)
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

    def _start_step(self, job_id: str) -> str:
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
            connection.execute(
                """UPDATE manual_generation_jobs SET status='running', current_step='diagrams',
                updated_at=?, safe_error_message=NULL WHERE id=?""", (now, job_id),
            )
        return step_id

    def _generate(self, context: dict, request: dict) -> dict:
        prompt = self._prompt(context["project_name"], request)
        started = time.monotonic()
        api_key = None if context["protocol_id"] == "ollama" else self._vault.read(
            context["credential_ref"] or context["model_id"]
        )
        raw = self._model_call(context["model"], context["endpoint_mode"], api_key, prompt)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        semantic = self._normalize(self._parse(raw), request)
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version),0)+1 value FROM manual_figure_revisions
                WHERE job_id=? AND figure_key=?""",
                (context["job_id"], request["figure_key"]),
            ).fetchone()["value"]
        relative_root = Path("artifacts") / "manual" / "diagrams" / request["figure_key"]
        stem = "v{0}".format(version)
        drawio_rel, svg_rel, png_rel = (relative_root / (stem + suffix)
                                        for suffix in (".drawio", ".svg", ".png"))
        task_root = self._data_root / "tasks" / context["task_id"]
        drawio, svg, png = (task_root / item for item in (drawio_rel, svg_rel, png_rel))
        build = self._builder.build(semantic, drawio)
        validation = self._inspector.require_valid(drawio)
        self._svg.render(drawio, svg)
        png_report = self._png.render(drawio, png)
        qa = {"structure": validation, "build": build, "png": png_report,
              "visual_review": "pending_final_document_qa"}
        now = utc_now()
        semantic_json = json.dumps(semantic, ensure_ascii=False, separators=(",", ":"))
        qa_json = json.dumps(qa, ensure_ascii=False, separators=(",", ":"))
        refs_json = json.dumps(semantic["evidence_refs"], ensure_ascii=False,
                               separators=(",", ":"))
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_figure_revisions(id, job_id, figure_key, version,
                section_key, title, figure_type, semantic_json, evidence_refs_json,
                drawio_relative_path, svg_relative_path, png_relative_path, qa_json,
                model_name, elapsed_ms, created_at) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), context["job_id"], request["figure_key"], version,
                 request["section_key"], request["title"], request["figure_type"],
                 semantic_json, refs_json, drawio_rel.as_posix(), svg_rel.as_posix(),
                 png_rel.as_posix(), qa_json, context["model_name"], elapsed_ms, now),
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
                (str(uuid4()), context["job_id"], request["figure_key"], request["section_key"],
                 request["figure_type"], request["title"], semantic_json,
                 drawio_rel.as_posix(), svg_rel.as_posix(), png_rel.as_posix(), qa_json, now),
            )
        return {"figure_key": request["figure_key"], "section_key": request["section_key"],
                "version": version, "title": request["title"], "status": "rendered",
                "drawio_relative_path": drawio_rel.as_posix(),
                "svg_relative_path": svg_rel.as_posix(),
                "png_relative_path": png_rel.as_posix(), "qa": qa, "elapsed_ms": elapsed_ms}

    @staticmethod
    def _prompt(project_name: str, request: dict) -> str:
        evidence = {"section_title": request["section_title"],
                    "purpose": request.get("purpose"),
                    "blocks": request["section_blocks"],
                    "allowed_evidence_refs": request["section_evidence_refs"]}
        return """你是专业软件架构图语义设计师。为项目“{0}”设计“{1}”，类型 {2}。
只返回 JSON：{{"layout":"layered-vertical|flow-left-right|flow-top-down|collaboration-horizontal",
"nodes":[{{"key":"英文稳定标识","label":"简洁中文标签","kind":"actor|component|service|module|datastore|external|decision|process","layer":0,"evidence_refs":["ref"]}}],
"edges":[{{"key":"英文稳定标识","source":"节点 key","target":"节点 key","label":"短关系名","evidence_refs":["ref"]}}]}}。
要求 3 至 12 个节点；只表达正文和证据支持的关系；不得为了丰富画面编造模块；主阅读方向明确；边标签简短；架构/模块图优先 layered-vertical，流程图优先 flow-left-right；所有证据引用只能来自允许列表。
章节输入：{3}""".format(project_name, request["title"], request["figure_type"],
                              json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))

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
            raise ManualFigureError("模型未返回可解析的图表语义") from error

    @staticmethod
    def _normalize(payload: dict, request: dict) -> dict:
        allowed_refs = set(request["section_evidence_refs"])
        nodes, seen = [], set()
        for index, item in enumerate(payload.get("nodes", [])[:12]):
            key = re.sub(r"[^a-z0-9_-]", "_", str(item.get("key", "")).lower()).strip("_")
            if not key:
                key = "node-{0}".format(index + 1)
            if key in seen:
                key += "-{0}".format(index + 1)
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            seen.add(key)
            nodes.append({"key": key, "label": label[:40],
                          "kind": item.get("kind") or "component",
                          "layer": max(0, min(8, int(item.get("layer", index)))),
                          "evidence_refs": [ref for ref in item.get("evidence_refs", [])
                                            if ref in allowed_refs]})
        if len(nodes) < 3:
            raise ManualFigureError("图表语义节点不足")
        keys = {item["key"] for item in nodes}
        edges = []
        for index, item in enumerate(payload.get("edges", [])[:20]):
            if item.get("source") not in keys or item.get("target") not in keys:
                continue
            edges.append({"key": re.sub(r"[^a-z0-9_-]", "_",
                                         str(item.get("key") or "edge-{0}".format(index + 1)).lower()),
                          "source": item["source"], "target": item["target"],
                          "label": str(item.get("label", "")).strip()[:24],
                          "evidence_refs": [ref for ref in item.get("evidence_refs", [])
                                            if ref in allowed_refs]})
        if not edges:
            raise ManualFigureError("图表语义缺少有效关系")
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
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_figure_artifacts(id, job_id, figure_key, section_key,
                figure_type, title, status, semantic_json, qa_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'failed', '{}', ?, ?)
                ON CONFLICT(job_id, figure_key) DO UPDATE SET status='failed',
                qa_json=excluded.qa_json, updated_at=excluded.updated_at""",
                (str(uuid4()), context["job_id"], request["figure_key"], request["section_key"],
                 request["figure_type"], request["title"],
                 json.dumps({"error": str(error)[:300]}, ensure_ascii=False), now),
            )

    def _finish_step(self, job_id: str, step_id: str, generated: list, errors: list) -> None:
        now = utc_now()
        if errors and not generated:
            status, job_status, current, completed = "failed", "failed", "diagrams", 2
        else:
            status = "completed_with_warnings" if errors else "completed"
            job_status, current, completed = "running", "screenshots", 3
        summary = {"generated_figures": len(generated), "failed_figures": len(errors),
                   "errors": errors, "visual_review": "pending_final_document_qa"}
        progress = {"completed": completed, "total": 6,
                    "percent": round(completed / 6 * 100)}
        with self._database.connect() as connection:
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
