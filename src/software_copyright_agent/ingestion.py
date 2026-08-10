import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional, Set

from .domain import SourceKind


class IngestionError(ValueError):
    pass


class UnsafeArchiveError(IngestionError):
    pass


class ArchiveBudgetExceededError(IngestionError):
    pass


@dataclass(frozen=True)
class IngestionLimits:
    max_files: int = 50_000
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    copy_chunk_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class IngestedProject:
    kind: SourceKind
    original_path: Path
    scan_root: Path


class InputIngestor:
    def __init__(self, limits: Optional[IngestionLimits] = None) -> None:
        self._limits = limits or IngestionLimits()

    def ingest(self, source: Path, task_root: Path) -> IngestedProject:
        original = source.expanduser().resolve()
        if not original.exists():
            raise IngestionError("Project path does not exist: {0}".format(original))
        if original.is_dir():
            return IngestedProject(SourceKind.DIRECTORY, original, original)
        if original.is_file() and original.suffix.lower() == ".zip":
            extracted = task_root / "input" / "extracted"
            self._extract_zip(original, extracted)
            return IngestedProject(SourceKind.ZIP, original, self._select_scan_root(extracted))
        raise IngestionError("Project input must be a directory or ZIP file: {0}".format(original))

    def _extract_zip(self, archive: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix="extract-", dir=str(destination.parent))
        )
        try:
            with zipfile.ZipFile(str(archive), "r") as source:
                entries = source.infolist()
                files = [entry for entry in entries if not entry.is_dir()]
                if len(files) > self._limits.max_files:
                    raise ArchiveBudgetExceededError(
                        "ZIP contains too many files: {0}".format(len(files))
                    )

                declared_total = 0
                normalized_paths: Set[str] = set()
                for entry in entries:
                    relative = self._validated_relative_path(entry)
                    collision_key = relative.as_posix().casefold()
                    if collision_key in normalized_paths:
                        raise UnsafeArchiveError(
                            "ZIP contains duplicate or case-colliding path: {0}".format(
                                relative.as_posix()
                            )
                        )
                    normalized_paths.add(collision_key)
                    if entry.is_dir():
                        continue
                    if entry.flag_bits & 0x1:
                        raise UnsafeArchiveError(
                            "Encrypted ZIP entries are not supported: {0}".format(entry.filename)
                        )
                    if entry.file_size > self._limits.max_file_bytes:
                        raise ArchiveBudgetExceededError(
                            "ZIP entry exceeds per-file limit: {0}".format(entry.filename)
                        )
                    declared_total += entry.file_size
                    if declared_total > self._limits.max_total_bytes:
                        raise ArchiveBudgetExceededError("ZIP exceeds total extraction limit")

                actual_total = 0
                for entry in entries:
                    relative = self._validated_relative_path(entry)
                    target = temporary.joinpath(*relative.parts)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry, "r") as input_stream, target.open("xb") as output_stream:
                        actual_total += self._copy_limited(input_stream, output_stream, entry)
                    if actual_total > self._limits.max_total_bytes:
                        raise ArchiveBudgetExceededError("ZIP exceeds total extraction limit")

            if destination.exists():
                raise IngestionError(
                    "Task extraction destination already exists: {0}".format(destination)
                )
            os.replace(str(temporary), str(destination))
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise UnsafeArchiveError("Invalid ZIP archive") from error
        finally:
            if temporary.exists():
                shutil.rmtree(str(temporary))

    def _copy_limited(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        entry: zipfile.ZipInfo,
    ) -> int:
        written = 0
        while True:
            chunk = input_stream.read(self._limits.copy_chunk_bytes)
            if not chunk:
                break
            written += len(chunk)
            if written > self._limits.max_file_bytes or written > entry.file_size:
                raise ArchiveBudgetExceededError(
                    "ZIP entry expanded beyond declared or allowed size: {0}".format(
                        entry.filename
                    )
                )
            output_stream.write(chunk)
        if written != entry.file_size:
            raise UnsafeArchiveError(
                "ZIP entry size does not match metadata: {0}".format(entry.filename)
            )
        return written

    @staticmethod
    def _validated_relative_path(entry: zipfile.ZipInfo) -> PurePosixPath:
        raw_name = entry.filename.replace("\\", "/")
        relative = PurePosixPath(raw_name)
        if not raw_name or relative.is_absolute():
            raise UnsafeArchiveError("ZIP contains an absolute or empty path")
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise UnsafeArchiveError(
                "ZIP contains an unsafe path: {0}".format(entry.filename)
            )
        if ":" in relative.parts[0]:
            raise UnsafeArchiveError(
                "ZIP contains a drive-qualified path: {0}".format(entry.filename)
            )
        file_type = (entry.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise UnsafeArchiveError(
                "ZIP contains a symbolic link: {0}".format(entry.filename)
            )
        return relative

    @staticmethod
    def _select_scan_root(extracted: Path) -> Path:
        children = sorted(extracted.iterdir(), key=lambda child: child.name)
        visible = [child for child in children if child.name not in {"__MACOSX"}]
        if len(visible) == 1 and visible[0].is_dir():
            return visible[0]
        return extracted
