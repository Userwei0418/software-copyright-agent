import json
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .domain import PersistedScan, SourceKind, TaskStatus
from .ingestion import InputIngestor
from .scanner import ProjectScanner
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


WORKFLOW_VERSION = "mvp-1"
QUALITY_POLICY_VERSION = "mvp-1"
SCANNER_VERSION = "0.1.0"
RULES_VERSION = "0.1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


class ScanProjectService:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        scanner: ProjectScanner = None,
        ingestor: InputIngestor = None,
    ) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._scanner = scanner or ProjectScanner()
        self._ingestor = ingestor or InputIngestor()
        self._state_machine = TaskStateMachine()

    def execute(self, project_root: Path) -> PersistedScan:
        self._database.initialize()
        original = project_root.expanduser().resolve()
        if not original.exists():
            return self._scan_without_persistence(project_root)
        if not original.is_dir() and not (
            original.is_file() and original.suffix.lower() == ".zip"
        ):
            return self._scan_without_persistence(project_root)

        source_id = new_id()
        task_id = new_id()
        stage_id = new_id()
        now = utc_now()
        task_root = self._data_root / "tasks" / task_id

        with UnitOfWork(self._database) as unit_of_work:
            source_kind = SourceKind.ZIP if original.is_file() else SourceKind.DIRECTORY
            unit_of_work.sources.add(
                source_id, source_kind, str(original), original.name, now
            )
            unit_of_work.tasks.add(
                task_id,
                source_id,
                WORKFLOW_VERSION,
                QUALITY_POLICY_VERSION,
                now,
            )
            task = unit_of_work.tasks.get(task_id)
            task = self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.RUNNING,
                now,
                current_stage_key="02_scan",
            )
            unit_of_work.stages.start(stage_id, task_id, "02_scan", 2, 1, now)

        try:
            ingested = self._ingestor.ingest(original, task_root)
            result = self._scanner.scan(ingested.scan_root)
        except Exception as error:
            self._record_failure(task_id, stage_id, error)
            raise

        snapshot_id = new_id()

        manifest_relative = Path("input") / "manifest.jsonl"
        manifest_path = task_root / manifest_relative
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest_atomic(manifest_path, result)

        scan_report_relative = Path("qa") / "scan-report.json"
        scan_report_path = task_root / scan_report_relative
        scan_report_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(scan_report_path, self._scan_report(result))

        categories = Counter(item.category.value for item in result.files)
        languages = Counter(item.language for item in result.files if item.language)
        summary = {
            "file_count": len(result.files),
            "ignored_count": result.ignored_count,
            "ignored_by_reason": result.ignored_by_reason,
            "skipped_symlink_count": result.skipped_symlink_count,
            "total_bytes": result.total_bytes,
            "binary_file_count": sum(1 for item in result.files if item.is_binary),
            "secret_finding_count": len(result.secret_findings),
            "categories": dict(sorted(categories.items())),
            "languages": dict(sorted(languages.items())),
        }

        finished_at = utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.snapshots.add(
                snapshot_id,
                source_id,
                result.root_fingerprint,
                SCANNER_VERSION,
                RULES_VERSION,
                summary,
                manifest_relative.as_posix(),
                finished_at,
            )
            unit_of_work.tasks.attach_snapshot(task_id, snapshot_id)
            unit_of_work.stages.succeed(
                stage_id,
                result.root_fingerprint,
                {"snapshot_id": snapshot_id},
                finished_at,
            )
            unit_of_work.events.add(
                task_id,
                "stage.succeeded",
                "info",
                "Project scan completed",
                summary,
                finished_at,
                stage_run_id=stage_id,
            )
            running_task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                running_task,
                TaskStatus.COMPLETED,
                finished_at,
                current_stage_key="02_scan",
            )

        return PersistedScan(
            task_id=task_id,
            source_id=source_id,
            snapshot_id=snapshot_id,
            manifest_path=manifest_path,
            scan_report_path=scan_report_path,
            result=result,
        )

    def _record_failure(self, task_id: str, stage_id: str, error: Exception) -> None:
        now = utc_now()
        safe_message = "Project scan failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, "scan_error", safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.FAILED,
                now,
                current_stage_key="02_scan",
                failure_category="scan_error",
                safe_error_message=safe_message,
            )

    def _scan_without_persistence(self, project_root: Path) -> PersistedScan:
        self._ingestor.ingest(project_root, self._data_root / "validation")
        raise AssertionError("Unreachable after input validation")

    @staticmethod
    def _write_manifest_atomic(path: Path, result: object) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="manifest-", suffix=".tmp", dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                for item in result.files:
                    stream.write(
                        json.dumps(
                            {
                                "path": item.relative_path,
                                "size": item.size,
                                "modified_ns": item.modified_ns,
                                "sha256": item.sha256,
                                "category": item.category.value,
                                "language": item.language,
                                "is_binary": item.is_binary,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _scan_report(result: object) -> object:
        return {
            "schema_version": 1,
            "root_fingerprint": result.root_fingerprint,
            "file_count": len(result.files),
            "total_bytes": result.total_bytes,
            "ignored_count": result.ignored_count,
            "ignored_by_reason": result.ignored_by_reason,
            "binary_files": [
                item.relative_path for item in result.files if item.is_binary
            ],
            "secret_findings": [
                {
                    "path": finding.relative_path,
                    "line_number": finding.line_number,
                    "rule_id": finding.rule_id,
                }
                for finding in result.secret_findings
            ],
        }

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="report-", suffix=".tmp", dir=str(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
