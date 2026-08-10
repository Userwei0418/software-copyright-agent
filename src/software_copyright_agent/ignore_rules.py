import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List


@dataclass(frozen=True)
class IgnoreRule:
    base_path: str
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool

    def matches(self, relative_path: str, is_directory: bool) -> bool:
        if self.directory_only and not is_directory:
            return False
        candidate = self._relative_to_base(relative_path)
        if candidate is None:
            return False
        pattern = self.pattern
        if self.anchored:
            return fnmatch.fnmatchcase(candidate, pattern)
        if "/" in pattern:
            return fnmatch.fnmatchcase(candidate, pattern)
        return any(fnmatch.fnmatchcase(part, pattern) for part in PurePosixPath(candidate).parts)

    def _relative_to_base(self, relative_path: str) -> str:
        if not self.base_path:
            return relative_path
        prefix = self.base_path.rstrip("/") + "/"
        if not relative_path.startswith(prefix):
            return None
        return relative_path[len(prefix):]


class IgnoreRules:
    def __init__(self, rules: Iterable[IgnoreRule] = ()) -> None:
        self._rules = tuple(rules)

    def extend_from_file(self, ignore_file: Path, project_root: Path) -> "IgnoreRules":
        base = ignore_file.parent.relative_to(project_root).as_posix()
        if base == ".":
            base = ""
        parsed: List[IgnoreRule] = list(self._rules)
        try:
            lines = ignore_file.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            return self
        for raw_line in lines:
            rule = self._parse_line(raw_line, base)
            if rule is not None:
                parsed.append(rule)
        return IgnoreRules(parsed)

    def ignored(self, relative_path: str, is_directory: bool) -> bool:
        ignored = False
        for rule in self._rules:
            if rule.matches(relative_path, is_directory):
                ignored = not rule.negated
        return ignored

    @staticmethod
    def _parse_line(raw_line: str, base: str) -> IgnoreRule:
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            return None
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        elif line.startswith("\\#"):
            line = line[1:]
        if line.startswith("\\!"):
            line = line[1:]
        directory_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = line.startswith("/")
        line = line.lstrip("/")
        if not line:
            return None
        return IgnoreRule(
            base_path=base,
            pattern=line,
            negated=negated,
            directory_only=directory_only,
            anchored=anchored,
        )
