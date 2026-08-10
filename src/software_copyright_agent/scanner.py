import hashlib
import os
from pathlib import Path
from typing import Iterable, List, Set

from .domain import FileCategory, ScanResult, ScannedFile


DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "__pycache__",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "coverage",
        ".next",
        ".nuxt",
        ".venv",
        "venv",
        "target",
    }
)

SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        "secrets.json",
    }
)

SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

SOURCE_SUFFIXES = frozenset(
    {
        ".c", ".cc", ".cpp", ".cs", ".dart", ".ex", ".exs", ".go",
        ".h", ".hpp", ".java", ".js", ".jsx", ".kt", ".kts", ".php",
        ".py", ".rb", ".rs", ".scala", ".swift", ".ts", ".tsx", ".vue",
    }
)
CONFIG_NAMES = frozenset(
    {
        "cargo.toml", "composer.json", "dockerfile", "gemfile", "go.mod",
        "package.json", "pom.xml", "pyproject.toml", "requirements.txt",
    }
)
CONFIG_SUFFIXES = frozenset({".json", ".toml", ".yaml", ".yml"})
DOCUMENT_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt"})
ASSET_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"})


class ScanError(ValueError):
    pass


class ProjectScanner:
    def __init__(
        self,
        ignored_directories: Iterable[str] = DEFAULT_IGNORED_DIRECTORIES,
        max_file_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._ignored_directories: Set[str] = set(ignored_directories)
        self._max_file_bytes = max_file_bytes

    def scan(self, project_root: Path) -> ScanResult:
        root = project_root.expanduser().resolve()
        if not root.exists():
            raise ScanError("Project path does not exist: {0}".format(root))
        if not root.is_dir():
            raise ScanError("Project path is not a directory: {0}".format(root))

        files: List[ScannedFile] = []
        ignored_count = 0
        skipped_symlink_count = 0

        for current_root, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            retained = []
            for directory in sorted(directories):
                candidate = current / directory
                if candidate.is_symlink():
                    skipped_symlink_count += 1
                elif directory in self._ignored_directories:
                    ignored_count += 1
                else:
                    retained.append(directory)
            directories[:] = retained

            for filename in sorted(filenames):
                candidate = current / filename
                if candidate.is_symlink():
                    skipped_symlink_count += 1
                    continue
                if self._is_sensitive(filename):
                    ignored_count += 1
                    continue
                try:
                    stat = candidate.stat()
                except OSError:
                    ignored_count += 1
                    continue
                if not candidate.is_file() or stat.st_size > self._max_file_bytes:
                    ignored_count += 1
                    continue

                relative_path = candidate.relative_to(root).as_posix()
                files.append(
                    ScannedFile(
                        relative_path=relative_path,
                        size=stat.st_size,
                        modified_ns=stat.st_mtime_ns,
                        sha256=self._hash_file(candidate),
                        category=self._categorize(candidate),
                    )
                )

        files.sort(key=lambda item: item.relative_path)
        fingerprint = self._root_fingerprint(files)
        return ScanResult(
            root=root,
            root_fingerprint=fingerprint,
            files=tuple(files),
            ignored_count=ignored_count,
            skipped_symlink_count=skipped_symlink_count,
            total_bytes=sum(item.size for item in files),
        )

    @staticmethod
    def _is_sensitive(filename: str) -> bool:
        lowered = filename.lower()
        return lowered in SENSITIVE_FILE_NAMES or lowered.endswith(SENSITIVE_SUFFIXES)

    @staticmethod
    def _categorize(path: Path) -> FileCategory:
        lowered_name = path.name.lower()
        suffix = path.suffix.lower()
        if suffix in SOURCE_SUFFIXES:
            return FileCategory.SOURCE
        if lowered_name in CONFIG_NAMES or suffix in CONFIG_SUFFIXES:
            return FileCategory.CONFIG
        if suffix in DOCUMENT_SUFFIXES:
            return FileCategory.DOCUMENTATION
        if suffix in ASSET_SUFFIXES:
            return FileCategory.ASSET
        return FileCategory.OTHER

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _root_fingerprint(files: Iterable[ScannedFile]) -> str:
        digest = hashlib.sha256()
        for item in files:
            digest.update(item.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(item.size).encode("ascii"))
            digest.update(b"\0")
            digest.update(item.sha256.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()
