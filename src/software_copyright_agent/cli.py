import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .confirmation import ConfirmationError, ConfirmationService
from .code_preview import CodePreviewError
from .code_preview_service import CodePreviewService
from .ingestion import IngestionError
from .inspection import InspectionError, InspectionService
from .scanner import ScanError
from .service import ScanProjectService
from .source_plan_service import SourcePlanError, SourcePlanService
from .storage import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="copyright-agent")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".software-copyright-agent"),
        help="Application data directory (default: .software-copyright-agent)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan and persist a local project")
    scan_parser.add_argument("project", type=Path, help="Local project directory or ZIP file")
    scan_parser.add_argument("--json", action="store_true", help="Print JSON output")

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect persisted facts, evidence and confirmations"
    )
    inspect_parser.add_argument(
        "task_id", nargs="?", help="Task ID; omit to inspect the latest task"
    )
    inspect_parser.add_argument("--json", action="store_true", help="Print JSON output")

    confirm_parser = subparsers.add_parser(
        "confirm", help="Answer a pending project metadata confirmation"
    )
    confirm_parser.add_argument("task_id", help="Task ID")
    confirm_parser.add_argument("field_key", help="Field key, for example project.version")
    confirm_parser.add_argument("value", help="Confirmed text value")
    confirm_parser.add_argument("--json", action="store_true", help="Print JSON output")

    source_plan_parser = subparsers.add_parser(
        "source-plan", help="Build an explainable A/B/C source selection plan"
    )
    source_plan_parser.add_argument("task_id", help="Completed task ID")
    source_plan_parser.add_argument("--json", action="store_true", help="Print JSON output")

    code_preview_parser = subparsers.add_parser(
        "code-preview", help="Build deterministic wrapped and paginated source preview"
    )
    code_preview_parser.add_argument("task_id", help="Task ID with a source plan")
    code_preview_parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    database = Database(data_dir / "app.db")
    if args.command == "inspect":
        try:
            inspection = InspectionService(database).inspect(args.task_id)
        except InspectionError as error:
            print(str(error), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(inspection, ensure_ascii=False, sort_keys=True))
        else:
            print("Task: {0}".format(inspection["task"]["id"]))
            print("Status: {0}".format(inspection["task"]["status"]))
            print("Facts:")
            for fact in inspection["facts"]:
                print("  {0}: {1}".format(fact["key"], fact["value"]))
            print("Pending confirmations: {0}".format(
                sum(1 for item in inspection["confirmations"] if item["status"] == "pending")
            ))
        return 0

    if args.command == "confirm":
        try:
            result = ConfirmationService(database).answer(
                args.task_id, args.field_key, args.value
            )
        except ConfirmationError as error:
            print(str(error), file=sys.stderr)
            return 2
        payload = {
            "task_id": result.task_id,
            "field_key": result.field_key,
            "confirmation_id": result.confirmation_id,
            "fact_id": result.fact_id,
            "remaining_required": result.remaining_required,
            "task_status": result.task_status.value,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print("Confirmed: {0}".format(result.field_key))
            print("Remaining required: {0}".format(result.remaining_required))
            print("Task status: {0}".format(result.task_status.value))
        return 0

    if args.command == "source-plan":
        try:
            persisted_plan = SourcePlanService(database, data_dir).execute(args.task_id)
        except SourcePlanError as error:
            print(str(error), file=sys.stderr)
            return 2
        payload = {
            "task_id": persisted_plan.task_id,
            "run_id": persisted_plan.run_id,
            "version": persisted_plan.version,
            "artifact_path": str(persisted_plan.artifact_path),
            "total_source_files": persisted_plan.plan.total_source_files,
            "selected_files": persisted_plan.plan.selected_files,
            "selected_code_lines": persisted_plan.plan.selected_code_lines,
            "excluded_files": persisted_plan.plan.excluded_files,
            "grades": {
                grade: sum(
                    1 for item in persisted_plan.plan.candidates if item.grade == grade
                )
                for grade in ("A", "B", "C")
            },
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print("Source plan: v{0}".format(persisted_plan.version))
            print("Selected files: {0}".format(persisted_plan.plan.selected_files))
            print("Selected code lines: {0}".format(persisted_plan.plan.selected_code_lines))
            print("Artifact: {0}".format(persisted_plan.artifact_path))
        return 0

    if args.command == "code-preview":
        try:
            persisted_preview = CodePreviewService(database, data_dir).execute(args.task_id)
        except CodePreviewError as error:
            print(str(error), file=sys.stderr)
            return 2
        preview = persisted_preview.preview
        payload = {
            "task_id": persisted_preview.task_id,
            "run_id": persisted_preview.run_id,
            "version": persisted_preview.version,
            "artifact_path": str(persisted_preview.artifact_path),
            "available_visual_lines": preview.available_visual_lines,
            "used_visual_lines": preview.used_visual_lines,
            "required_visual_lines": preview.required_visual_lines,
            "generated_pages": preview.generated_pages,
            "target_pages": preview.target_pages,
            "sufficient": preview.sufficient,
            "selected_files": preview.selected_files,
            "included_files": preview.included_files,
            "truncated": preview.truncated,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print("Code preview: v{0}".format(persisted_preview.version))
            print("Pages: {0}/{1}".format(preview.generated_pages, preview.target_pages))
            print("Sufficient: {0}".format(preview.sufficient))
            print("Artifact: {0}".format(persisted_preview.artifact_path))
        return 0

    if args.command != "scan":
        return 2

    service = ScanProjectService(database=database, data_root=data_dir)
    try:
        persisted = service.execute(args.project)
    except (IngestionError, ScanError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except OSError as error:
        print("Scan failed: {0}".format(error), file=sys.stderr)
        return 1

    summary = {
        "task_id": persisted.task_id,
        "snapshot_id": persisted.snapshot_id,
        "project_root": str(persisted.result.root),
        "root_fingerprint": persisted.result.root_fingerprint,
        "file_count": len(persisted.result.files),
        "total_bytes": persisted.result.total_bytes,
        "ignored_count": persisted.result.ignored_count,
        "ignored_by_reason": persisted.result.ignored_by_reason,
        "skipped_symlink_count": persisted.result.skipped_symlink_count,
        "binary_file_count": sum(
            1 for item in persisted.result.files if item.is_binary
        ),
        "secret_finding_count": len(persisted.result.secret_findings),
        "manifest_path": str(persisted.manifest_path),
        "scan_report_path": str(persisted.scan_report_path),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print("Task: {0}".format(summary["task_id"]))
        print("Project: {0}".format(summary["project_root"]))
        print("Files: {0}".format(summary["file_count"]))
        print("Ignored: {0}".format(summary["ignored_count"]))
        print("Secret findings: {0}".format(summary["secret_finding_count"]))
        print("Manifest: {0}".format(summary["manifest_path"]))
        print("Scan report: {0}".format(summary["scan_report_path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
