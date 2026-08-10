import json
import hashlib
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .domain import PersistedScan, SourceKind, TaskStatus
from .fact_extraction import DeterministicFactExtractor
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
        fact_extractor: DeterministicFactExtractor = None,
    ) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._scanner = scanner or ProjectScanner()
        self._ingestor = ingestor or InputIngestor()
        self._fact_extractor = fact_extractor or DeterministicFactExtractor()
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
        fact_stage_id = new_id()
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
            unit_of_work.stages.start(
                fact_stage_id, task_id, "03_extract_facts", 3, 1, finished_at
            )
            unit_of_work.events.add(
                task_id,
                "stage.running",
                "info",
                "Deterministic fact extraction started",
                {"stage_key": "03_extract_facts"},
                finished_at,
                stage_run_id=fact_stage_id,
            )

        try:
            extraction = self._fact_extractor.extract(result)
        except Exception as error:
            self._record_failure(
                task_id,
                fact_stage_id,
                error,
                category="fact_error",
                stage_key="03_extract_facts",
            )
            raise

        extracted_at = utc_now()
        with UnitOfWork(self._database) as unit_of_work:
            evidence_ids = {}
            for candidate in extraction.evidence:
                evidence_id = new_id()
                evidence_ids[candidate.ref] = evidence_id
                unit_of_work.evidence.add(
                    evidence_id, snapshot_id, candidate, extracted_at
                )
            for candidate in extraction.facts:
                linked = [
                    evidence_ids[ref]
                    for ref in candidate.evidence_refs
                    if ref in evidence_ids
                ]
                unit_of_work.facts.add(
                    new_id(), task_id, candidate, linked, extracted_at
                )
            for candidate in extraction.confirmations:
                linked = [
                    evidence_ids[ref]
                    for ref in candidate.evidence_refs
                    if ref in evidence_ids
                ]
                unit_of_work.confirmations.add(
                    new_id(), task_id, candidate, linked, extracted_at
                )
            fact_fingerprint = self._fact_fingerprint(extraction)
            unit_of_work.stages.succeed(
                fact_stage_id,
                fact_fingerprint,
                {
                    "fact_count": len(extraction.facts),
                    "evidence_count": len(extraction.evidence),
                    "confirmation_count": len(extraction.confirmations),
                },
                extracted_at,
            )
            unit_of_work.events.add(
                task_id,
                "stage.succeeded",
                "info",
                "Deterministic fact extraction completed",
                {
                    "fact_count": len(extraction.facts),
                    "evidence_count": len(extraction.evidence),
                    "confirmation_count": len(extraction.confirmations),
                },
                extracted_at,
                stage_run_id=fact_stage_id,
            )
            running_task = unit_of_work.tasks.get(task_id)
            if extraction.confirmations:
                self._state_machine.transition(
                    unit_of_work,
                    running_task,
                    TaskStatus.WAITING_FOR_USER,
                    extracted_at,
                    current_stage_key="04_confirm_metadata",
                )
            else:
                self._state_machine.transition(
                    unit_of_work,
                    running_task,
                    TaskStatus.COMPLETED,
                    extracted_at,
                    current_stage_key="03_extract_facts",
                )

        return PersistedScan(
            task_id=task_id,
            source_id=source_id,
            snapshot_id=snapshot_id,
            manifest_path=manifest_path,
            scan_report_path=scan_report_path,
            result=result,
        )

    def _record_failure(
        self,
        task_id: str,
        stage_id: str,
        error: Exception,
        category: str = "scan_error",
        stage_key: str = "02_scan",
    ) -> None:
        now = utc_now()
        safe_message = "Task stage failed: {0}".format(type(error).__name__)
        with UnitOfWork(self._database) as unit_of_work:
            unit_of_work.stages.fail(stage_id, category, safe_message, now)
            task = unit_of_work.tasks.get(task_id)
            self._state_machine.transition(
                unit_of_work,
                task,
                TaskStatus.FAILED,
                now,
                current_stage_key=stage_key,
                failure_category=category,
                safe_error_message=safe_message,
            )

    def _scan_without_persistence(self, project_root: Path) -> PersistedScan:
        self._ingestor.ingest(project_root, self._data_root / "validation")
        raise AssertionError("Unreachable after input validation")

    @staticmethod
    def _fact_fingerprint(extraction: object) -> str:
        payload = {
            "facts": [
                {"key": fact.key, "value": fact.value, "confidence": fact.confidence}
                for fact in extraction.facts
            ],
            "confirmations": [item.field_key for item in extraction.confirmations],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

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
