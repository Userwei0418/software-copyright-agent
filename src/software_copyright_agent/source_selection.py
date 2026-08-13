import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Set


SOURCE_SELECTION_RULES_VERSION = "source-selection-v4"
SOURCE_SELECTION_STRATEGIES = {
    "standard": {"minimum_score": 50, "relax_path_exclusions": False},
    "relaxed": {"minimum_score": 25, "relax_path_exclusions": False},
    "maximum": {"minimum_score": 0, "relax_path_exclusions": True},
}

EXCLUDED_SEGMENTS = {
    "test": "test_code",
    "tests": "test_code",
    "__tests__": "test_code",
    "mock": "mock_code",
    "mocks": "mock_code",
    "fixture": "fixture_code",
    "fixtures": "fixture_code",
    "vendor": "vendored_code",
    "migration": "migration_code",
    "migrations": "migration_code",
    "demo": "demo_code",
    "demos": "demo_code",
    "sample": "sample_code",
    "samples": "sample_code",
    "example": "example_code",
    "examples": "example_code",
    "generated": "generated_code",
    "legacy": "legacy_code",
    "template": "template_code",
    "templates": "template_code",
    "agent-template": "template_code",
    "agent-templates": "template_code",
}

BUSINESS_SEGMENTS = {
    "domain", "domains", "feature", "features", "service", "services",
    "controller", "controllers", "handler", "handlers", "usecase", "usecases",
    "workflow", "workflows",
}
INFRASTRUCTURE_SEGMENTS = {
    "api", "auth", "database", "db", "repository", "repositories", "route",
    "routes", "storage", "worker", "workers",
}
GENERIC_SEGMENTS = {"common", "constant", "constants", "types", "util", "utils"}
ENTRY_STEMS = {"app", "main", "server", "bootstrap", "application"}


@dataclass(frozen=True)
class SourceCandidate:
    relative_path: str
    grade: str
    selected: bool
    score: int
    code_lines: int
    byte_size: int
    language: Optional[str]
    reasons: tuple
    exclusion_code: Optional[str]


@dataclass(frozen=True)
class SourcePlan:
    candidates: tuple
    selected_files: int
    selected_code_lines: int
    total_source_files: int
    excluded_files: int


class SourceSelector:
    def build(
        self,
        scan_root: Path,
        manifest_path: Path,
        secret_paths: Iterable[str] = (),
        strategy: str = "standard",
    ) -> SourcePlan:
        if strategy not in SOURCE_SELECTION_STRATEGIES:
            raise ValueError("Unknown source selection strategy: {0}".format(strategy))
        policy = SOURCE_SELECTION_STRATEGIES[strategy]
        secrets: Set[str] = set(secret_paths)
        candidates: List[SourceCandidate] = []
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["category"] != "source":
                continue
            relative_path = item["path"]
            exclusion = self._exclusion(
                relative_path, item, secrets, policy["relax_path_exclusions"]
            )
            target = scan_root.joinpath(*PurePosixPath(relative_path).parts)
            code_lines = self._count_code_lines(target) if exclusion is None else 0
            score, reasons = self._score(relative_path, code_lines)
            if exclusion is not None:
                grade = "C"
                selected = False
                score = 0
                reasons = ("excluded:{0}".format(exclusion),)
            elif score >= 70:
                grade = "A"
                selected = True
            elif score >= 50:
                grade = "B"
                selected = True
            else:
                grade = "C"
                selected = score >= policy["minimum_score"]
                reasons = reasons + ("low_business_relevance",)
            candidates.append(
                SourceCandidate(
                    relative_path=relative_path,
                    grade=grade,
                    selected=selected,
                    score=score,
                    code_lines=code_lines,
                    byte_size=int(item["size"]),
                    language=item.get("language"),
                    reasons=reasons,
                    exclusion_code=exclusion,
                )
            )

        candidates.sort(
            key=lambda item: (
                {"A": 0, "B": 1, "C": 2}[item.grade],
                -item.score,
                item.relative_path,
            )
        )
        selected = [item for item in candidates if item.selected]
        return SourcePlan(
            candidates=tuple(candidates),
            selected_files=len(selected),
            selected_code_lines=sum(item.code_lines for item in selected),
            total_source_files=len(candidates),
            excluded_files=sum(1 for item in candidates if item.exclusion_code is not None),
        )

    @staticmethod
    def _exclusion(relative_path: str, item: dict, secret_paths: Set[str],
                   relax_path_exclusions: bool = False) -> Optional[str]:
        if item.get("is_binary"):
            return "binary_source"
        if relative_path in secret_paths:
            return "secret_detected"
        path = PurePosixPath(relative_path)
        lowered_parts = [part.lower() for part in path.parts]
        for part in lowered_parts[:-1]:
            if part in EXCLUDED_SEGMENTS:
                if relax_path_exclusions and EXCLUDED_SEGMENTS[part] in {
                    "test_code", "mock_code", "fixture_code", "migration_code",
                    "demo_code", "sample_code", "example_code",
                }:
                    continue
                return EXCLUDED_SEGMENTS[part]
        lowered_name = path.name.lower()
        if "lottie" in lowered_parts[:-1] or (
            "assets" in lowered_parts[:-1]
            and lowered_name in {"data.js", "animation-data.js", "animation_data.js"}
        ):
            return "generated_animation_data"
        if any(marker in lowered_name for marker in (".test.", ".spec.", ".generated.")):
            if relax_path_exclusions and ".generated." not in lowered_name:
                return None
            return "generated_or_test_file"
        if lowered_name.startswith(("generated-", "generated_")):
            return "generated_code"
        if lowered_name.endswith((".generated.js", ".generated.ts", ".generated.jsx", ".generated.tsx")):
            return "generated_code"
        if path.stem.lower().endswith(("_test", "_spec", ".min")):
            if relax_path_exclusions and not path.stem.lower().endswith(".min"):
                return None
            return "generated_or_test_file"
        return None

    @staticmethod
    def _score(relative_path: str, code_lines: int) -> tuple:
        path = PurePosixPath(relative_path)
        segments = {part.lower() for part in path.parts[:-1]}
        normalized_stem = path.stem.lower().strip("_")
        stem_tokens = set(normalized_stem.replace("-", "_").split("_"))
        score = 45
        reasons: List[str] = ["source_file"]
        is_business = bool(segments & BUSINESS_SEGMENTS or stem_tokens & BUSINESS_SEGMENTS)
        is_entry = normalized_stem in ENTRY_STEMS
        is_infrastructure = bool(segments & INFRASTRUCTURE_SEGMENTS)
        if is_business:
            score += 25
            reasons.append("business_module")
        if is_entry:
            score += 20
            reasons.append("application_entry")
        if is_infrastructure:
            score += 10
            reasons.append("necessary_infrastructure")
        if "src" in segments or "app" in segments or "lib" in segments:
            score += 5
            reasons.append("primary_source_tree")
        if code_lines >= 100:
            score += 5
            reasons.append("substantive_implementation")
        if code_lines < 5 and not (is_business or is_entry or is_infrastructure):
            score -= 15
            reasons.append("minimal_implementation")
        if segments & GENERIC_SEGMENTS:
            score -= 10
            reasons.append("generic_support_code")
        if path.name.lower().endswith(".d.ts"):
            score -= 20
            reasons.append("declaration_only_types")
        return max(0, min(100, score)), tuple(reasons)

    @staticmethod
    def _count_code_lines(path: Path) -> int:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return 0
        return sum(1 for line in text.splitlines() if line.strip())
