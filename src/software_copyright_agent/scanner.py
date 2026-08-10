import hashlib
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from .domain import FileCategory, ScanResult, ScannedFile
from .ignore_rules import IgnoreRules
from .secret_detection import SecretFinding, detect_secrets


DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__",
        "node_modules", "vendor", "dist", "build", "coverage", ".next",
        ".nuxt", ".venv", "venv", "target",
    }
)

SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
        "credentials.json", "secrets.json",
    }
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

LANGUAGES = {
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".cs": "C#",
    ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".go": "Go",
    ".h": "C/C++ Header", ".hpp": "C++ Header", ".java": "Java",
    ".js": "JavaScript", ".jsx": "JavaScript JSX", ".kt": "Kotlin",
    ".kts": "Kotlin", ".php": "PHP", ".py": "Python", ".rb": "Ruby",
    ".rs": "Rust", ".scala": "Scala", ".swift": "Swift",
    ".ts": "TypeScript", ".tsx": "TypeScript TSX", ".vue": "Vue",
}
SOURCE_SUFFIXES = frozenset(LANGUAGES)
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


class ScanBudgetExceededError(ScanError):
    pass


@dataclass(frozen=True)
class ScanLimits:
    max_files: int = 100_000
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    binary_sample_bytes: int = 8192


class ProjectScanner:
    def __init__(
        self,
        ignored_directories: Iterable[str] = DEFAULT_IGNORED_DIRECTORIES,
        max_file_bytes: int = None,
        limits: ScanLimits = None,
    ) -> None:
        self._ignored_directories: Set[str] = set(ignored_directories)
        self._limits = limits or ScanLimits()
        if max_file_bytes is not None:
            self._limits = ScanLimits(
                max_files=self._limits.max_files,
                max_file_bytes=max_file_bytes,
                max_total_bytes=self._limits.max_total_bytes,
                binary_sample_bytes=self._limits.binary_sample_bytes,
            )

    def scan(self, project_root: Path) -> ScanResult:
        root = project_root.expanduser().resolve()
        if not root.exists():
            raise ScanError("Project path does not exist: {0}".format(root))
        if not root.is_dir():
            raise ScanError("Project path is not a directory: {0}".format(root))

        files: List[ScannedFile] = []
        findings: List[SecretFinding] = []
        ignored = Counter()
        skipped_symlink_count = 0
        total_bytes = 0
        rules_by_directory = {root: IgnoreRules()}

        for current_root, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            rules = rules_by_directory.get(current, IgnoreRules())
            ignore_file = current / ".gitignore"
            if ignore_file.is_file() and not ignore_file.is_symlink():
                rules = rules.extend_from_file(ignore_file, root)

            retained = []
            for directory in sorted(directories):
                candidate = current / directory
                relative_path = candidate.relative_to(root).as_posix()
                if candidate.is_symlink():
                    skipped_symlink_count += 1
                    ignored["symlink"] += 1
                elif directory in self._ignored_directories:
                    ignored["default_directory"] += 1
                elif rules.ignored(relative_path, is_directory=True):
                    ignored["gitignore"] += 1
                else:
                    retained.append(directory)
                    rules_by_directory[candidate] = rules
            directories[:] = retained

            for filename in sorted(filenames):
                candidate = current / filename
                relative_path = candidate.relative_to(root).as_posix()
                if candidate.is_symlink():
                    skipped_symlink_count += 1
                    ignored["symlink"] += 1
                    continue
                if rules.ignored(relative_path, is_directory=False):
                    ignored["gitignore"] += 1
                    continue
                if self._is_sensitive(filename):
                    ignored["sensitive_filename"] += 1
                    continue
                try:
                    stat_result = candidate.stat()
                except OSError:
                    ignored["unreadable"] += 1
                    continue
                if not candidate.is_file():
                    ignored["not_regular_file"] += 1
                    continue
                if stat_result.st_size > self._limits.max_file_bytes:
                    ignored["file_too_large"] += 1
                    continue
                if len(files) + 1 > self._limits.max_files:
                    raise ScanBudgetExceededError("Project exceeds maximum scanned file count")
                if total_bytes + stat_result.st_size > self._limits.max_total_bytes:
                    raise ScanBudgetExceededError("Project exceeds maximum scanned byte budget")

                sha256, is_binary, text = self._analyze_file(candidate)
                total_bytes += stat_result.st_size
                files.append(
                    ScannedFile(
                        relative_path=relative_path,
                        size=stat_result.st_size,
                        modified_ns=stat_result.st_mtime_ns,
                        sha256=sha256,
                        category=self._categorize(candidate),
                        language=LANGUAGES.get(candidate.suffix.lower()),
                        is_binary=is_binary,
                    )
                )
                if text is not None:
                    findings.extend(detect_secrets(relative_path, text))

        files.sort(key=lambda item: item.relative_path)
        findings.sort(key=lambda item: (item.relative_path, item.line_number, item.rule_id))
        return ScanResult(
            root=root,
            root_fingerprint=self._root_fingerprint(files),
            files=tuple(files),
            ignored_count=sum(ignored.values()),
            skipped_symlink_count=skipped_symlink_count,
            total_bytes=total_bytes,
            ignored_by_reason=dict(sorted(ignored.items())),
            secret_findings=tuple(findings),
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

    def _analyze_file(self, path: Path) -> Tuple[str, bool, Optional[str]]:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        sample = data[: self._limits.binary_sample_bytes]
        is_binary = b"\0" in sample
        if is_binary:
            return digest, True, None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return digest, True, None
        return digest, False, text

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
