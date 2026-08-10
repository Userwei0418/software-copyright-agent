from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class SourceKind(str, Enum):
    DIRECTORY = "directory"
    ZIP = "zip"


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    FAILED = "failed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    COMPLETED = "completed"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    STALE = "stale"


class FileCategory(str, Enum):
    SOURCE = "source"
    CONFIG = "config"
    DOCUMENTATION = "documentation"
    ASSET = "asset"
    OTHER = "other"


@dataclass(frozen=True)
class ScannedFile:
    relative_path: str
    size: int
    modified_ns: int
    sha256: str
    category: FileCategory


@dataclass(frozen=True)
class ScanResult:
    root: Path
    root_fingerprint: str
    files: tuple
    ignored_count: int
    skipped_symlink_count: int
    total_bytes: int


@dataclass(frozen=True)
class PersistedScan:
    task_id: str
    source_id: str
    snapshot_id: str
    manifest_path: Path
    result: ScanResult
    warning: Optional[str] = None
