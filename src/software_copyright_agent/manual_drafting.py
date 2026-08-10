import hashlib
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from .credential_vault import CredentialVault
from .manual_generation import ManualGenerationService
from .service import utc_now
from .storage import Database


SECTION_BLUEPRINTS = (
    ("introduction", "引言", 1),
    ("architecture", "总体设计", 2),
    ("modules", "功能与模块设计", 3),
    ("data_interfaces", "数据与接口设计", 4),
    ("runtime", "运行与部署设计", 5),
    ("security_reliability", "安全性与可靠性", 6),
    ("ui_operations", "用户界面与操作说明", 7),
    ("testing_summary", "测试、技术指标与总结", 8),
)
ALLOWED_BLOCK_TYPES = {"paragraph", "list", "table", "figure_request"}
ALLOWED_FIGURE_TYPES = {
    "architecture", "module", "workflow", "sequence", "er", "deployment", "data_flow",
}


class ManualDraftingError(ValueError):
    pass


class ManualDraftingService:
    """Generates reviewable, evidence-bound section blocks instead of Markdown."""

    def __init__(
        self,
        database: Database,
        data_root: Path,
        model_call: Optional[Callable[[dict, str, Optional[str], str], str]] = None,
    ) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._vault = CredentialVault(database, data_root)
        self._model_call = model_call or ManualGenerationService(
            database, data_root
        )._call_model

    def generate_all(self, job_id: str) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        research = self._research(job_id, context["task_id"])
        step_id = self._start_step(job_id)
        generated, errors = [], []
        for section_key, title, ordinal in self._blueprints(research):
            try:
                generated.append(self._generate(
                    context, research, section_key, title, ordinal, origin="ai"
                ))
            except Exception as error:
                errors.append({"section_key": section_key, "message": str(error)[:300]})
        if not generated:
            message = errors[0]["message"] if errors else "没有生成任何章节"
            self._finish_step(job_id, step_id, [], errors, failed=True)
            raise ManualDraftingError(message)
        self._finish_step(job_id, step_id, generated, errors, failed=False)
        return {
            "job_id": job_id, "status": "completed_with_warnings" if errors else "completed",
            "generated": generated, "errors": errors, "sections": self.list_sections(job_id),
        }

    def regenerate(self, job_id: str, section_key: str) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        research = self._research(job_id, context["task_id"])
        blueprint = next(
            (item for item in self._blueprints(research) if item[0] == section_key), None
        )
        if blueprint is None:
            raise ManualDraftingError("章节不存在或不适用于当前项目")
        result = self._generate(context, research, *blueprint, origin="ai")
        self._refresh_draft_summary(job_id)
        return result

    def save_edit(self, job_id: str, section_key: str, title: str, blocks: list) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        research = self._research(job_id, context["task_id"])
        current = self._current(job_id, section_key)
        if current is None:
            raise ManualDraftingError("章节尚未生成，不能保存人工修改")
        normalized = self._normalize_payload(
            {"title": title, "blocks": blocks}, research, expected_key=section_key
        )
        normalized["status"] = "confirmed"
        return self._persist_section(
            context, section_key, title.strip(), current["ordinal"], normalized,
            origin="user", prompt_fingerprint=None, elapsed_ms=0,
        )

    def list_sections(self, job_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT msa.*, COALESCE(MAX(msr.version), 0) revision_version
                FROM manual_section_artifacts msa
                LEFT JOIN manual_section_revisions msr ON msr.job_id = msa.job_id
                    AND msr.section_key = msa.section_key
                WHERE msa.job_id = ? GROUP BY msa.id ORDER BY msa.ordinal""",
                (job_id,),
            ).fetchall()
        return [self._section_dict(row) for row in rows]

    def revisions(self, job_id: str, section_key: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM manual_section_revisions WHERE job_id = ? AND section_key = ?
                ORDER BY version DESC""", (job_id, section_key),
            ).fetchall()
        return [{
            "id": row["id"], "section_key": row["section_key"],
            "version": row["version"], "origin": row["origin"], "title": row["title"],
            "status": row["status"], "blocks": json.loads(row["content_json"]),
            "evidence_refs": json.loads(row["evidence_refs_json"]),
            "inference_notes": json.loads(row["inference_notes_json"]),
            "figure_requests": json.loads(row["figure_requests_json"]),
            "model_name": row["model_name"], "elapsed_ms": row["elapsed_ms"],
            "created_at": row["created_at"],
        } for row in rows]

    def _context(self, job_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.id job_id, j.task_id, ps.display_name project_name,
                mc.id model_id, mc.protocol_id, mc.base_url, mc.model_name,
                mc.credential_ref, mc.settings_json FROM manual_generation_jobs j
                JOIN tasks t ON t.id = j.task_id
                JOIN project_sources ps ON ps.id = t.source_id
                JOIN model_configs mc ON mc.id = j.model_config_id AND mc.enabled = 1
                WHERE j.id = ?""", (job_id,),
            ).fetchone()
        if row is None:
            raise ManualDraftingError("说明书任务或模型配置不存在")
        result = dict(row)
        settings = json.loads(result.pop("settings_json") or "{}")
        result["endpoint_mode"] = settings.get("endpoint_mode") or (
            ManualGenerationService._default_mode(result["protocol_id"])
        )
        result["model"] = {
            "id": result["model_id"], "protocol_id": result["protocol_id"],
            "base_url": result["base_url"], "model_name": result["model_name"],
        }
        return result

    def _research(self, job_id: str, task_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT artifact_relative_path FROM manual_research_artifacts
                WHERE job_id = ? ORDER BY version DESC LIMIT 1""", (job_id,),
            ).fetchone()
        if row is None:
            raise ManualDraftingError("请先完成项目研究阶段")
        path = self._data_root / "tasks" / task_id / row["artifact_relative_path"]
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManualDraftingError("项目研究产物缺失或损坏") from error

    @staticmethod
    def _blueprints(research: dict) -> tuple:
        guidance_keys = {
            item.get("section_key") for item in research.get("section_guidance", [])
        }
        has_ui = "ui_operations" in guidance_keys or any(
            marker in json.dumps(research.get("project_profile", {}), ensure_ascii=False).lower()
            for marker in ("react", "vue", "svelte", "tauri", "electron", "frontend")
        )
        return tuple(
            item for item in SECTION_BLUEPRINTS if item[0] != "ui_operations" or has_ui
        )

    def _start_step(self, job_id: str) -> str:
        now = utc_now()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT id, status, attempt FROM manual_generation_steps WHERE job_id = ?
                AND step_key = 'draft' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if row is None:
                raise ManualDraftingError("说明书任务缺少正文阶段")
            if row["status"] == "running":
                raise ManualDraftingError("正文正在生成，请勿重复提交")
            if row["status"] in {"completed", "completed_with_warnings"}:
                step_id = str(uuid4())
                connection.execute(
                    """INSERT INTO manual_generation_steps(id, job_id, step_key, status,
                    attempt, summary_json, started_at) VALUES
                    (?, ?, 'draft', 'running', ?, '{}', ?)""",
                    (step_id, job_id, row["attempt"] + 1, now),
                )
            else:
                step_id = row["id"]
                connection.execute(
                    """UPDATE manual_generation_steps SET status = 'running', started_at = ?,
                    finished_at = NULL, safe_error_message = NULL WHERE id = ?""",
                    (now, step_id),
                )
            connection.execute(
                """UPDATE manual_generation_jobs SET status = 'running', current_step = 'draft',
                updated_at = ?, safe_error_message = NULL WHERE id = ?""", (now, job_id),
            )
        return step_id

    def _generate(self, context: dict, research: dict, section_key: str, title: str,
                  ordinal: int, origin: str) -> dict:
        prompt = self._prompt(context["project_name"], section_key, title, research)
        started = time.monotonic()
        api_key = self._api_key(context)
        raw = self._model_call(context["model"], context["endpoint_mode"], api_key, prompt)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        payload = self._parse_json(raw)
        normalized = self._normalize_payload(payload, research, expected_key=section_key)
        return self._persist_section(
            context, section_key, normalized.get("title") or title, ordinal, normalized,
            origin=origin, prompt_fingerprint=hashlib.sha256(prompt.encode()).hexdigest(),
            elapsed_ms=elapsed_ms,
        )

    def _api_key(self, context: dict) -> Optional[str]:
        if context["protocol_id"] == "ollama":
            return None
        try:
            return self._vault.read(context["credential_ref"] or context["model_id"])
        except ValueError as error:
            raise ManualDraftingError("所选模型的 API Key 不存在，请在设置中重新配置") from error

    @staticmethod
    def _prompt(project_name: str, section_key: str, title: str, research: dict) -> str:
        guidance = next((item for item in research.get("section_guidance", [])
                         if item.get("section_key") == section_key), {})
        relevant_refs = set(guidance.get("evidence_refs", []))
        notes = [item for item in research.get("research_notes", [])
                 if relevant_refs.intersection(item.get("evidence_refs", []))]
        if not notes:
            notes = research.get("research_notes", [])[:30]
        source_refs = [item for item in research.get("source_refs", [])
                       if item.get("ref") in relevant_refs][:6]
        evidence = {
            "project_profile": research.get("project_profile", {}),
            "research_notes": notes, "section_guidance": guidance,
            "source_refs": source_refs,
        }
        return """你是中国软件著作权技术说明书撰写专家。请为项目“{0}”撰写“{1}”章节，章节 key 为 {2}。

只返回 JSON，不要 Markdown。格式：
{{"section_key":"{2}","title":"{1}","blocks":[
  {{"type":"paragraph","text":"项目化正文","evidence_refs":["ref"],"inference":false}},
  {{"type":"list","lead":"引导句","items":["项目化条目"],"evidence_refs":["ref"],"inference":false}},
  {{"type":"table","title":"表名","headers":["列"],"rows":[["值"]],"evidence_refs":["ref"],"inference":false}},
  {{"type":"figure_request","figure_key":"唯一英文标识","figure_type":"architecture|module|workflow|sequence|er|deployment|data_flow","title":"图名","purpose":"图应表达的项目语义","evidence_refs":["ref"]}}
]}}

要求：
1. 至少 4 个有实质内容的块；正文使用自然、正式的中文，不写“待补充”模板段落。
2. 事实性内容必须引用输入中真实存在的 ref。合理推断设置 inference=true 并明确写“根据项目证据推断”。
3. 缺失事实可以说明尚待确认，但不能编造客户、规模、性能、外部系统或未实现功能。
4. 不在正文中罗列内部文件路径，不使用 Markdown 标题、加粗符号或代码围栏。
5. 只有确实提升理解时才请求图表；表格仅用于真正可比较的信息。
6. 描述职责、输入、处理、输出、异常与恢复，避免空泛的软件工程套话。

研究证据：
{3}
""".format(project_name, title, section_key,
           json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _parse_json(raw: str) -> dict:
        if not raw or not raw.strip():
            raise ManualDraftingError("模型返回了空章节")
        text = raw.strip()
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
            raise ManualDraftingError("模型未返回可解析的结构化章节") from error

    @staticmethod
    def _normalize_payload(payload: dict, research: dict, expected_key: str) -> dict:
        if payload.get("section_key", expected_key) != expected_key:
            raise ManualDraftingError("模型返回了错误的章节标识")
        valid_refs = {item["ref"] for item in research.get("source_refs", [])}
        valid_refs.update(ref for note in research.get("research_notes", [])
                          for ref in note.get("evidence_refs", []))
        blocks, evidence_refs, inference_notes, figures = [], set(), [], []
        needs_review = False
        for raw in payload.get("blocks", []):
            if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_BLOCK_TYPES:
                continue
            block_type = raw["type"]
            refs = [ref for ref in raw.get("evidence_refs", []) if ref in valid_refs]
            inference = bool(raw.get("inference", False))
            if block_type == "paragraph":
                text = str(raw.get("text", "")).strip()
                if len(text) < 20:
                    continue
                if inference and "推断" not in text:
                    text = "根据项目证据推断，" + text
                block = {"type": block_type, "text": text, "evidence_refs": refs,
                         "inference": inference}
            elif block_type == "list":
                items = [str(item).strip() for item in raw.get("items", [])
                         if len(str(item).strip()) >= 8]
                if not items:
                    continue
                block = {"type": block_type, "lead": str(raw.get("lead", "")).strip(),
                         "items": items, "evidence_refs": refs, "inference": inference}
            elif block_type == "table":
                headers = [str(item).strip() for item in raw.get("headers", [])]
                rows = [[str(cell).strip() for cell in row] for row in raw.get("rows", [])
                        if isinstance(row, list)]
                rows = [row for row in rows if len(row) == len(headers)]
                if not headers or not rows:
                    continue
                block = {"type": block_type, "title": str(raw.get("title", "")).strip(),
                         "headers": headers, "rows": rows, "evidence_refs": refs,
                         "inference": inference}
            else:
                figure_type = raw.get("figure_type")
                figure_key = re.sub(r"[^a-z0-9_-]", "_", str(raw.get("figure_key", "")).lower())
                if figure_type not in ALLOWED_FIGURE_TYPES or not figure_key:
                    continue
                block = {"type": block_type, "figure_key": figure_key,
                         "figure_type": figure_type,
                         "title": str(raw.get("title", "")).strip(),
                         "purpose": str(raw.get("purpose", "")).strip(),
                         "evidence_refs": refs}
                figures.append(block)
            if block_type != "figure_request" and not refs:
                needs_review = True
            if inference:
                inference_notes.append({"block_index": len(blocks), "reason": "AI 合理推断"})
            evidence_refs.update(refs)
            blocks.append(block)
        substantive = [block for block in blocks if block["type"] != "figure_request"]
        if len(substantive) < 3:
            raise ManualDraftingError("章节内容过少，未达到可审阅标准")
        return {
            "title": str(payload.get("title") or "").strip(), "blocks": blocks,
            "evidence_refs": sorted(evidence_refs), "inference_notes": inference_notes,
            "figure_requests": figures,
            "status": "needs_review" if needs_review else "generated",
        }

    def _persist_section(self, context: dict, section_key: str, title: str, ordinal: int,
                         content: dict, origin: str, prompt_fingerprint: Optional[str],
                         elapsed_ms: int) -> dict:
        now = utc_now()
        blocks_json = json.dumps(content["blocks"], ensure_ascii=False, separators=(",", ":"))
        refs_json = json.dumps(content["evidence_refs"], ensure_ascii=False, separators=(",", ":"))
        inference_json = json.dumps(content["inference_notes"], ensure_ascii=False,
                                    separators=(",", ":"))
        figures_json = json.dumps(content["figure_requests"], ensure_ascii=False,
                                  separators=(",", ":"))
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 value FROM manual_section_revisions
                WHERE job_id = ? AND section_key = ?""",
                (context["job_id"], section_key),
            ).fetchone()["value"]
            revision_id = str(uuid4())
            connection.execute(
                """INSERT INTO manual_section_revisions(id, job_id, section_key, version,
                origin, title, status, content_json, evidence_refs_json, inference_notes_json,
                figure_requests_json, model_name, prompt_fingerprint, elapsed_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (revision_id, context["job_id"], section_key, version, origin, title,
                 content["status"], blocks_json, refs_json, inference_json, figures_json,
                 context["model_name"] if origin == "ai" else None,
                 prompt_fingerprint, elapsed_ms, now),
            )
            connection.execute(
                """INSERT INTO manual_section_artifacts(id, job_id, section_key, title,
                ordinal, status, content_json, evidence_refs_json, inference_notes_json,
                figure_requests_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, section_key) DO UPDATE SET title=excluded.title,
                ordinal=excluded.ordinal, status=excluded.status,
                content_json=excluded.content_json, evidence_refs_json=excluded.evidence_refs_json,
                inference_notes_json=excluded.inference_notes_json,
                figure_requests_json=excluded.figure_requests_json,
                updated_at=excluded.updated_at""",
                (str(uuid4()), context["job_id"], section_key, title, ordinal,
                 content["status"], blocks_json, refs_json, inference_json, figures_json, now),
            )
        return {"section_key": section_key, "title": title, "ordinal": ordinal,
                "version": version, "origin": origin, "status": content["status"],
                "blocks": content["blocks"], "evidence_refs": content["evidence_refs"],
                "inference_notes": content["inference_notes"],
                "figure_requests": content["figure_requests"], "elapsed_ms": elapsed_ms,
                "updated_at": now}

    def _finish_step(self, job_id: str, step_id: str, generated: list,
                     errors: list, failed: bool) -> None:
        now = utc_now()
        if failed:
            status, job_status, current, completed = "failed", "failed", "draft", 1
        else:
            status = "completed_with_warnings" if errors else "completed"
            job_status, current, completed = "running", "diagrams", 2
        summary = {"generated_sections": len(generated), "failed_sections": len(errors),
                   "errors": errors}
        progress = {"completed": completed, "total": 6,
                    "percent": round(completed / 6 * 100)}
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_generation_steps SET status = ?, summary_json = ?,
                finished_at = ?, safe_error_message = ? WHERE id = ?""",
                (status, json.dumps(summary, ensure_ascii=False, separators=(",", ":")), now,
                 errors[0]["message"] if failed and errors else None, step_id),
            )
            connection.execute(
                """UPDATE manual_generation_jobs SET status = ?, current_step = ?,
                progress_json = ?, updated_at = ?, safe_error_message = ? WHERE id = ?""",
                (job_status, current, json.dumps(progress, separators=(",", ":")), now,
                 errors[0]["message"] if failed and errors else None, job_id),
            )

    def _refresh_draft_summary(self, job_id: str) -> None:
        sections = self.list_sections(job_id)
        with self._database.connect() as connection:
            step = connection.execute(
                """SELECT id FROM manual_generation_steps WHERE job_id = ? AND step_key = 'draft'
                ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if step:
                summary = {"generated_sections": len(sections),
                           "needs_review_sections": sum(item["status"] == "needs_review"
                                                        for item in sections)}
                connection.execute(
                    "UPDATE manual_generation_steps SET summary_json = ? WHERE id = ?",
                    (json.dumps(summary, ensure_ascii=False, separators=(",", ":")), step["id"]),
                )

    def _current(self, job_id: str, section_key: str):
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM manual_section_artifacts WHERE job_id = ? AND section_key = ?",
                (job_id, section_key),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _section_dict(row) -> dict:
        return {"id": row["id"], "section_key": row["section_key"],
                "title": row["title"], "ordinal": row["ordinal"],
                "status": row["status"], "version": row["revision_version"],
                "blocks": json.loads(row["content_json"]),
                "evidence_refs": json.loads(row["evidence_refs_json"]),
                "inference_notes": json.loads(row["inference_notes_json"]),
                "figure_requests": json.loads(row["figure_requests_json"]),
                "updated_at": row["updated_at"]}
