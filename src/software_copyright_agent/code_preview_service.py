import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .code_preview import (
    FORMATTER_VERSION,
    CodeInputFile,
    CodePreview,
    CodePreviewBuilder,
    CodePreviewConfig,
    CodePreviewError,
)
from .domain import TaskStatus
from .service import new_id, utc_now
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


@dataclass(frozen=True)
class PersistedCodePreview:
    task_id: str
    run_id: str
    version: int
    artifact_path: Path
    preview: CodePreview


class CodePreviewService:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        config: CodePreviewConfig = None,
    ) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._builder = CodePreviewBuilder(config)
        self._state_machine = TaskStateMachine()

    def execute(self, task_id: str) -> PersistedCodePreview:
        self._database.initialize()
        with self._database.connect() as connection:
            task_row = connection.execute(
                """SELECT t.status, ps.manifest_relative_path, ps.scan_root_mode,
                ps.scan_root_path FROM tasks t
                JOIN project_snapshots ps ON ps.id = t.snapshot_id
                WHERE t.id = ?""",
                (task_id,),
            ).fetchone()
            plan_row = connection.execute(
                """SELECT id, version FROM source_plan_runs
                WHERE task_id = ? ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            if task_row is None or plan_row is None:
                raise CodePreviewError("Completed source plan not found for task")
            if task_row["status"] not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.COMPLETED_WITH_WARNINGS.value,
            }:
                raise CodePreviewError(
                    "Task must be completed before code preview: {0}".format(
                        task_row["status"]
                    )
                )
            candidate_rows = connection.execute(
                """SELECT relative_path, grade, score, language
                FROM source_candidates WHERE plan_run_id = ? AND selected = 1
                ORDER BY CASE grade WHEN 'A' THEN 0 ELSE 1 END,
                         score DESC, relative_path""",
                (plan_row["id"],),
            ).fetchall()

            task_root = self._data_root / "tasks" / task_id
            manifest_path = task_root / PurePosixPath(task_row["manifest_relative_path"])
            if task_row["scan_root_mode"] == "task":
                scan_root = task_root / PurePosixPath(task_row["scan_root_path"])
            else:
                scan_root = Path(task_row["scan_root_path"])

        manifest_hashes = {}
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            manifest_hashes[item["path"]] = item["sha256"]
        files = [
            CodeInputFile(
                relative_path=row["relative_path"],
                grade=row["grade"],
                score=row["score"],
                language=row["language"],
                expected_sha256=manifest_hashes[row["relative_path"]],
            )
            for row in candidate_rows
        ]

        stage_id = new_id()
        now = utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.RUNNING,
                now,
                current_stage_key="06_prepare_source_doc",
            )
            attempt = unit_of_work.stages.next_attempt(task_id, "06_prepare_source_doc")
            unit_of_work.stages.start(
                stage_id, task_id, "06_prepare_source_doc", 6, attempt, now
            )
            version = unit_of_work.code_previews.next_version(task_id)

        try:
            preview = self._builder.build(scan_root, files)
            artifact_relative = Path("intermediate") / "code-pagination" / (
                "preview.v{0}.json".format(version)
            )
            artifact_path = task_root / artifact_relative
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._payload(task_id, version, plan_row["id"], preview)
            self._write_json_atomic(artifact_path, payload)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        finished_at = utc_now()
        run_id = new_id()
        config = asdict(self._builder.config)
        summary = self._summary(preview)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.code_previews.add_run(
                run_id,
                task_id,
                plan_row["id"],
                stage_id,
                version,
                FORMATTER_VERSION,
                config,
                summary,
                artifact_relative.as_posix(),
                finished_at,
            )
            fingerprint = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            unit_of_work.stages.succeed(
                stage_id, fingerprint, {"run_id": run_id, "version": version}, finished_at
            )
            unit_of_work.events.add(
                task_id, "stage.succeeded", "info", "Code pagination preview completed",
                summary, finished_at, stage_run_id=stage_id,
            )
            running_task = unit_of_work.tasks.get(task_id)
            target = (
                TaskStatus.COMPLETED
                if preview.sufficient
                else TaskStatus.COMPLETED_WITH_WARNINGS
            )
            self._state_machine.transition(
                unit_of_work,
                running_task,
                target,
                finished_at,
                current_stage_key="06_prepare_source_doc",
            )

        return PersistedCodePreview(task_id, run_id, version, artifact_path, preview)

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe_message = "Code preview failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "code_preview_error", safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work, task, TaskStatus.FAILED, now,
                current_stage_key="06_prepare_source_doc",
                failure_category="code_preview_error",
                safe_error_message=safe_message,
            )

    def _payload(
        self, task_id: str, version: int, source_plan_run_id: str, preview: CodePreview
    ) -> dict:
        return {
            "schema_version": 1,
            "task_id": task_id,
            "version": version,
            "source_plan_run_id": source_plan_run_id,
            "formatter_version": FORMATTER_VERSION,
            "config": asdict(self._builder.config),
            "summary": self._summary(preview),
            "pages": preview.pages,
        }

    @staticmethod
    def _summary(preview: CodePreview) -> dict:
        return {
            "available_visual_lines": preview.available_visual_lines,
            "used_visual_lines": preview.used_visual_lines,
            "required_visual_lines": preview.required_visual_lines,
            "generated_pages": preview.generated_pages,
            "target_pages": preview.target_pages,
            "sufficient": preview.sufficient,
            "selected_files": preview.selected_files,
            "included_files": preview.included_files,
            "available_buckets": list(preview.available_buckets),
            "included_buckets": list(preview.included_buckets),
            "included_languages": list(preview.included_languages),
            "truncated": preview.truncated,
        }

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="code-preview-", suffix=".tmp", dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
