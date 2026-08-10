import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

from .domain import ScanResult


@dataclass(frozen=True)
class EvidenceCandidate:
    ref: str
    kind: str
    relative_path: Optional[str]
    locator: dict
    excerpt: Optional[str]
    content_hash: Optional[str]
    confidence: float


@dataclass(frozen=True)
class FactCandidate:
    key: str
    value: object
    confidence: float
    evidence_refs: tuple
    status: str = "candidate"


@dataclass(frozen=True)
class ConfirmationCandidate:
    field_key: str
    question: str
    candidates: tuple
    evidence_refs: tuple
    required: bool = True


@dataclass(frozen=True)
class FactExtractionResult:
    evidence: tuple
    facts: tuple
    confirmations: tuple


FRAMEWORK_MARKERS = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "express": "Express",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring-boot": "Spring Boot",
    "tauri": "Tauri",
    "electron": "Electron",
}


class DeterministicFactExtractor:
    def extract(self, scan: ScanResult) -> FactExtractionResult:
        evidence: List[EvidenceCandidate] = []
        facts: List[FactCandidate] = []
        confirmations: List[ConfirmationCandidate] = []
        file_hashes = {item.relative_path: item.sha256 for item in scan.files}

        name, version, metadata_evidence = self._extract_metadata(scan.root, file_hashes)
        evidence.extend(metadata_evidence)
        metadata_refs = tuple(item.ref for item in metadata_evidence)
        metadata_confidence = metadata_evidence[0].confidence if metadata_evidence else 0.0

        if name is None:
            name = scan.root.name
            facts.append(FactCandidate("project.name", name, 0.35, ()))
            confirmations.append(
                ConfirmationCandidate(
                    "project.name",
                    "请确认软件名称",
                    (name,),
                    (),
                )
            )
        else:
            name_status = "confirmed" if metadata_confidence >= 0.85 else "candidate"
            facts.append(
                FactCandidate(
                    "project.name",
                    name,
                    metadata_confidence,
                    metadata_refs,
                    status=name_status,
                )
            )
            if name_status != "confirmed":
                confirmations.append(
                    ConfirmationCandidate(
                        "project.name",
                        "README 标题可能是软件名称，请确认",
                        (name,),
                        metadata_refs,
                    )
                )

        if version is None:
            confirmations.append(
                ConfirmationCandidate(
                    "project.version",
                    "项目中未发现可靠版本号，请填写软件版本",
                    (),
                    metadata_refs,
                )
            )
        else:
            facts.append(
                FactCandidate(
                    "project.version",
                    version,
                    metadata_confidence,
                    metadata_refs,
                    status="confirmed" if metadata_confidence >= 0.85 else "candidate",
                )
            )

        languages = Counter(
            item.language for item in scan.files if item.language and not item.is_binary
        )
        if languages:
            language_evidence = EvidenceCandidate(
                ref="scan:languages",
                kind="derived",
                relative_path=None,
                locator={"source": "manifest", "field": "language"},
                excerpt=None,
                content_hash=scan.root_fingerprint,
                confidence=1.0,
            )
            evidence.append(language_evidence)
            facts.append(
                FactCandidate(
                    "tech.languages",
                    dict(sorted(languages.items())),
                    1.0,
                    (language_evidence.ref,),
                )
            )

        frameworks, framework_evidence = self._extract_frameworks(scan.root, file_hashes)
        evidence.extend(framework_evidence)
        if frameworks:
            facts.append(
                FactCandidate(
                    "tech.frameworks",
                    sorted(frameworks),
                    0.85,
                    tuple(item.ref for item in framework_evidence),
                )
            )

        modules = self._module_candidates(scan.files)
        if modules:
            module_evidence = EvidenceCandidate(
                ref="scan:modules",
                kind="derived",
                relative_path=None,
                locator={"source": "manifest", "field": "relative_path"},
                excerpt=None,
                content_hash=scan.root_fingerprint,
                confidence=0.7,
            )
            evidence.append(module_evidence)
            facts.append(
                FactCandidate(
                    "project.modules",
                    modules,
                    0.7,
                    (module_evidence.ref,),
                )
            )

        return FactExtractionResult(tuple(evidence), tuple(facts), tuple(confirmations))

    def _extract_metadata(
        self, root: Path, file_hashes: Dict[str, str]
    ) -> Tuple[Optional[str], Optional[str], List[EvidenceCandidate]]:
        package_json = root / "package.json"
        if package_json.is_file():
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            name = self._clean_string(payload.get("name"))
            version = self._clean_string(payload.get("version"))
            if name or version:
                return name, version, [
                    self._file_evidence(
                        "metadata:package-json",
                        "package.json",
                        {"json_paths": ["$.name", "$.version"]},
                        None,
                        file_hashes,
                        0.95,
                    )
                ]

        for filename, section in (("pyproject.toml", "project"), ("Cargo.toml", "package")):
            path = root / filename
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            block = self._toml_section(text, section)
            name = self._toml_string(block, "name")
            version = self._toml_string(block, "version")
            if name or version:
                return name, version, [
                    self._file_evidence(
                        "metadata:{0}".format(filename.lower()),
                        filename,
                        {"section": section, "keys": ["name", "version"]},
                        None,
                        file_hashes,
                        0.9,
                    )
                ]

        for readme_name in ("README.md", "README.MD", "readme.md"):
            path = root / readme_name
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines[:80], start=1):
                match = re.match(r"^#\s+(.+?)\s*$", line)
                if match:
                    heading = match.group(1).strip()
                    return heading, None, [
                        self._file_evidence(
                            "metadata:readme-heading",
                            readme_name,
                            {"line": line_number},
                            heading[:200],
                            file_hashes,
                            0.65,
                        )
                    ]
        return None, None, []

    def _extract_frameworks(
        self, root: Path, file_hashes: Dict[str, str]
    ) -> Tuple[set, List[EvidenceCandidate]]:
        frameworks = set()
        evidence: List[EvidenceCandidate] = []
        candidates = ["package.json", "pyproject.toml", "requirements.txt", "pom.xml"]
        for filename in candidates:
            path = root / filename
            if not path.is_file():
                continue
            try:
                lowered = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                continue
            matched = sorted(
                label for marker, label in FRAMEWORK_MARKERS.items() if marker in lowered
            )
            if not matched:
                continue
            frameworks.update(matched)
            evidence.append(
                self._file_evidence(
                    "frameworks:{0}".format(filename),
                    filename,
                    {"markers": matched},
                    None,
                    file_hashes,
                    0.85,
                )
            )
        return frameworks, evidence

    @staticmethod
    def _module_candidates(files: Iterable[object]) -> List[str]:
        counts = Counter()
        for item in files:
            if item.category.value != "source":
                continue
            parts = PurePosixPath(item.relative_path).parts
            if len(parts) < 2:
                continue
            module = parts[1] if parts[0] in {"src", "app", "lib"} and len(parts) > 2 else parts[0]
            if module not in {"test", "tests", "mock", "mocks"}:
                counts[module] += 1
        return [name for name, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]

    @staticmethod
    def _file_evidence(
        ref: str,
        relative_path: str,
        locator: dict,
        excerpt: Optional[str],
        file_hashes: Dict[str, str],
        confidence: float,
    ) -> EvidenceCandidate:
        return EvidenceCandidate(
            ref=ref,
            kind="config" if not relative_path.lower().startswith("readme") else "documentation",
            relative_path=relative_path,
            locator=locator,
            excerpt=excerpt,
            content_hash=file_hashes.get(relative_path),
            confidence=confidence,
        )

    @staticmethod
    def _clean_string(value: object) -> Optional[str]:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _toml_section(text: str, section: str) -> str:
        match = re.search(
            r"(?ms)^\[{0}\]\s*$\n(.*?)(?=^\[|\Z)".format(re.escape(section)), text
        )
        return match.group(1) if match else ""

    @staticmethod
    def _toml_string(block: str, key: str) -> Optional[str]:
        match = re.search(
            r"(?m)^\s*{0}\s*=\s*['\"]([^'\"]+)['\"]".format(re.escape(key)), block
        )
        return match.group(1).strip() if match else None
