import json
import re
from pathlib import Path

from .storage import Database


_SECRET_KEY = re.compile(r"api[_-]?key|authorization|credential|password|secret|token", re.I)


class RunDiagnosticsService:
    """Builds redacted, exportable diagnostics from durable workflow checkpoints."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def recent(self, limit: int = 5) -> dict:
        limit = max(1, min(20, int(limit)))
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM quick_start_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        items = [self._run(dict(row)) for row in rows]
        return {"schema_version": 1, "generated_from": "durable_checkpoints",
                "run_count": len(items), "runs": items}

    def _run(self, row: dict) -> dict:
        config = self._loads(row.pop("config_json", "{}"), {})
        stages = self._loads(row.pop("stages_json", "[]"), [])
        outputs = self._loads(row.pop("outputs_json", "{}"), {})
        task_id, job_id = row.get("task_id"), row.get("manual_job_id")
        item = {**row, "config": self._redact_config(config),
                "stages": [self._stage(stage) for stage in stages],
                "outputs": self._compact(outputs), "task_events": [], "manual_job": None,
                "manual_nodes": [], "document_qa": [], "source_qa": []}
        with self._database.connect() as connection:
            if task_id:
                events = connection.execute(
                    """SELECT event_type,level,message,payload_json,created_at
                    FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 200""", (task_id,)
                ).fetchall()
                item["task_events"] = []
                for event in reversed(events):
                    entry = dict(event)
                    entry["payload"] = self._compact(self._loads(
                        entry.pop("payload_json", "{}"), {}))
                    item["task_events"].append(entry)
                source_qa = connection.execute(
                    """SELECT version,policy_version,passed,summary_json,created_at
                    FROM source_document_qa_runs WHERE task_id=? ORDER BY version DESC LIMIT 3""",
                    (task_id,),
                ).fetchall()
                item["source_qa"] = [{**dict(qa), "passed": bool(qa["passed"]),
                                      "summary": self._loads(qa["summary_json"], {})}
                                     for qa in source_qa]
            if job_id:
                job = connection.execute(
                    "SELECT * FROM manual_generation_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if job:
                    item["manual_job"] = {**dict(job), "progress": self._loads(
                        job["progress_json"], {})}
                    item["manual_job"].pop("progress_json", None)
                nodes = connection.execute(
                    """SELECT node_key,stage_key,node_kind,title,status,attempt,max_attempts,
                    error_category,safe_error_message,started_at,heartbeat_at,finished_at,updated_at
                    FROM manual_execution_nodes WHERE job_id=? ORDER BY created_at""", (job_id,)
                ).fetchall()
                item["manual_nodes"] = [dict(node) for node in nodes]
                qa_rows = connection.execute(
                    """SELECT a.version,a.status,q.qa_version,q.policy_version,q.passed,
                    q.checks_json,q.summary_json,q.created_at FROM manual_document_artifacts a
                    JOIN manual_document_qa_runs q ON q.document_artifact_id=a.id
                    WHERE a.job_id=? ORDER BY a.version DESC,q.qa_version DESC LIMIT 20""", (job_id,)
                ).fetchall()
                item["document_qa"] = [self._qa(dict(qa)) for qa in qa_rows]
        return self._redact(item)

    def _stage(self, stage: dict) -> dict:
        result = {key: value for key, value in stage.items() if key != "output"}
        output = stage.get("output")
        if output:
            result["output"] = self._compact(output)
        return result

    def _qa(self, row: dict) -> dict:
        checks = self._loads(row.pop("checks_json", "[]"), [])
        summary = self._loads(row.pop("summary_json", "{}"), {})
        return {**row, "passed": bool(row.get("passed")), "summary": summary,
                "failed_checks": [item for item in checks if not item.get("passed")]}

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            return {key: ("[REDACTED]" if _SECRET_KEY.search(str(key))
                          else cls._redact(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, str) and Path(value).is_absolute():
            return "[LOCAL_PATH]/" + Path(value).name
        return value

    @staticmethod
    def _redact_config(config: dict) -> dict:
        return {key: (Path(value).name if key in {"project_path", "screenshot_folder"}
                      and isinstance(value, str) else value)
                for key, value in config.items()}

    @classmethod
    def _compact(cls, value, depth: int = 0):
        """Keep diagnostic context useful without exporting project contents."""
        if depth >= 4:
            return "[OMITTED]"
        if isinstance(value, dict):
            omitted = {"content", "xml", "files", "candidates", "manifest",
                       "manifest_path", "source_text", "blocks", "pages"}
            return {key: ("[OMITTED]" if key in omitted else cls._compact(item, depth + 1))
                    for key, item in value.items()}
        if isinstance(value, list):
            items = [cls._compact(item, depth + 1) for item in value[:20]]
            if len(value) > 20:
                items.append({"omitted_items": len(value) - 20})
            return items
        if isinstance(value, str) and len(value) > 2000:
            return value[:2000] + "…[TRUNCATED]"
        return value

    @staticmethod
    def _loads(raw: str, fallback):
        try:
            return json.loads(raw or "")
        except (TypeError, json.JSONDecodeError):
            return fallback
