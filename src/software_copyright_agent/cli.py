import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .ingestion import IngestionError
from .scanner import ScanError
from .service import ScanProjectService
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "scan":
        return 2

    data_dir = args.data_dir.expanduser().resolve()
    database = Database(data_dir / "app.db")
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
        "skipped_symlink_count": persisted.result.skipped_symlink_count,
        "manifest_path": str(persisted.manifest_path),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print("Task: {0}".format(summary["task_id"]))
        print("Project: {0}".format(summary["project_root"]))
        print("Files: {0}".format(summary["file_count"]))
        print("Ignored: {0}".format(summary["ignored_count"]))
        print("Manifest: {0}".format(summary["manifest_path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
