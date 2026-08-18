import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Callable, Optional
from uuid import uuid4

from .app_settings import AppSettingsService, style_prompt
from .credential_vault import CredentialVault
from .manual_execution import ManualExecutionNodeService, manual_job_slot
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
MAX_GENERATION_ATTEMPTS = 3
ALLOWED_BLOCK_TYPES = {"subheading", "paragraph", "list", "table", "figure_request"}
ALLOWED_FIGURE_TYPES = {
    "architecture", "module", "workflow", "sequence", "er", "deployment", "data_flow",
}
REQUIRED_FIGURES = {
    "architecture": "architecture",
    "modules": "module",
    "data_interfaces": "data_flow",
    "runtime": "deployment",
}
REQUIRED_FIGURE_TITLES = {
    "architecture": "系统总体架构图",
    "modules": "核心模块协作图",
    "data_interfaces": "核心数据流图",
    "runtime": "系统部署架构图",
}
UNVERIFIED_OUTCOME_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"各项(?:技术)?指标.{0,16}(?:符合|达到).{0,12}(?:预期|要求)",
    r"(?:均|全部).{0,18}通过(?:实际)?(?:请求|运行|测试)?验证",
    r"(?:已|均已).{0,18}通过验收",
    r"(?:提供|形成).{0,8}充分依据",
    r"具备上线(?:运行)?条件",
    r"可直接上线",
    r"已全面验证",
    r"(?:测试(?:用例|过程|文件|框架)?|用例).{0,40}(?:验证|确认).{0,40}(?:完整|准确|有效|正确|一致|预期|功能)",
    r"测试文件.{0,24}覆盖了.{0,36}(?:基础功能|核心功能|全部功能|完整链路)",
    r"(?:验证|确认)了.{0,40}(?:完整链路|准确性|有效性|正确性|一致性)",
    r"进行了(?:专项|相应)?验证.{0,36}(?:确保|确认|表明|证明)",
    r"(?:测试(?:结构|文件|用例|过程)?|用例).{0,45}(?:提供|构成).{0,25}(?:保障|依据|防线)",
    r"(?:测试(?:结构|文件|用例|过程)?|用例).{0,45}确保.{0,30}",
))
TEST_INTENT_MARKERS = re.compile(r"是否|用于|用以|以便|旨在|模拟|构造|断言|检查点")
TEST_COMPLETION_MARKERS = re.compile(
    r"已经|已通过|均已|全部通过|验证了|确认了|测试结果|结果表明|确保|证明"
)
EVIDENCE_BOUND_TESTING_SENTENCE = (
    "项目材料中可识别到相关文件、断言目标或校验逻辑；本文仅记录源码直接呈现的检查点，"
    "不表述为已经执行的结果。"
)


class ManualDraftingError(ValueError):
    pass


