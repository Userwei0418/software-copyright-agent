import json
from uuid import uuid4

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
        now, job_id = utc_now(), str(uuid4())
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT snapshot_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or not task["snapshot_id"]:
                raise ManualPipelineError("项目尚未完成扫描，无法创建说明书任务")
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
        return {
            "id": job["id"], "task_id": job["task_id"],
            "model_config_id": job["model_config_id"], "version": job["version"],
            "status": job["status"], "current_step": job["current_step"],
            "progress": json.loads(job["progress_json"]), "created_at": job["created_at"],
            "started_at": job["started_at"], "finished_at": job["finished_at"],
            "updated_at": job["updated_at"], "safe_error_message": job["safe_error_message"],
            "steps": [latest[key] for key in PIPELINE_STEPS if key in latest],
        }

    def list_for_task(self, task_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM manual_generation_jobs WHERE task_id = ?
                ORDER BY version DESC""", (task_id,),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]
