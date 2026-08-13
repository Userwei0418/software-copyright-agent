import json
from contextlib import contextmanager
from datetime import datetime
from threading import Lock, Semaphore
from uuid import uuid4

from .service import utc_now
from .storage import Database


_JOB_SLOT_LOCK = Lock()
_JOB_SLOTS = {}


@contextmanager
def manual_job_slot(job_id: str, concurrency: int):
    """Share the configured model-call ceiling across workflow and item retries."""
    limit = max(1, int(concurrency))
    with _JOB_SLOT_LOCK:
        entry = _JOB_SLOTS.get(job_id)
        if entry is None:
            entry = (limit, Semaphore(limit))
            _JOB_SLOTS[job_id] = entry
        semaphore = entry[1]
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


class ManualExecutionNodeService:
    """Persists independently observable and retryable units of manual generation."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def prepare(self, job_id: str, node_key: str, stage_key: str, node_kind: str,
                title: str, dependencies=None, model_config_id=None,
                max_attempts: int = 1, input_value=None) -> dict:
        self._database.initialize()
        now = utc_now()
        dependencies = list(dependencies or [])
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_execution_nodes(
                id,job_id,node_key,stage_key,node_kind,title,status,
                dependency_keys_json,attempt,max_attempts,model_config_id,input_json,
                output_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'queued',?,0,?,?,?,'{}',?,?)
                ON CONFLICT(job_id,node_key) DO UPDATE SET
                stage_key=excluded.stage_key,node_kind=excluded.node_kind,title=excluded.title,
                dependency_keys_json=excluded.dependency_keys_json,
                max_attempts=excluded.max_attempts,model_config_id=excluded.model_config_id,
                input_json=excluded.input_json,updated_at=excluded.updated_at""",
                (str(uuid4()), job_id, node_key, stage_key, node_kind, title,
                 json.dumps(dependencies, ensure_ascii=False, separators=(",", ":")),
                 max_attempts, model_config_id,
                 json.dumps(input_value or {}, ensure_ascii=False, separators=(",", ":")),
                 now, now),
            )
        return self.get(job_id, node_key)

    def running(self, job_id: str, node_key: str, attempt: int = 1) -> dict:
        now = utc_now()
        with self._database.connect() as connection:
            changed = connection.execute(
                """UPDATE manual_execution_nodes SET status='running',attempt=?,
                started_at=COALESCE(started_at,?),heartbeat_at=?,finished_at=NULL,
                error_category=NULL,safe_error_message=NULL,updated_at=?
                WHERE job_id=? AND node_key=?""",
                (attempt, now, now, now, job_id, node_key),
            ).rowcount
        if not changed:
            raise ValueError("Execution node not found")
        return self.get(job_id, node_key)

    def queued(self, job_id: str, node_key: str) -> dict:
        """Expose a retry waiting for the job-wide concurrency slot."""
        now = utc_now()
        with self._database.connect() as connection:
            changed = connection.execute(
                """UPDATE manual_execution_nodes SET status='queued',finished_at=NULL,
                error_category=NULL,safe_error_message=NULL,heartbeat_at=NULL,updated_at=?
                WHERE job_id=? AND node_key=?""",
                (now, job_id, node_key),
            ).rowcount
        if not changed:
            raise ValueError("Execution node not found")
        return self.get(job_id, node_key)

    def heartbeat(self, job_id: str, node_key: str, attempt: int = None,
                  retry_reason: str = None) -> None:
        now = utc_now()
        with self._database.connect() as connection:
            output_json = None
            if retry_reason:
                row = connection.execute(
                    """SELECT output_json FROM manual_execution_nodes
                    WHERE job_id=? AND node_key=? AND status='running'""",
                    (job_id, node_key),
                ).fetchone()
                if row is not None:
                    output = json.loads(row["output_json"] or "{}")
                    output.update({
                        "retry_reason": retry_reason[:300],
                        "next_action": "当前重试仍在等待返回；失败后只需重试此项",
                    })
                    output_json = json.dumps(
                        output, ensure_ascii=False, separators=(",", ":")
                    )
            if attempt is None:
                connection.execute(
                    """UPDATE manual_execution_nodes SET heartbeat_at=?,updated_at=?,
                    output_json=COALESCE(?,output_json)
                    WHERE job_id=? AND node_key=? AND status='running'""",
                    (now, now, output_json, job_id, node_key),
                )
            else:
                connection.execute(
                    """UPDATE manual_execution_nodes SET attempt=?,heartbeat_at=?,updated_at=?,
                    output_json=COALESCE(?,output_json)
                    WHERE job_id=? AND node_key=? AND status='running'""",
                    (attempt, now, now, output_json, job_id, node_key),
                )

    def complete(self, job_id: str, node_key: str, output=None,
                 warnings: bool = False) -> dict:
        now = utc_now()
        status = "completed_with_warnings" if warnings else "completed"
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_execution_nodes SET status=?,output_json=?,heartbeat_at=?,
                finished_at=?,updated_at=?,error_category=NULL,safe_error_message=NULL
                WHERE job_id=? AND node_key=?""",
                (status, json.dumps(output or {}, ensure_ascii=False, separators=(",", ":")),
                 now, now, now, job_id, node_key),
            )
        return self.get(job_id, node_key)

    def waiting_for_authorization(self, job_id: str, node_key: str, output=None) -> dict:
        """Park a non-blocking node until the user explicitly authorizes its action."""
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_execution_nodes SET status='waiting_for_authorization',
                output_json=?,heartbeat_at=?,finished_at=?,updated_at=?,error_category=NULL,
                safe_error_message=NULL WHERE job_id=? AND node_key=?""",
                (json.dumps(output or {}, ensure_ascii=False, separators=(",", ":")),
                 now, now, now, job_id, node_key),
            )
        return self.get(job_id, node_key)

    def waiting_for_screenshots(self, job_id: str, node_key: str, output=None) -> dict:
        return self._park(job_id, node_key, "waiting_for_screenshots", output)

    def waiting_for_review(self, job_id: str, node_key: str, output=None) -> dict:
        return self._park(job_id, node_key, "waiting_for_review", output)

    def adopted(self, job_id: str, node_key: str, output=None) -> dict:
        return self._park(job_id, node_key, "adopted", output)

    def outdated(self, job_id: str, node_key: str, output=None) -> dict:
        return self._park(job_id, node_key, "outdated", output)

    def _park(self, job_id: str, node_key: str, status: str, output=None) -> dict:
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_execution_nodes SET status=?,output_json=?,heartbeat_at=?,
                finished_at=?,updated_at=?,error_category=NULL,safe_error_message=NULL
                WHERE job_id=? AND node_key=?""",
                (status, json.dumps(output or {}, ensure_ascii=False, separators=(",", ":")),
                 now, now, now, job_id, node_key),
            )
        return self.get(job_id, node_key)

    def fail(self, job_id: str, node_key: str, message: str,
             category: str = "unexpected") -> dict:
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_execution_nodes SET status='failed',error_category=?,
                safe_error_message=?,heartbeat_at=?,finished_at=?,updated_at=?
                WHERE job_id=? AND node_key=?""",
                (category[:80], message[:300], now, now, now, job_id, node_key),
            )
        return self.get(job_id, node_key)

    def get(self, job_id: str, node_key: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM manual_execution_nodes WHERE job_id=? AND node_key=?",
                (job_id, node_key),
            ).fetchone()
        if row is None:
            raise ValueError("Execution node not found")
        return self._public(row)

    def list(self, job_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM manual_execution_nodes WHERE job_id=?
                ORDER BY created_at,node_key""", (job_id,),
            ).fetchall()
        return [self._public(row) for row in rows]

    @staticmethod
    def _public(row) -> dict:
        started_at = row["started_at"]
        finished_at = row["finished_at"]
        duration_ms = None
        if started_at:
            try:
                start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                end = datetime.fromisoformat(
                    str(finished_at or row["updated_at"]).replace("Z", "+00:00")
                )
                duration_ms = max(0, round((end - start).total_seconds() * 1000))
            except (TypeError, ValueError):
                duration_ms = None
        output = json.loads(row["output_json"] or "{}")
        next_action = output.get("next_action")
        if not next_action and row["status"] == "failed":
            next_action = {
                "section": "重试此章节，不需要整单重跑",
                "figure": "重试此图表；已保留的语义会优先复用本地渲染",
                "screenshot": "稍后授权采集或人工导入真实截图",
                "screenshot_analysis": "可直接重试此截图，无需重跑其他图片",
                "assemble": "使用当前可用资产重新装配候选稿",
                "qa": "定位未通过项后单独重跑质检",
            }.get(row["node_kind"], "查看错误并重试此节点")
        if not next_action and row["status"] == "waiting_for_authorization":
            next_action = "用户明确授权后再启动项目；也可人工导入真实截图"
        if not next_action and row["status"] == "waiting_for_screenshots":
            next_action = "导入真实截图；其他章节和阶段审阅稿不受影响"
        if not next_action and row["status"] == "waiting_for_review":
            next_action = "审核 AI 逐图解读、分组和顺序后批量确认采用"
        if not next_action and row["status"] == "outdated":
            next_action = "截图或项目概要已变化，请更新用户界面章节"
        return {
            "id": row["id"], "key": row["node_key"], "stage_key": row["stage_key"],
            "kind": row["node_kind"], "title": row["title"], "status": row["status"],
            "dependencies": json.loads(row["dependency_keys_json"] or "[]"),
            "attempt": row["attempt"], "max_attempts": row["max_attempts"],
            "model_config_id": row["model_config_id"],
            "input": json.loads(row["input_json"] or "{}"),
            "output": output,
            "next_action": next_action,
            "duration_ms": duration_ms,
            "error_category": row["error_category"],
            "safe_error_message": row["safe_error_message"],
            "started_at": row["started_at"], "heartbeat_at": row["heartbeat_at"],
            "finished_at": row["finished_at"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