def unverified_outcome_hits(text: str) -> list:
    """Return unsupported result claims without flagging test-plan language.

    A sentence that says a test *constructs*, *simulates* or checks *whether* a
    condition holds describes a method, not a completed result.  Treating those
    phrases as proof claims made Quick Start retry an unchanged document forever.
    """
    hits = []
    for pattern in UNVERIFIED_OUTCOME_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = match.group(0)
            if TEST_INTENT_MARKERS.search(value) and not TEST_COMPLETION_MARKERS.search(value):
                continue
            hits.append(value)
    return sorted(set(hits))


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

    def generate_all(self, job_id: str, on_section_completed=None) -> dict:
        self._database.initialize()
        context = self._context(job_id)
        research = self._research(job_id, context["task_id"])
        step_id = self._start_step(job_id)
        generated, errors = [], []
        blueprints = self._blueprints(research)
        concurrency = AppSettingsService(self._database).effective_concurrency(
            context["model_id"]
        )
        state_lock = Lock()
        execution = ManualExecutionNodeService(self._database)
        completed = 0
        states = {
            section_key: {"key": section_key, "title": title, "status": "queued",
                          "attempt": 0, "started_at": None, "finished_at": None,
                          "error": None}
            for section_key, title, _ in blueprints
        }
        for section_key, title, ordinal in blueprints:
            execution.prepare(
                job_id, "section:{0}".format(section_key), "draft", "section", title,
                dependencies=["research"], model_config_id=context["model_id"],
                max_attempts=MAX_GENERATION_ATTEMPTS,
                input_value={"section_key": section_key, "ordinal": ordinal},
            )

        def publish(current_title: str) -> None:
            self._update_progress(
                job_id, step_id, completed, len(blueprints), current_title,
                list(states.values()), concurrency,
            )

        def run_section(blueprint: tuple) -> dict:
            section_key, title, ordinal = blueprint
            node_key = "section:{0}".format(section_key)
            with manual_job_slot(job_id, concurrency):
                execution.running(job_id, node_key, 1)
                with state_lock:
                    states[section_key].update(
                        status="running", attempt=1, started_at=utc_now(), error=None
                    )
                    publish(title)

                def on_retry(attempt: int, reason: str) -> None:
                    execution.heartbeat(job_id, node_key, attempt, reason)
                    with state_lock:
                        states[section_key].update(attempt=attempt, error=reason[:180])
                        publish("{0}（第 {1} 次校正）".format(title, attempt))

                return self._generate(
                    context, research, section_key, title, ordinal, origin="ai",
                    on_retry=on_retry,
                )

        publish("准备并发生成章节")
        with ThreadPoolExecutor(max_workers=concurrency,
                                thread_name_prefix="manual-section") as executor:
            futures = {executor.submit(run_section, blueprint): blueprint
                       for blueprint in blueprints}
            for future in as_completed(futures):
                section_key, title, _ = futures[future]
                error_message = None
                try:
                    result = future.result()
                    generated.append(result)
                    execution.complete(
                        job_id, "section:{0}".format(section_key),
                        {"version": result["version"], "elapsed_ms": result["elapsed_ms"],
                         "figure_request_count": len(result.get("figure_requests", [])),
                         "next_action": "审阅正文；本章图表请求已立即进入独立队列"},
                    )
                    if on_section_completed:
                        on_section_completed(result)
                except Exception as error:
                    error_message = str(error)[:300]
                    errors.append({"section_key": section_key, "message": error_message})
                    execution.fail(
                        job_id, "section:{0}".format(section_key), error_message,
                        "content_validation",
                    )
                with state_lock:
                    completed += 1
                    states[section_key].update(
                        status="failed" if error_message else "completed",
                        finished_at=utc_now(), error=error_message,
                    )
                    publish("已完成 {0}/{1} 章".format(completed, len(blueprints)))
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
        section_key, title, ordinal = blueprint
        execution = ManualExecutionNodeService(self._database)
        node_key = "section:{0}".format(section_key)
        current = next((item for item in execution.list(job_id) if item["key"] == node_key), None)
        attempt = (current["attempt"] if current else 0) + 1
        execution.prepare(
            job_id, node_key, "draft", "section", title, dependencies=["research"],
            model_config_id=context["model_id"],
            max_attempts=attempt + MAX_GENERATION_ATTEMPTS - 1,
            input_value={"section_key": section_key, "ordinal": ordinal},
        )
        concurrency = AppSettingsService(self._database).effective_concurrency(
            context["model_id"]
        )
        execution.queued(job_id, node_key)
        try:
            with manual_job_slot(job_id, concurrency):
                execution.running(job_id, node_key, attempt)
                result = self._generate(
                    context, research, section_key, title, ordinal, origin="ai",
                    on_retry=lambda retry, reason: execution.heartbeat(
                        job_id, node_key, attempt + retry - 1, reason
                    ),
                )
            execution.complete(job_id, node_key, {
                "version": result["version"], "elapsed_ms": result["elapsed_ms"],
                "next_action": "审阅正文；相关图表和截图节点可独立执行",
            })
        except Exception as error:
            execution.fail(job_id, node_key, str(error), "content_validation")
            raise
        self._refresh_draft_summary(job_id)
        return result

    def generate_ui_from_screenshots(self, job_id: str, project_profile: dict,
                                     screenshots: list) -> dict:
        """Generate chapter 7 only from reviewed, version-pinned screenshot evidence."""
        if not screenshots:
            raise ManualDraftingError("用户界面章节正在等待已审核并确认采用的真实截图")
        context = self._context(job_id)
        evidence_refs = ["screenshot:{0}:v{1}".format(
            item["id"], item["interpretation_version"]
        ) for item in screenshots]
        evidence = [{
            "ref": ref, "asset_id": item["id"], "title": item["title"],
            "group": item["group_title"], "order": item["sort_order"],
            "caption": item["interpretation"].get("suggested_caption", ""),
            "interpretation": item["interpretation"],
        } for ref, item in zip(evidence_refs, screenshots)]
        prompt = """你是中国软件著作权说明书撰写专家。请只根据已审核的真实截图解读和项目概要，
撰写第 7 章“用户界面与操作说明”。不要补写图片或证据未支持的页面、权限、后台动作或成功结果。
只返回 JSON：{{"section_key":"ui_operations","title":"用户界面与操作说明","blocks":[
{{"type":"subheading","title":"页面组标题","evidence_refs":["screenshot:..."]}},
{{"type":"paragraph","text":"结合对应界面图号说明页面用途、可见区域和操作反馈的正文","evidence_refs":["screenshot:..."]}},
{{"type":"list","lead":"操作步骤","items":["步骤"],"evidence_refs":["screenshot:..."]}}
]}}。
要求：按页面组和 sort_order 排列；正文明确使用“如界面图所示”等引用措辞；每张采用截图至少被一个块引用；
每个块必须引用下列真实 ref；不得输出 figure_request，不得生成截图之外的新页面。全章 600 至 1600 字。
项目概要：{0}
已审核截图证据：{1}""".format(
            json.dumps(project_profile, ensure_ascii=False)[:16000],
            json.dumps(evidence, ensure_ascii=False)[:30000],
        )
        started, errors, normalized = time.monotonic(), [], None
        api_key = self._api_key(context)
        for attempt in range(1, 3):
            current_prompt = prompt if attempt == 1 else prompt + (
                "\n上一次 JSON 未通过本地校验：{0}。只修复格式和证据引用后返回完整 JSON。"
                .format(errors[-1])
            )
            try:
                raw = self._model_call(context["model"], context["endpoint_mode"],
                                       api_key, current_prompt)
                normalized = self._normalize_ui_payload(
                    self._parse_json(raw), evidence_refs
                )
                break
            except ManualDraftingError as error:
                errors.append(str(error))
        if normalized is None:
            raise ManualDraftingError("AI 两次未生成合格的截图驱动用户界面章节：{0}".format(
                errors[-1] if errors else "未知错误"))
        result = self._persist_section(
            context, "ui_operations", normalized.get("title") or "用户界面与操作说明",
            7, normalized, origin="ai",
            prompt_fingerprint=hashlib.sha256(prompt.encode()).hexdigest(),
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        result["screenshot_refs"] = evidence_refs
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
        del research
        # Chapter 7 is intentionally absent from the source-only drafting fan-out.
        # It is created later from reviewed, version-pinned screenshot evidence.
        return tuple(item for item in SECTION_BLUEPRINTS if item[0] != "ui_operations")

    @staticmethod
    def _normalize_ui_payload(payload: dict, ordered_refs: list) -> dict:
        valid_refs = set(ordered_refs)
        if payload.get("section_key", "ui_operations") != "ui_operations":
            raise ManualDraftingError("模型返回了错误的用户界面章节标识")
        blocks, used_refs, character_count = [], set(), 0
        for raw in payload.get("blocks", []):
            if not isinstance(raw, dict) or raw.get("type") not in {
                "subheading", "paragraph", "list"
            }:
                continue
            refs = [ref for ref in raw.get("evidence_refs", []) if ref in valid_refs]
            if not refs:
                continue
            if raw["type"] == "subheading":
                title = str(raw.get("title", "")).strip()[:60]
                if len(title) < 2:
                    continue
                block = {"type": "subheading", "title": title,
                         "evidence_refs": refs, "inference": False}
            elif raw["type"] == "paragraph":
                text = str(raw.get("text", "")).strip()
                if len(text) < 40:
                    continue
                character_count += len(text)
                block = {"type": "paragraph", "text": text,
                         "evidence_refs": refs, "inference": False}
            else:
                items = [str(item).strip() for item in raw.get("items", [])
                         if len(str(item).strip()) >= 6]
                if not items:
                    continue
                lead = str(raw.get("lead", "")).strip()
                character_count += len(lead) + sum(len(item) for item in items)
                block = {"type": "list", "lead": lead, "items": items,
                         "evidence_refs": refs, "inference": False}
            used_refs.update(refs)
            blocks.append(block)
        if character_count < 500 or len(blocks) < 4:
            raise ManualDraftingError("截图驱动章节内容不足")
        missing = sorted(valid_refs - used_refs)
        if missing:
            raise ManualDraftingError("部分采用截图没有正文引用：" + "、".join(missing[:5]))
        # The reviewed screenshot order is authoritative.  A vision model can still
        # return otherwise valid page groups in a different JSON order, which used
        # to turn a recoverable presentation issue into a permanently failed node.
        # Canonicalise citation metadata locally before the final validation: keep
        # the prose intact, order blocks by their earliest reviewed screenshot and
        # defer a premature first citation to the nearest later block when needed.
        ref_position = {ref: index for index, ref in enumerate(ordered_refs)}
        for block in blocks:
            block["evidence_refs"] = sorted(
                dict.fromkeys(block.get("evidence_refs", [])),
                key=lambda ref: ref_position[ref],
            )
        blocks.sort(key=lambda block: min(
            ref_position[ref] for ref in block.get("evidence_refs", [])
        ))

        first_owner = {}
        for index, block in enumerate(blocks):
            for ref in block.get("evidence_refs", []):
                first_owner.setdefault(ref, index)
        previous_owner = 0
        for ref in ordered_refs:
            owner = max(first_owner[ref], previous_owner)
            first_owner[ref] = owner
            previous_owner = owner
        for index, block in enumerate(blocks):
            block["evidence_refs"] = [
                ref for ref in block.get("evidence_refs", [])
                if index >= first_owner[ref]
            ]
        for ref in ordered_refs:
            owner = first_owner[ref]
            refs = blocks[owner]["evidence_refs"]
            if ref not in refs:
                refs.append(ref)
            refs.sort(key=lambda item: ref_position[item])

        first_seen = []
        for block in blocks:
            for ref in block.get("evidence_refs", []):
                if ref not in first_seen:
                    first_seen.append(ref)
        if first_seen != ordered_refs:
            raise ManualDraftingError("用户界面章节的截图引用顺序与人工确认的页面组顺序不一致")
        return {"title": str(payload.get("title") or "用户界面与操作说明").strip(),
                "blocks": blocks, "evidence_refs": list(ordered_refs),
                "inference_notes": [], "figure_requests": [], "status": "generated"}

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

    def _update_progress(self, job_id: str, step_id: str, completed_items: int,
                         total_items: int, current_title: str,
                         items: Optional[list] = None, concurrency: int = 1) -> None:
        """Persist sub-step progress so navigation/reload can recover long AI work."""
        fraction = completed_items / total_items if total_items else 1
        percent = round((1 + fraction) / 6 * 100)
        summary = {
            "completed_items": completed_items,
            "total_items": total_items,
            "current_title": current_title,
            "concurrency": concurrency,
        }
        if items is not None:
            summary["items"] = items
        progress = {
            "completed": 1,
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

    def _generate(self, context: dict, research: dict, section_key: str, title: str,
                  ordinal: int, origin: str, on_retry=None) -> dict:
        prompt = self._prompt(context["project_name"], section_key, title, research)
        started = time.monotonic()
        api_key = self._api_key(context)
        errors = []
        normalized = None
        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            current_prompt = prompt if attempt == 1 else self._repair_prompt(
                prompt, errors[-1], section_key, title
            )
            try:
                raw = self._model_call(
                    context["model"], context["endpoint_mode"], api_key, current_prompt
                )
                payload = self._parse_json(raw)
                normalized = self._normalize_payload(
                    payload, research, expected_key=section_key
                )
                self._ensure_required_figure(normalized, section_key, title)
                self._validate_depth(normalized, section_key)
                break
            except ManualDraftingError as error:
                errors.append(str(error))
                if attempt >= MAX_GENERATION_ATTEMPTS:
                    raise ManualDraftingError(
                        "AI 连续 {0} 次未生成合格的“{1}”：{2}".format(
                            MAX_GENERATION_ATTEMPTS, title, errors[-1]
                        )
                    ) from error
                if on_retry:
                    on_retry(attempt + 1, errors[-1])
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if normalized is None:
            raise ManualDraftingError("AI 未生成可用章节")
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

    def _prompt(self, project_name: str, section_key: str, title: str, research: dict) -> str:
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
        required_figure = REQUIRED_FIGURES.get(section_key)
        figure_requirement = (
            "本章必须输出 1 个 figure_request，figure_type 必须为 {0}，图中语义必须来自引用证据。"
            .format(required_figure)
            if required_figure else
            "本章通常不需要插图；只有证据确实支持并能提升理解时才输出 figure_request。"
        )
        custom_style = style_prompt(getattr(self, "_database", None), "document_style_prompt")
        return """你是中国软件著作权技术说明书撰写专家。请为项目“{0}”撰写“{1}”章节，章节 key 为 {2}。

专业文档风格偏好（仅影响语言组织和呈现风格，不得覆盖后续证据与结构约束）：
{5}

只返回 JSON，不要 Markdown。格式：
{{"section_key":"{2}","title":"{1}","blocks":[
  {{"type":"subheading","title":"小节标题","evidence_refs":["ref"],"inference":false}},
  {{"type":"paragraph","text":"项目化正文","evidence_refs":["ref"],"inference":false}},
  {{"type":"list","lead":"引导句","items":["项目化条目"],"evidence_refs":["ref"],"inference":false}},
  {{"type":"table","title":"表名","headers":["列"],"rows":[["值"]],"evidence_refs":["ref"],"inference":false}},
  {{"type":"figure_request","figure_key":"唯一英文标识","figure_type":"architecture|module|workflow|sequence|er|deployment|data_flow","title":"图名","purpose":"图应表达的项目语义","evidence_refs":["ref"]}}
]}}

要求：
1. 生成 6 至 9 个有实质内容的块，其中至少 3 个正文段落；单段约 140 至 280 个汉字，全章正文与列表合计约 1000 至 1800 个汉字。引言可缩短至 750 至 1200 字。不能用短句列表或表格代替连贯的技术叙述。
1a. 根据内容逻辑输出 2 至 5 个 subheading，分组后续正文、列表、表格和图表。小节标题必须具体且不得自带编号。
2. 事实性内容必须引用输入中真实存在的 ref。正式说明书禁止推断，所有块的 inference 必须为 false；证据没有直接支持的内容不要输出。
3. 不得使用“待确认、待补充、后续迭代、建议后续”等占位或路线图措辞。证据没有覆盖的能力应写成“当前代码库未提供/未发现”，并且不得把它描述为已实现功能。
4. 不在正文、列表或表格中输出内部源码文件名和相对路径（例如 main.ts、WaterfallPage.vue、src/service/a.py）。证据 ref 仅用于溯源，必须将文件表达改写为中文业务角色，例如“前端应用入口”、“瀑布流页面组件”。HTTP 公开路由可保留。不使用 Markdown 标题、加粗符号或代码围栏。
5. 每章最多请求 1 张图；只有确实提升理解时才请求。表格仅用于真正可比较的信息，连续章节不得都用同一种表格组织正文。
6. 必须写出证据中出现的真实模块、接口、数据对象、状态或技术组件，并说明职责、输入、处理、输出、异常与恢复；避免“提高效率、保证稳定”等空泛套话。
7. 不重复项目概述。总体设计讲边界和协作，功能设计讲业务流程，数据与接口讲字段/校验/状态，运行部署讲真实依赖，安全可靠讲实际机制，界面操作讲真实入口和反馈，测试总结讲可验证的检查点。
8. 每一个小节标题、正文、列表、表格和图表请求都必须至少引用一个真实 ref；没有 ref 的内容不要输出。
9. {4}
10. 除非输入中存在明确的测试报告、运行日志或验收记录，不得声称“指标达到预期、均通过验证、通过验收、具备上线条件、可直接上线、已全面验证”。只能客观描述源码中存在的测试文件、校验逻辑、部署配置和可验证检查点。
11. 不得根据框架惯例补写实现细节。TTL、端口、盐值、重试次数、设备上限、角色名、缓存策略、审计日志、备份恢复、预签名有效期等精确参数或安全机制，只有在 evidence excerpt 直接出现时才能写入；否则明确写“当前代码证据未显示该配置”，不要猜测。
12. “存在测试文件”不等于“测试已验证业务正确”。没有测试运行结果时，只能描述测试对象、断言目标和可执行检查点，禁止使用“测试确认了、测试验证了、用例覆盖并证明了”等完成性结论。

研究证据：
{3}
""".format(project_name, title, section_key,
           json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
           figure_requirement, custom_style or "使用系统默认的正式技术文风。")

    @staticmethod
    def _repair_prompt(original: str, reason: str, section_key: str, title: str) -> str:
        return original + """

上一次输出未通过本地校验：{0}。
请重新完整输出“{1}”（section_key={2}）的单个 JSON 对象。不要解释，不要代码围栏；
检查所有字符串引号、逗号、方括号和花括号是否闭合。正文必须达到要求的块数和篇幅，
且每个正文、列表、表格都必须引用输入中真实存在的 evidence ref。
""".format(reason, title, section_key)

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
            raise ManualDraftingError(
                "结构化 JSON 不完整（第 {0} 个字符附近：{1}）".format(
                    error.pos, error.msg
                )
            ) from error

    @staticmethod
    def _validate_depth(content: dict, section_key: str) -> None:
        substantive = [
            block for block in content["blocks"]
            if block["type"] not in {"figure_request", "subheading"}
        ]
        paragraphs = [block for block in substantive if block["type"] == "paragraph"]
        paragraph_characters = sum(len(block.get("text", "")) for block in paragraphs)
        character_count = 0
        for block in substantive:
            if block["type"] == "paragraph":
                character_count += len(block.get("text", ""))
            elif block["type"] == "list":
                character_count += len(block.get("lead", "")) + sum(
                    len(item) for item in block.get("items", [])
                )
            elif block["type"] == "table":
                character_count += sum(len(item) for item in block.get("headers", []))
                character_count += sum(
                    len(cell) for row in block.get("rows", []) for cell in row
                )
        minimum = 750 if section_key == "introduction" else 950
        # Lists and tables support a technical narrative but cannot replace it.
        # Requiring two developed paragraphs prevents a checklist-shaped result
        # from being polished into a formal Word document that only looks complete.
        if (len(substantive) < 4 or len(paragraphs) < 2 or
                paragraph_characters < 320 or character_count < minimum):
            raise ManualDraftingError(
                "章节深度不足（正文块 {0}/4、段落 {1}/2、叙述文字 {2}/320、有效文字 {3}/{4}）".format(
                    len(substantive), len(paragraphs), paragraph_characters,
                    character_count, minimum
                )
            )
        required_figure = REQUIRED_FIGURES.get(section_key)
        if required_figure and not any(
            block.get("type") == "figure_request" and
            block.get("figure_type") == required_figure
            for block in content["blocks"]
        ):
            raise ManualDraftingError(
                "本章缺少必要的 {0} 可编辑图表语义".format(required_figure)
            )

    @staticmethod
    def _ensure_required_figure(content: dict, section_key: str, section_title: str) -> None:
        figure_type = REQUIRED_FIGURES.get(section_key)
        if not figure_type or any(
            block.get("type") == "figure_request" and
            block.get("figure_type") == figure_type
            for block in content["blocks"]
        ):
            return
        refs = list(content.get("evidence_refs", []))
        if not refs:
            return
        request = {
            "type": "figure_request",
            "figure_key": "{0}_{1}".format(section_key, figure_type),
            "figure_type": figure_type,
            "title": REQUIRED_FIGURE_TITLES.get(section_key, section_title + "图"),
            "purpose": "依据本章已引用证据呈现“{0}”中的主要对象、边界与关系。".format(
                section_title
            ),
            "evidence_refs": refs,
        }
        content["blocks"].append(request)
        content["figure_requests"].append(request)

    @staticmethod
    def _normalize_payload(payload: dict, research: dict, expected_key: str) -> dict:
        if payload.get("section_key", expected_key) != expected_key:
            raise ManualDraftingError("模型返回了错误的章节标识")
        valid_refs = {item["ref"] for item in research.get("source_refs", [])}
        valid_refs.update(ref for note in research.get("research_notes", [])
                          for ref in note.get("evidence_refs", []))
        blocks, evidence_refs, inference_notes, figures = [], set(), [], []
        for raw in payload.get("blocks", []):
            if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_BLOCK_TYPES:
                continue
            block_type = raw["type"]
            refs = [ref for ref in raw.get("evidence_refs", []) if ref in valid_refs]
            inference = bool(raw.get("inference", False))
            if not refs:
                # Unsupported prose must not survive into a formal manual. It is
                # safer to omit one model block than to turn an untraceable claim
                # into a polished Word paragraph that appears authoritative.
                continue
            if inference:
                # Formal registration materials must not turn a plausible model
                # inference into a polished statement of implemented behaviour.
                continue
            if block_type == "subheading":
                title = re.sub(r"^\s*\d+(?:\.\d+)*[\s、.:-]*", "", str(
                    raw.get("title", "")
                )).strip()
                if len(title) < 4 or len(title) > 40:
                    continue
                block = {"type": block_type, "title": title, "evidence_refs": refs,
                         "inference": False}
            elif block_type == "paragraph":
                text = ManualDraftingService._sanitize_unverified_outcomes(
                    str(raw.get("text", "")).strip()
                )
                if len(text) < 20:
                    continue
                if inference and "推断" not in text:
                    text = "根据项目证据推断，" + text
                block = {"type": block_type, "text": text, "evidence_refs": refs,
                         "inference": inference}
            elif block_type == "list":
                items = [ManualDraftingService._sanitize_unverified_outcomes(
                    str(item).strip()) for item in raw.get("items", [])
                    if len(str(item).strip()) >= 8]
                if not items:
                    continue
                block = {"type": block_type, "lead": ManualDraftingService._sanitize_unverified_outcomes(
                    str(raw.get("lead", "")).strip()),
                         "items": items, "evidence_refs": refs, "inference": inference}
            elif block_type == "table":
                headers = [ManualDraftingService._sanitize_unverified_outcomes(
                    str(item).strip()) for item in raw.get("headers", [])]
                rows = [[ManualDraftingService._sanitize_unverified_outcomes(
                    str(cell).strip()) for cell in row] for row in raw.get("rows", [])
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
            if inference:
                inference_notes.append({"block_index": len(blocks), "reason": "AI 合理推断"})
            evidence_refs.update(refs)
            blocks.append(block)
        substantive = [block for block in blocks
                       if block["type"] not in {"figure_request", "subheading"}]
        if len(substantive) < 3:
            raise ManualDraftingError("章节内容过少，未达到可审阅标准")
        return {
            "title": str(payload.get("title") or "").strip(), "blocks": blocks,
            "evidence_refs": sorted(evidence_refs), "inference_notes": inference_notes,
            "figure_requests": figures,
            "status": "generated",
        }

    @staticmethod
    def _sanitize_unverified_outcomes(text: str) -> str:
        """Keep test intent while removing completion claims unsupported by run evidence."""
        if not text or not unverified_outcome_hits(text):
            return text
        parts = re.split(r"(?<=[。！？；])", text)
        normalized = []
        for part in parts:
            if not part:
                continue
            replacement = (EVIDENCE_BOUND_TESTING_SENTENCE
                           if unverified_outcome_hits(part) else part)
            if not normalized or normalized[-1] != replacement:
                normalized.append(replacement)
        return "".join(normalized).strip()

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
        return {"id": revision_id, "section_key": section_key, "title": title, "ordinal": ordinal,
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
        progress = {"completed": completed, "total": 6,
                    "percent": round(completed / 6 * 100)}
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT summary_json FROM manual_generation_steps WHERE id = ?", (step_id,),
            ).fetchone()
            summary = json.loads(row["summary_json"] or "{}") if row else {}
            summary.update({"generated_sections": len(generated),
                            "failed_sections": len(errors),
                            "completed_items": len(generated) + len(errors),
                            "total_items": len(generated) + len(errors), "errors": errors})
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
                """SELECT id, summary_json FROM manual_generation_steps WHERE job_id = ? AND step_key = 'draft'
                ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if step:
                summary = json.loads(step["summary_json"] or "{}")
                summary.update({"generated_sections": len(sections),
                                "needs_review_sections": sum(
                                    item["status"] == "needs_review" for item in sections)})
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
