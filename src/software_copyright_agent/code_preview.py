import hashlib
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List


FORMATTER_VERSION = "code-preview-v1"


class CodePreviewError(ValueError):
    pass


class SourceChangedError(CodePreviewError):
    pass


@dataclass(frozen=True)
class CodePreviewConfig:
    max_visual_width: int = 100
    lines_per_page: int = 50
    target_code_pages: int = 59
    tab_size: int = 4


@dataclass(frozen=True)
class CodeInputFile:
    relative_path: str
    grade: str
    score: int
    language: str
    expected_sha256: str


@dataclass(frozen=True)
class CodePreview:
    pages: tuple
    available_visual_lines: int
    used_visual_lines: int
    required_visual_lines: int
    generated_pages: int
    target_pages: int
    sufficient: bool
    selected_files: int
    included_files: int
    truncated: bool


def character_width(character: str) -> int:
    if unicodedata.category(character) in {"Mn", "Me", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def visual_width(text: str) -> int:
    return sum(character_width(character) for character in text)


def hard_wrap_visual(text: str, max_width: int) -> List[str]:
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    if text == "":
        return [""]
    segments: List[str] = []
    current: List[str] = []
    current_width = 0
    for character in text:
        width = character_width(character)
        if current and current_width + width > max_width:
            segments.append("".join(current))
            current = []
            current_width = 0
        current.append(character)
        current_width += width
    if current:
        segments.append("".join(current))
    return segments


class CodePreviewBuilder:
    def __init__(self, config: CodePreviewConfig = None) -> None:
        self.config = config or CodePreviewConfig()

    def build(self, scan_root: Path, files: Iterable[CodeInputFile]) -> CodePreview:
        entries: List[dict] = []
        selected_files = list(files)
        for file_index, item in enumerate(selected_files):
            path = scan_root.joinpath(*PurePosixPath(item.relative_path).parts)
            data = path.read_bytes()
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != item.expected_sha256:
                raise SourceChangedError(
                    "Source file changed after scan: {0}".format(item.relative_path)
                )
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise CodePreviewError(
                    "Selected source file is not UTF-8: {0}".format(item.relative_path)
                ) from error

            entries.append(
                {
                    "kind": "file_header",
                    "path": item.relative_path,
                    "grade": item.grade,
                    "score": item.score,
                    "language": item.language,
                    "text": "FILE: {0}".format(item.relative_path),
                }
            )
            physical_lines = text.splitlines()
            for line_number, raw_line in enumerate(physical_lines, start=1):
                expanded = raw_line.expandtabs(self.config.tab_size)
                segments = hard_wrap_visual(expanded, self.config.max_visual_width)
                for segment_index, segment in enumerate(segments):
                    entries.append(
                        {
                            "kind": "code",
                            "path": item.relative_path,
                            "source_line": line_number,
                            "segment": segment_index + 1,
                            "continuation": segment_index > 0,
                            "visual_width": visual_width(segment),
                            "text": segment,
                        }
                    )
            if file_index < len(selected_files) - 1:
                entries.append({"kind": "separator", "text": ""})

        required = self.config.target_code_pages * self.config.lines_per_page
        used_entries = entries[:required]
        pages = []
        for start in range(0, len(used_entries), self.config.lines_per_page):
            page_entries = used_entries[start : start + self.config.lines_per_page]
            pages.append(
                {
                    "page_number": len(pages) + 1,
                    "line_count": len(page_entries),
                    "entries": page_entries,
                }
            )
        included_paths = {
            entry["path"] for entry in used_entries if entry.get("path") is not None
        }
        return CodePreview(
            pages=tuple(pages),
            available_visual_lines=len(entries),
            used_visual_lines=len(used_entries),
            required_visual_lines=required,
            generated_pages=math.ceil(len(used_entries) / self.config.lines_per_page)
            if used_entries
            else 0,
            target_pages=self.config.target_code_pages,
            sufficient=len(entries) >= required,
            selected_files=len(selected_files),
            included_files=len(included_paths),
            truncated=len(entries) > required,
        )
