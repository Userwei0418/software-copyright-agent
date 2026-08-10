import hashlib
import json
import re
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from uuid import uuid4

from .credential_vault import CredentialVault
from .manual_generation import ManualGenerationService
from .service import utc_now
from .storage import Database


MAX_SOURCE_FILES = 14
MAX_SOURCE_CHARACTERS = 52_000
MAX_FILE_CHARACTERS = 8_000
ALLOWED_CLASSIFICATIONS = {"verified", "inference", "pending_confirmation"}


class ManualResearchError(ValueError):
    pass


class ManualResearchService:
    """Builds a traceable local clue graph and asks AI for evidence-bound research notes."""

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

    def execute(self, job_id: str) -> dict:
        self._database.initialize()
        context = self._start(job_id)
        started = time.monotonic()
        try:
            project_profile, fact_refs = self._project_profile(context["task_id"])
            source_refs = self._source_refs(context)
            if not source_refs:
                raise ManualResearchError("未找到可安全读取的代表性源码")
            prompt = self._prompt(
                context["project_name"], project_profile, fact_refs, source_refs
            )
            api_key = self._api_key(context)
            raw = self._model_call(
                context["model"], context["endpoint_mode"], api_key, prompt
            )
            research = self._normalize_response(raw, fact_refs, source_refs)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            return self._persist(
                context, project_profile, source_refs, research, prompt, elapsed_ms
            )
        except Exception as error:
            self._fail(job_id, error)
            if isinstance(error, ManualResearchError):
                raise
            raise ManualResearchError(str(error)) from error

    def latest(self, job_id: str) -> Optional[dict]:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM manual_research_artifacts WHERE job_id = ?
                ORDER BY version DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        path = self._data_root / "tasks" / self._task_id(job_id) / row["artifact_relative_path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["artifact_relative_path"] = row["artifact_relative_path"]
        payload["elapsed_ms"] = row["elapsed_ms"]
        payload["created_at"] = row["created_at"]
        return payload

    def _start(self, job_id: str) -> dict:
        now = utc_now()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.id job_id, j.task_id, j.model_config_id, j.status job_status,
                ps.display_name project_name, psn.scan_root_mode, psn.scan_root_path,
                psn.manifest_relative_path, mc.id model_id, mc.protocol_id, mc.base_url,
                mc.model_name, mc.credential_ref, mc.settings_json
                FROM manual_generation_jobs j
                JOIN tasks t ON t.id = j.task_id
                JOIN project_sources ps ON ps.id = t.source_id
                JOIN project_snapshots psn ON psn.id = t.snapshot_id
                JOIN model_configs mc ON mc.id = j.model_config_id AND mc.enabled = 1
                WHERE j.id = ?""",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ManualResearchError("说明书任务、项目快照或模型配置不存在")
            step = connection.execute(
                """SELECT id, status, attempt FROM manual_generation_steps
                WHERE job_id = ? AND step_key = 'research'
                ORDER BY attempt DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
            if step is None:
                raise ManualResearchError("说明书任务缺少研究阶段")
            if step["status"] == "running":
                raise ManualResearchError("项目研究正在执行，请勿重复提交")
            if step["status"] in {"completed", "completed_with_warnings"}:
                step_id, attempt = str(uuid4()), step["attempt"] + 1
                connection.execute(
                    """INSERT INTO manual_generation_steps(
                    id, job_id, step_key, status, attempt, summary_json, started_at)
                    VALUES (?, ?, 'research', 'running', ?, '{}', ?)""",
                    (step_id, job_id, attempt, now),
                )
            else:
                step_id, attempt = step["id"], step["attempt"]
                connection.execute(
                    """UPDATE manual_generation_steps SET status = 'running',
                    started_at = ?, finished_at = NULL, safe_error_message = NULL
                    WHERE id = ?""",
                    (now, step_id),
                )
            connection.execute(
                """UPDATE manual_generation_jobs SET status = 'running',
                current_step = 'research', started_at = COALESCE(started_at, ?),
                updated_at = ?, safe_error_message = NULL WHERE id = ?""",
                (now, now, job_id),
            )
        values = dict(row)
        settings = json.loads(values.pop("settings_json") or "{}")
        values["endpoint_mode"] = settings.get("endpoint_mode") or (
            ManualGenerationService._default_mode(values["protocol_id"])
        )
        values["model"] = {
            "id": values["model_id"], "protocol_id": values["protocol_id"],
            "base_url": values["base_url"], "model_name": values["model_name"],
        }
        values["step_id"], values["attempt"] = step_id, attempt
        return values

    def _project_profile(self, task_id: str) -> tuple:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT f.fact_key, f.value_json, f.status, f.confidence,
                f.evidence_ids_json FROM facts f WHERE f.task_id = ?
                AND f.status IN ('candidate', 'confirmed')
                ORDER BY CASE f.status WHEN 'confirmed' THEN 0 ELSE 1 END,
                f.confidence DESC, f.created_at DESC""",
                (task_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """SELECT e.id, e.kind, e.relative_path, e.locator_json, e.excerpt,
                e.confidence FROM evidence e JOIN tasks t ON t.snapshot_id = e.snapshot_id
                WHERE t.id = ? AND e.sensitivity = 'normal'""",
                (task_id,),
            ).fetchall()
        evidence = {
            row["id"]: {
                "ref": row["id"], "kind": row["kind"],
                "path": row["relative_path"],
                "locator": json.loads(row["locator_json"]),
                "excerpt": row["excerpt"], "confidence": row["confidence"],
            }
            for row in evidence_rows
        }
        profile, fact_refs = {}, []
        seen = set()
        for row in rows:
            if row["fact_key"] in seen:
                continue
            seen.add(row["fact_key"])
            refs = [ref for ref in json.loads(row["evidence_ids_json"]) if ref in evidence]
            profile[row["fact_key"]] = json.loads(row["value_json"])
            fact_refs.append({
                "key": row["fact_key"], "value": profile[row["fact_key"]],
                "status": row["status"], "confidence": row["confidence"],
                "evidence_refs": refs,
            })
        return profile, fact_refs

    def _source_refs(self, context: dict) -> list:
        task_root = self._data_root / "tasks" / context["task_id"]
        if context["scan_root_mode"] == "task":
            scan_root = task_root / PurePosixPath(context["scan_root_path"])
        else:
            scan_root = Path(context["scan_root_path"])
        secret_paths = self._secret_paths(task_root)
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT sc.relative_path, sc.grade, sc.score, sc.language
                FROM source_candidates sc JOIN source_plan_runs spr ON spr.id = sc.plan_run_id
                WHERE spr.task_id = ? AND spr.version = (
                    SELECT MAX(version) FROM source_plan_runs WHERE task_id = ?
                ) AND sc.selected = 1 AND sc.grade IN ('A', 'B')
                ORDER BY CASE sc.grade WHEN 'A' THEN 0 ELSE 1 END,
                sc.score DESC, sc.code_lines DESC""",
                (context["task_id"], context["task_id"]),
            ).fetchall()
        candidates = [dict(row) for row in rows]
        if not candidates:
            manifest = task_root / PurePosixPath(context["manifest_relative_path"])
            candidates = self._manifest_candidates(manifest)
        refs, total = [], 0
        for item in candidates:
            relative = item["relative_path"]
            if relative in secret_paths or len(refs) >= MAX_SOURCE_FILES:
                continue
            path = scan_root.joinpath(*PurePosixPath(relative).parts)
            snippet = self._read_source(path)
            if snippet is None or total + len(snippet["text"]) > MAX_SOURCE_CHARACTERS:
                continue
            ref = "source:{0}:L{1}-L{2}".format(
                relative, snippet["start_line"], snippet["end_line"]
            )
            refs.append({
                "ref": ref, "path": relative,
                "language": item.get("language"), "grade": item.get("grade", "B"),
                "score": item.get("score", 0), "start_line": snippet["start_line"],
                "end_line": snippet["end_line"], "sha256": snippet["sha256"],
                "excerpt": snippet["text"],
            })
            total += len(snippet["text"])
        return refs

    @staticmethod
    def _manifest_candidates(path: Path) -> list:
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("category") != "source" or item.get("is_binary"):
                continue
            relative = item["path"]
            lowered = relative.lower()
            if any(part in lowered.split("/") for part in (
                "test", "tests", "mock", "mocks", "vendor", "generated"
            )):
                continue
            score = 80 if re.search(r"(^|/)(app|main|server|service|controller)", lowered) else 50
            result.append({
                "relative_path": relative, "grade": "A" if score >= 70 else "B",
                "score": score, "language": item.get("language"),
            })
        return sorted(result, key=lambda item: (-item["score"], item["relative_path"]))

    @staticmethod
    def _read_source(path: Path) -> Optional[dict]:
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        lines = text.splitlines()
        if not lines:
            return None
        marker = re.compile(
            r"^\s*(?:class |def |async def |export |function |interface |type |"
            r"@(?:app|router)\.|CREATE TABLE|public class|func )",
            re.IGNORECASE,
        )
        first_marker = next((index for index, line in enumerate(lines) if marker.search(line)), 0)
        start = max(0, first_marker - 12)
        selected = lines[start:start + 180]
        rendered = "\n".join(
            "{0:05d} | {1}".format(index, line)
            for index, line in enumerate(selected, start=start + 1)
        )[:MAX_FILE_CHARACTERS]
        end_line = start + rendered.count("\n") + 1
        return {
            "text": rendered, "start_line": start + 1, "end_line": end_line,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    @staticmethod
    def _secret_paths(task_root: Path) -> set:
        report = task_root / "qa" / "scan-report.json"
        if not report.is_file():
            return set()
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        return {item["path"] for item in payload.get("secret_findings", []) if item.get("path")}

    def _api_key(self, context: dict) -> Optional[str]:
        if context["protocol_id"] == "ollama":
            return None
        try:
            return self._vault.read(context["credential_ref"] or context["model_id"])
        except ValueError as error:
            raise ManualResearchError("所选模型的 API Key 不存在，请在设置中重新配置") from error

    @staticmethod
    def _prompt(project_name: str, profile: dict, fact_refs: list, source_refs: list) -> str:
        evidence = {
            "project_profile": profile,
            "facts": fact_refs,
            "representative_source": source_refs,
        }
        return """你是中国软件著作权技术说明书的项目研究员。请基于证据研究项目“{0}”，而不是直接写成文章。

只返回一个 JSON 对象，不要 Markdown 代码围栏。格式：
{{
  "research_notes": [{{
    "topic": "主题",
    "classification": "verified|inference|pending_confirmation",
    "statement": "具体结论",
    "evidence_refs": ["证据 ref"],
    "confidence": 0.0
  }}],
  "section_guidance": [{{
    "section_key": "introduction|architecture|modules|data_interfaces|runtime|security_reliability|ui_operations|testing_summary",
    "focus": ["本章应说明的项目具体内容"],
    "evidence_refs": ["证据 ref"],
    "open_questions": ["确实无法从证据确定的问题"]
  }}]
}}

约束：
1. verified 必须至少引用一个输入中真实存在的 evidence ref 或 source ref。
2. inference 必须明确使用“根据……推断”的措辞并引用推断依据。
3. 不得编造客户、规模、性能数字、版本、外部系统或未实现功能。
4. pending_confirmation 只用于源码无法回答且会影响说明书真实性的信息。
5. 优先研究系统边界、入口、核心模块协作、数据生命周期、接口校验、错误恢复、部署与界面流程。
6. 文件路径仅用于证据引用，不应建议在最终正文中罗列内部路径。

证据输入：
{1}
""".format(project_name, json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _normalize_response(raw: str, fact_refs: list, source_refs: list) -> dict:
        if not raw or not raw.strip():
            raise ManualResearchError("模型返回了空研究结果")
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            first, last = text.find("{"), text.rfind("}")
            if first >= 0 and last > first:
                text = text[first:last + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ManualResearchError("模型未返回可解析的结构化研究结果") from error
        valid_refs = {
            ref for fact in fact_refs for ref in fact.get("evidence_refs", [])
        } | {item["ref"] for item in source_refs}
        notes = []
        for item in payload.get("research_notes", []):
            if not isinstance(item, dict) or not str(item.get("statement", "")).strip():
                continue
            classification = item.get("classification")
            if classification not in ALLOWED_CLASSIFICATIONS:
                classification = "pending_confirmation"
            refs = [ref for ref in item.get("evidence_refs", []) if ref in valid_refs]
            if classification == "verified" and not refs:
                classification = "pending_confirmation"
            statement = str(item["statement"]).strip()
            if classification == "inference" and "推断" not in statement:
                statement = "根据现有项目证据推断，" + statement
            notes.append({
                "topic": str(item.get("topic") or "未分类").strip(),
                "classification": classification, "statement": statement,
                "evidence_refs": refs,
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
            })
        if not notes:
            raise ManualResearchError("模型没有返回有效的研究结论")
        guidance = []
        for item in payload.get("section_guidance", []):
            if not isinstance(item, dict) or not item.get("section_key"):
                continue
            guidance.append({
                "section_key": str(item["section_key"]),
                "focus": [str(value) for value in item.get("focus", []) if str(value).strip()],
                "evidence_refs": [ref for ref in item.get("evidence_refs", []) if ref in valid_refs],
                "open_questions": [str(value) for value in item.get("open_questions", [])
                                   if str(value).strip()],
            })
        return {"research_notes": notes, "section_guidance": guidance}

    def _persist(self, context: dict, profile: dict, source_refs: list, research: dict,
                 prompt: str, elapsed_ms: int) -> dict:
        now = utc_now()
        input_fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with self._database.connect() as connection:
            version = connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 value
                FROM manual_research_artifacts WHERE job_id = ?""",
                (context["job_id"],),
            ).fetchone()["value"]
        job_id = context["job_id"]
        relative = "intermediate/manual-research/research.v{0}.json".format(version)
        payload = {
            "schema_version": 1, "job_id": job_id, "version": version,
            "project_profile": profile, "source_refs": source_refs,
            "research_notes": research["research_notes"],
            "section_guidance": research["section_guidance"],
            "model": context["model_name"], "input_fingerprint": input_fingerprint,
        }
        path = self._data_root / "tasks" / context["task_id"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        counts = {
            key: sum(1 for item in research["research_notes"]
                     if item["classification"] == key)
            for key in ALLOWED_CLASSIFICATIONS
        }
        warning = counts["pending_confirmation"] > 0
        status = "completed_with_warnings" if warning else "completed"
        summary = {
            "source_file_count": len(source_refs), "research_note_count": len(research["research_notes"]),
            "section_guidance_count": len(research["section_guidance"]), **counts,
            "artifact_relative_path": relative, "elapsed_ms": elapsed_ms,
        }
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_research_artifacts(id, job_id, version, status,
                project_profile_json, source_refs_json, notes_json, artifact_relative_path,
                input_fingerprint, model_name, elapsed_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), job_id, version, status,
                 json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                 json.dumps(source_refs, ensure_ascii=False, separators=(",", ":")),
                 json.dumps(research, ensure_ascii=False, separators=(",", ":")),
                 relative, input_fingerprint, context["model_name"], elapsed_ms, now),
            )
            connection.execute(
                """UPDATE manual_generation_steps SET status = ?, summary_json = ?,
                finished_at = ?, safe_error_message = NULL WHERE id = ?""",
                (status, json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                 now, context["step_id"]),
            )
            progress = {"completed": 1, "total": 6, "percent": 17}
            connection.execute(
                """UPDATE manual_generation_jobs SET status = 'running', current_step = 'draft',
                progress_json = ?, updated_at = ?, safe_error_message = NULL WHERE id = ?""",
                (json.dumps(progress, separators=(",", ":")), now, job_id),
            )
        payload.update({"status": status, "summary": summary, "created_at": now})
        return payload

    def _fail(self, job_id: str, error: Exception) -> None:
        now = utc_now()
        message = str(error).strip()[:500] or type(error).__name__
        with self._database.connect() as connection:
            step = connection.execute(
                """SELECT id FROM manual_generation_steps WHERE job_id = ?
                AND step_key = 'research' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if step:
                connection.execute(
                    """UPDATE manual_generation_steps SET status = 'failed', finished_at = ?,
                    safe_error_message = ? WHERE id = ?""", (now, message, step["id"]),
                )
            connection.execute(
                """UPDATE manual_generation_jobs SET status = 'failed', updated_at = ?,
                safe_error_message = ? WHERE id = ?""", (now, message, job_id),
            )

    def _task_id(self, job_id: str) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM manual_generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ManualResearchError("说明书生成任务不存在")
        return row["task_id"]

    def _job_id(self, step_id: str) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT job_id FROM manual_generation_steps WHERE id = ?", (step_id,)
            ).fetchone()
        if row is None:
            raise ManualResearchError("研究阶段不存在")
        return row["job_id"]
