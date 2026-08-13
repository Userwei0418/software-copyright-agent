import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List


FORMATTER_VERSION = "code-preview-v5"


class CodePreviewError(ValueError):
    pass


class SourceChangedError(CodePreviewError):
    pass


@dataclass(frozen=True)
class CodePreviewConfig:
    max_visual_width: int = 90
    lines_per_page: int = 50
    target_code_pages: int = 59
    tab_size: int = 4
    preferred_excerpt_lines_per_file: int = 100


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
    available_buckets: tuple
    included_buckets: tuple
    included_languages: tuple
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
        selected_files = self._balanced_files(list(files))
        entries_by_file = []
        for item in selected_files:
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

            code_entries = []
            physical_lines = text.splitlines()
            preferred_start_line = self._preferred_start_line(
                item.relative_path, physical_lines
            )
            for line_number, raw_line in enumerate(physical_lines, start=1):
                if line_number < preferred_start_line:
                    continue
                expanded = raw_line.expandtabs(self.config.tab_size)
                segments = hard_wrap_visual(expanded, self.config.max_visual_width)
                for segment_index, segment in enumerate(segments):
                    code_entries.append(
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
            entries_by_file.append((item, code_entries))

        # Large controllers, generated clients and entity classes must not monopolize
        # the registration sample.  Interleave bounded, contiguous excerpts from the
        # already layer-balanced file order; only enter a second excerpt round when
        # the first round cannot fill the requested pages.
        entries: List[dict] = []
        excerpt_size = max(1, self.config.preferred_excerpt_lines_per_file)
        maximum_chunks = max(
            (math.ceil(len(code_entries) / excerpt_size)
             for _, code_entries in entries_by_file),
            default=0,
        )
        for chunk_index in range(maximum_chunks):
            start = chunk_index * excerpt_size
            for item, code_entries in entries_by_file:
                chunk = code_entries[start : start + excerpt_size]
                if not chunk:
                    continue
                source_line = chunk[0].get("source_line")
                if chunk_index > 0:
                    suffix = " (continued at source line {0})".format(source_line)
                elif source_line and source_line > 1:
                    suffix = " (excerpt from source line {0})".format(source_line)
                else:
                    suffix = ""
                entries.append(
                    {
                        "kind": "file_header",
                        "path": item.relative_path,
                        "grade": item.grade,
                        "score": item.score,
                        "language": item.language,
                        "text": "FILE: {0}{1}".format(item.relative_path, suffix),
                    }
                )
                entries.extend(chunk)
                entries.append({"kind": "separator", "text": ""})
        if entries and entries[-1]["kind"] == "separator":
            entries.pop()

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
        files_by_path = {item.relative_path: item for item in selected_files}
        available_buckets = tuple(sorted({
            self._source_bucket(item.relative_path) for item in selected_files
        }))
        included_buckets = tuple(sorted({
            self._source_bucket(path) for path in included_paths
        }))
        included_languages = tuple(sorted({
            str(files_by_path[path].language or "Unknown") for path in included_paths
        }))
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
            available_buckets=available_buckets,
            included_buckets=included_buckets,
            included_languages=included_languages,
            truncated=len(entries) > required,
        )

    @classmethod
    def _balanced_files(cls, files: List[CodeInputFile]) -> List[CodeInputFile]:
        """Round-robin project layers while retaining relevance order inside each layer.

        A straight score sort lets a handful of large controllers consume all 59
        pages even when the source plan selected frontend, domain and infrastructure
        files too. Layer rotation keeps each excerpt contiguous, but makes the final
        registration sample representative of the implemented software.
        """
        # The schedule is intentionally weighted.  Equal rotation made declaration-only
        # DTO/entity/type files consume the same page budget as controllers, services and
        # real screens.  Keep cross-stack coverage, but spend most of the registration
        # sample on executable business behaviour.
        bucket_schedule = (
            "backend_controller", "frontend_view", "backend_service", "frontend_view",
            "frontend_component", "backend_controller", "frontend_state",
            "backend_service", "backend_infrastructure", "frontend_client",
            "backend_controller", "frontend_view", "backend_service",
            "frontend_component", "backend_domain", "backend_controller",
            "frontend_view", "backend_service", "frontend_state",
            "backend_infrastructure", "frontend_other", "other",
        )
        bucket_names = tuple(dict.fromkeys(bucket_schedule))
        grouped = {name: [] for name in bucket_names}
        for item in files:
            grouped[cls._source_bucket(item.relative_path)].append(item)
        ordered: List[CodeInputFile] = []
        while any(grouped.values()):
            progressed = False
            for name in bucket_schedule:
                if grouped[name]:
                    ordered.append(grouped[name].pop(0))
                    progressed = True
            if not progressed:
                break
        return ordered

    @staticmethod
    def _preferred_start_line(relative_path: str, lines: List[str]) -> int:
        """Skip import boilerplate while keeping a contiguous implementation excerpt."""
        lowered = relative_path.lower()
        declaration_index = None
        if lowered.endswith((".java", ".kt", ".kts")):
            declaration = re.compile(
                r"\b(class|interface|record|enum|object)\s+[A-Za-z_$][\w$]*"
            )
            declaration_index = next(
                (index for index, line in enumerate(lines) if declaration.search(line)), None
            )
        elif lowered.endswith(".py"):
            declaration = re.compile(r"^\s*(async\s+def|def|class)\s+[A-Za-z_]\w*")
            declaration_index = next(
                (index for index, line in enumerate(lines) if declaration.search(line)), None
            )
        elif lowered.endswith((".ts", ".tsx", ".js", ".jsx")):
            declaration = re.compile(
                r"^\s*(export\s+(default\s+)?|async\s+)?"
                r"(function|class|const|let|interface|type)\b"
            )
            declaration_index = next(
                (index for index, line in enumerate(lines) if declaration.search(line)), None
            )
        if declaration_index is None or declaration_index <= 1:
            return 1

        # Keep class-level annotations and the immediately attached doc comment, but do
        # not pull the package/import block back into the sample.
        start = declaration_index
        while start > 0:
            previous = lines[start - 1].strip()
            if (
                not previous
                or previous.startswith(("@", "//", "/*", "*", "*/"))
            ):
                start -= 1
                continue
            break
        while start < declaration_index and not lines[start].strip():
            start += 1
        return start + 1

    @staticmethod
    def _source_bucket(relative_path: str) -> str:
        lowered = relative_path.replace("\\", "/").lower()
        parts = tuple(part for part in lowered.split("/") if part)
        is_frontend = (
            any("frontend" in part or "fronted" in part for part in parts)
            or any(part in {"web", "client"} for part in parts)
            or lowered.endswith((".vue", ".tsx", ".jsx"))
        )
        is_backend = any("backend" in part for part in parts) or any(
            part in {"server", "api"} for part in parts
        )
        if is_frontend:
            if any(part in {"views", "view", "pages", "page"} for part in parts):
                return "frontend_view"
            if any(part in {"components", "component"} for part in parts):
                return "frontend_component"
            if any(part in {"api", "apis", "services", "service", "request", "requests"}
                   for part in parts):
                return "frontend_client"
            if any(part in {"store", "stores", "router", "routes", "state"}
                   for part in parts):
                return "frontend_state"
            return "frontend_other"
        if is_backend:
            if any(part in {"controller", "controllers"} for part in parts):
                return "backend_controller"
            if any(part in {"service", "services", "serviceimpl", "usecase", "usecases"}
                   for part in parts):
                return "backend_service"
            if any(part in {"model", "models", "entity", "entities", "domain", "dto", "vo"}
                   for part in parts):
                return "backend_domain"
            return "backend_infrastructure"
        return "other"
