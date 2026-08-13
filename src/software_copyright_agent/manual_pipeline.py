import json
from datetime import datetime, timezone
from uuid import uuid4

from .manual_execution import ManualExecutionNodeService
from .service import utc_now
from .storage import Database


PIPELINE_STEPS = (
    "research", "draft", "diagrams", "screenshots", "assemble_docx", "render_qa",
)


class ManualPipelineError(ValueError):
    pass


class ManualPipelineService:
    """Persists the resumable shell of the formal technical-document pipeline."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, task_id: str, model_config_id: str) -> dict:
        self._database.initialize()
        self.recover_stale_jobs()
        now, job_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT snapshot_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or not task["snapshot_id"]:
                raise ManualPipelineError("项目尚未完成扫描，无法创建说明书任务")
            active = connection.execute(
                """SELECT id, version, current_step FROM manual_generation_jobs
                WHERE task_id = ? AND status IN ('queued', 'running')
                ORDER BY version DESC LIMIT 1""", (task_id,),
            ).fetchone()
            if active is not None:
                raise ManualPipelineError(
                    "说明书生成任务 v{0} 正在执行 {1}，请等待完成后再生成新版本".format(
                        active["version"], active["current_step"]
                    )
                )
            model = connection.execute(
                "SELECT id FROM model_configs WHERE id = ? AND enabled = 1", (model_config_id,)
            ).fetchone()
            if model is None:
                raise ManualPipelineError("所选模型不存在或已停用")
            version = connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 value FROM manual_generation_jobs
                WHERE task_id = ?""", (task_id,),
            ).fetchone()["value"]
            progress = {"completed": 0, "total": len(PIPELINE_STEPS), "percent": 0}
            connection.execute(
                """INSERT INTO manual_generation_jobs(id, task_id, model_config_id, version,
                status, current_step, progress_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', 'research', ?, ?, ?)""",
                (job_id, task_id, model_config_id, version,
                 json.dumps(progress, separators=(",", ":")), now, now),
            )
            for step in PIPELINE_STEPS:
                connection.execute(
                    """INSERT INTO manual_generation_steps(id, job_id, step_key, status,
                    attempt, summary_json) VALUES (?, ?, ?, 'pending', 1, '{}')""",
                    (str(uuid4()), job_id, step),
                )
        return self.get(job_id)

    def recover_interrupted_jobs(self) -> int:
        """Mark work left running by a previous sidecar process as retryable failure.

        This is called exactly once during sidecar startup, before the process can own a
        new job.  It must not be called as part of ordinary request handling because a
        healthy long model call is expected to remain in ``running`` for several minutes.
        """
        self._database.initialize()
        now = utc_now()
        message = "上次应用退出时生成被中断；已保留已有阶段结果，请重新生成新版本"
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM manual_generation_jobs
                WHERE status IN ('queued','running')"""
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            for job_id in job_ids:
                connection.execute(
                    """UPDATE manual_execution_nodes SET status='failed',finished_at=?,
                    heartbeat_at=?,updated_at=?,error_category='interrupted',
                    safe_error_message=? WHERE job_id=? AND status='running'""",
                    (now, now, now, message, job_id),
                )
                connection.execute(
                    """UPDATE manual_generation_steps SET status='failed', finished_at=?,
                    safe_error_message=? WHERE job_id=? AND status='running'""",
                    (now, message, job_id),
                )
                connection.execute(
                    """UPDATE manual_generation_jobs SET status='failed', updated_at=?,
                    finished_at=?, safe_error_message=? WHERE id=?""",
                    (now, now, message, job_id),
                )
            # UI-only updates may run against an already completed job. They are
            # still process-local work and must not remain permanently "running"
            # after a sidecar restart.
            connection.execute(
                """UPDATE manual_execution_nodes SET status='failed',finished_at=?,
                heartbeat_at=?,updated_at=?,error_category='interrupted',
                safe_error_message=? WHERE status='running'""",
                (now, now, now, message),
            )
        return len(job_ids)

    def recover_stale_jobs(self, maximum_idle_seconds: int = 5_400) -> int:
        """Recover jobs whose persisted heartbeat is too old to still be credible."""
        self._database.initialize()
        now = datetime.now(timezone.utc)
        message = "生成任务长时间没有进度，已按中断处理；已有阶段结果仍保留"
        stale = []
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id,updated_at FROM manual_generation_jobs
                WHERE status IN ('queued','running')"""
            ).fetchall()
            for row in rows:
                try:
                    updated = datetime.fromisoformat(
                        str(row["updated_at"]).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
                if (now - updated).total_seconds() >= maximum_idle_seconds:
                    stale.append(row["id"])
            stamp = utc_now()
            for job_id in stale:
                connection.execute(
                    """UPDATE manual_execution_nodes SET status='failed',finished_at=?,
                    heartbeat_at=?,updated_at=?,error_category='stale_heartbeat',
                    safe_error_message=? WHERE job_id=? AND status='running'""",
                    (stamp, stamp, stamp, message, job_id),
                )
                connection.execute(
                    """UPDATE manual_generation_steps SET status='failed',finished_at=?,
                    safe_error_message=? WHERE job_id=? AND status='running'""",
                    (stamp, message, job_id),
                )
                connection.execute(
                    """UPDATE manual_generation_jobs SET status='failed',updated_at=?,
                    finished_at=?,safe_error_message=? WHERE id=?""",
                    (stamp, stamp, message, job_id),
                )
        return len(stale)

    def get(self, job_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            job = connection.execute(
                "SELECT * FROM manual_generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise ManualPipelineError("说明书生成任务不存在")
            steps = connection.execute(
                """SELECT step_key, status, attempt, summary_json, started_at, finished_at,
                safe_error_message FROM manual_generation_steps WHERE job_id = ?
                ORDER BY CASE step_key
                    WHEN 'research' THEN 1 WHEN 'draft' THEN 2 WHEN 'diagrams' THEN 3
                    WHEN 'screenshots' THEN 4 WHEN 'assemble_docx' THEN 5 ELSE 6 END,
                    attempt DESC""", (job_id,),
            ).fetchall()
        latest = {}
        for row in steps:
            latest.setdefault(row["step_key"], {
                "key": row["step_key"], "status": row["status"], "attempt": row["attempt"],
                "summary": json.loads(row["summary_json"]), "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "safe_error_message": row["safe_error_message"],
            })
        nodes = ManualExecutionNodeService(self._database).list(job_id)
        persisted_progress = json.loads(job["progress_json"])
        progress = self._node_progress(nodes, persisted_progress)
        return {
            "id": job["id"], "task_id": job["task_id"],
            "model_config_id": job["model_config_id"], "version": job["version"],
            "status": job["status"], "current_step": job["current_step"],
            "progress": progress, "created_at": job["created_at"],
            "started_at": job["started_at"], "finished_at": job["finished_at"],
            "updated_at": job["updated_at"], "safe_error_message": job["safe_error_message"],
            "steps": [latest[key] for key in PIPELINE_STEPS if key in latest],
            "nodes": nodes,
        }

    @staticmethod
    def _node_progress(nodes: list, fallback: dict) -> dict:
        """Aggregate the task from durable execution nodes instead of six stage gates."""
        if not nodes:
            return fallback
        terminal = {
            "completed", "completed_with_warnings", "failed", "skipped",
            "waiting_for_authorization", "waiting_for_review", "waiting_for_screenshots",
            "adopted", "outdated",
        }
        status_counts = {}
        for node in nodes:
            status_counts[node["status"]] = status_counts.get(node["status"], 0) + 1
        resolved = sum(node["status"] in terminal for node in nodes)
        total = len(nodes)
        result = dict(fallback)
        result.update({
            "completed": resolved,
            "total": total,
            "percent": round(resolved / total * 100) if total else 0,
            "node_status_counts": status_counts,
            "running_nodes": status_counts.get("running", 0),
            "queued_nodes": status_counts.get("queued", 0),
        })
        return result

    def list_for_task(self, task_id: str) -> list:
        self._database.initialize()
        self.recover_stale_jobs()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM manual_generation_jobs WHERE task_id = ?
                ORDER BY version DESC""", (task_id,),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def wait_for_assets(self, job_id: str, reason: str) -> dict:
        """Finish the automatic run at a safe checkpoint until evidence is resolved."""
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_generation_jobs SET status='completed_with_warnings',
                current_step='screenshots',progress_json=?,finished_at=?,updated_at=?,
                safe_error_message=NULL WHERE id=?""",
                (json.dumps({"completed": 4, "total": 6, "percent": 67,
                             "waiting_reason": reason}, ensure_ascii=False,
                            separators=(",", ":")), now, now, job_id),
            )
        return self.get(job_id)
