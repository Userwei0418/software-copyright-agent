import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from .credential_vault import CredentialVault
from .manual_plan_service import ManualPlanService
from .service import utc_now
from .storage import Database


class ManualGenerationError(ValueError):
    pass


class ManualGenerationService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._vault = CredentialVault(database, data_root)
        self._planner = ManualPlanService(database, data_root)

    def execute(self, task_id: str, model_config_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ManualGenerationError("任务不存在")
            if task["status"] not in {"completed", "completed_with_warnings", "failed"}:
                raise ManualGenerationError("项目扫描尚未完成")
            config = connection.execute(
                """SELECT id, protocol_id, base_url, model_name, credential_ref, settings_json
                FROM model_configs WHERE id = ? AND enabled = 1""", (model_config_id,)
            ).fetchone()
            if config is None:
                raise ManualGenerationError("所选模型不存在或已停用")
            plan = connection.execute(
                """SELECT artifact_relative_path FROM manual_plan_runs WHERE task_id = ?
                ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
        if plan is None:
            self._planner.execute(task_id)
        prompt, plan_version = self._build_prompt(task_id)
        settings = json.loads(config["settings_json"] or "{}")
        endpoint_mode = settings.get("endpoint_mode") or self._default_mode(config["protocol_id"])
        try:
            api_key = None if config["protocol_id"] == "ollama" else self._vault.read(
                config["credential_ref"] or config["id"]
            )
        except ValueError as error:
            raise ManualGenerationError("所选模型的 API Key 不存在，请在设置中重新配置") from error
        started = time.monotonic()
        content = self._call_model(dict(config), endpoint_mode, api_key, prompt)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if not content.strip():
            raise ManualGenerationError("模型返回了空内容")
        return self._persist(task_id, model_config_id, config["model_name"], endpoint_mode,
                             plan_version, content.strip(), elapsed_ms)

    def _build_prompt(self, task_id: str):
        with self._database.connect() as connection:
            plan_row = connection.execute(
                """SELECT version, artifact_relative_path FROM manual_plan_runs WHERE task_id = ?
                ORDER BY version DESC LIMIT 1""", (task_id,)
            ).fetchone()
            facts = connection.execute(
                """SELECT fact_key, value_json, confidence FROM facts WHERE task_id = ?
                ORDER BY confidence DESC, fact_key LIMIT 160""", (task_id,)
            ).fetchall()
            project = connection.execute(
                """SELECT ps.display_name FROM tasks t JOIN project_sources ps ON ps.id = t.source_id
                WHERE t.id = ?""", (task_id,)
            ).fetchone()
        plan_path = self._data_root / "tasks" / task_id / plan_row["artifact_relative_path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        fact_payload = [{"key": row["fact_key"], "value": json.loads(row["value_json"]),
                         "confidence": row["confidence"]} for row in facts]
        evidence = json.dumps(fact_payload, ensure_ascii=False)[:30000]
        outline = json.dumps({"sections": plan.get("sections", []),
                              "missing_information": plan.get("missing_information", [])},
                             ensure_ascii=False)[:18000]
        prompt = f"""你是中国软件著作权技术说明书撰写助手。请为项目“{project['display_name']}”生成一份可审阅的中文 Markdown 技术说明书草稿。

要求：
1. 严格按章节规划组织，使用一级标题作为文档标题、二级标题作为章节、三级标题作为小节。
2. 以项目事实为主要依据，规则缺失项不是拒绝生成的理由。
3. 不得编造版本号、性能指标、客户名称、部署规模或源码中没有的业务事实；缺少时明确写“待确认”，并给出需要用户补充的内容。
4. 将合理推断明确标记为“根据项目结构推断”，不要伪装成已验证事实。
5. 内容应具体描述模块职责、输入、处理、输出、异常、数据与接口，避免空泛套话。
6. 暂不输出 Draw.io XML，只在需要插图处写占位符，例如：{{{{diagram:system_architecture}}}}。

章节规划：
{outline}

已提取项目事实：
{evidence}
"""
        return prompt, plan_row["version"]

    @staticmethod
    def _default_mode(protocol: str) -> str:
        return {"anthropic": "messages", "ollama": "ollama_chat"}.get(protocol, "chat_completions")

    def _call_model(self, config: dict, mode: str, api_key: str, prompt: str) -> str:
        base = config["base_url"].rstrip("/")
        model = config["model_name"]
        headers = {"Content-Type": "application/json"}
        if mode == "messages":
            url = base + "/messages"
            payload = {"model": model, "max_tokens": 8192,
                       "messages": [{"role": "user", "content": prompt}]}
            if config["protocol_id"] == "anthropic":
                headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            else:
                headers["Authorization"] = "Bearer " + api_key
        elif mode == "responses":
            url = base + "/responses"; headers["Authorization"] = "Bearer " + api_key
            payload = {"model": model, "input": prompt, "max_output_tokens": 8192}
        elif mode == "ollama_chat":
            url = base + "/api/chat"
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False, "options": {"temperature": 0.3}}
        else:
            url = base + "/chat/completions"; headers["Authorization"] = "Bearer " + api_key
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 8192, "temperature": 0.3}
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ManualGenerationError(f"模型调用失败（HTTP {error.code}）：{detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ManualGenerationError(f"模型调用失败：{error}") from error
        if mode == "chat_completions":
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if mode == "ollama_chat":
            return result.get("message", {}).get("content", "")
        if mode == "responses":
            if result.get("output_text"):
                return result["output_text"]
            return "".join(part.get("text", "") for item in result.get("output", [])
                           for part in item.get("content", []) if part.get("type") in {"output_text", "text"})
        return "".join(item.get("text", "") for item in result.get("content", [])
                       if item.get("type") == "text")

    def _persist(self, task_id: str, model_config_id: str, model_name: str,
                 endpoint_mode: str, plan_version: int, content: str, elapsed_ms: int) -> dict:
        task_root = self._data_root / "tasks" / task_id
        with self._database.connect() as connection:
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 value FROM manual_draft_runs WHERE task_id = ?",
                (task_id,),
            ).fetchone()["value"]
            relative = f"artifacts/manual/manual-draft.v{version}.md"
            path = task_root / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            summary = {"model_name": model_name, "endpoint_mode": endpoint_mode,
                       "plan_version": plan_version, "character_count": len(content)}
            created_at = utc_now()
            connection.execute(
                """INSERT INTO manual_draft_runs(id, task_id, model_config_id, version, status,
                summary_json, artifact_relative_path, elapsed_ms, created_at)
                VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?)""",
                (str(uuid4()), task_id, model_config_id, version,
                 json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                 relative, elapsed_ms, created_at),
            )
        return {"version": version, "summary": summary, "content": content,
                "elapsed_ms": elapsed_ms, "created_at": created_at}
