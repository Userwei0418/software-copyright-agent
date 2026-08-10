import ast
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

STRUCTURAL_MAX_FILES = 2_000
STRUCTURAL_MAX_FILE_BYTES = 512 * 1024
STRUCTURAL_MAX_TOTAL_BYTES = 16 * 1024 * 1024
STORAGE_MARKERS = {
    "sqlite": "SQLite", "sqlite3": "SQLite", "sqlalchemy": "SQLAlchemy",
    "postgres": "PostgreSQL", "psycopg": "PostgreSQL", "mysql": "MySQL",
    "redis": "Redis", "mongodb": "MongoDB", "mongoose": "MongoDB",
    "prisma": "Prisma", "typeorm": "TypeORM",
}
SOURCE_STORAGE_PATTERNS = {
    "SQLite": r"(?m)(?:^\s*(?:import\s+sqlite3|from\s+sqlite3\s+import)|\bsqlite3\.connect\s*\()",
    "SQLAlchemy": r"(?m)^\s*(?:import\s+sqlalchemy|from\s+sqlalchemy\s+import)",
    "PostgreSQL": r"(?m)^\s*(?:import\s+psycopg\w*|from\s+psycopg\w*\s+import)",
    "MySQL": r"(?m)^\s*(?:import\s+(?:pymysql|mysql)|from\s+(?:pymysql|mysql)\s+import)",
    "Redis": r"(?m)^\s*(?:import\s+redis|from\s+redis\s+import)",
    "MongoDB": r"(?m)^\s*(?:import\s+pymongo|from\s+pymongo\s+import)",
    "Prisma": r"(?m)^\s*(?:import\s+.*prisma|from\s+prisma\s+import)",
    "TypeORM": r"(?m)^\s*import\s+.*\s+from\s+['\"]typeorm['\"]",
}
STRUCTURAL_EXCLUDED_PARTS = frozenset({
    "test", "tests", "testing", "fixture", "fixtures", "mock", "mocks",
    "demo", "demos", "example", "examples", "sample", "samples",
})
STORAGE_CONFIG_NAMES = frozenset({
    "package.json", "pyproject.toml", "requirements.txt", "pom.xml",
    "cargo.toml", "composer.json", "go.mod", "schema.prisma",
    "application.yml", "application.yaml", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml",
})
DEPLOYMENT_FILES = {
    "dockerfile": "Dockerfile", "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose", "compose.yml": "Docker Compose",
    "compose.yaml": "Docker Compose", "fly.toml": "Fly.io",
    "vercel.json": "Vercel", "netlify.toml": "Netlify",
    "tauri.conf.json": "Tauri desktop bundle", "tauri.conf.json5": "Tauri desktop bundle",
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

        structural_evidence, structural_facts = self._extract_structural_facts(
            scan, file_hashes
        )
        evidence.extend(structural_evidence)
        facts.extend(structural_facts)

        return FactExtractionResult(tuple(evidence), tuple(facts), tuple(confirmations))

    def _extract_structural_facts(
        self, scan: ScanResult, file_hashes: Dict[str, str]
    ) -> Tuple[List[EvidenceCandidate], List[FactCandidate]]:
        evidence: List[EvidenceCandidate] = []
        facts: List[FactCandidate] = []
        storage = set()
        tables = []
        interfaces = []
        lifecycles = []
        deployment = []
        contracts = []
        errors = []
        config_keys = []
        entrypoints = []
        transitions = []
        transaction_mechanisms = []
        recovery_mechanisms = []
        module_dependencies = []
        python_modules = self._python_module_inventory(scan.files)
        refs = {
            "storage": [], "tables": [], "interfaces": [], "contracts": [],
            "errors": [], "lifecycles": [], "deployment": [], "config": [],
            "entrypoints": [],
            "transitions": [], "transactions": [], "recovery": [],
            "module_dependencies": [],
        }
        total_bytes = 0
        selected_files = 0

        for item in scan.files:
            if item.is_binary or item.size > STRUCTURAL_MAX_FILE_BYTES:
                continue
            if item.category.value not in {"source", "config"}:
                continue
            path_parts = {part.lower() for part in PurePosixPath(item.relative_path).parts}
            if path_parts & STRUCTURAL_EXCLUDED_PARTS or PurePosixPath(
                item.relative_path
            ).stem.lower().startswith("test_"):
                continue
            if selected_files >= STRUCTURAL_MAX_FILES or total_bytes + item.size > STRUCTURAL_MAX_TOTAL_BYTES:
                break
            path = scan.root / PurePosixPath(item.relative_path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            selected_files += 1
            total_bytes += item.size
            lowered = text.lower()

            if (item.category.value == "config" and
                    PurePosixPath(item.relative_path).name.lower() in STORAGE_CONFIG_NAMES):
                matched_storage = sorted({
                    label for marker, label in STORAGE_MARKERS.items()
                    if re.search(
                        r"(?<![a-z0-9_]){0}(?![a-z0-9_])".format(re.escape(marker)),
                        lowered,
                    )
                })
            elif item.category.value == "source":
                matched_storage = sorted(
                    label for label, pattern in SOURCE_STORAGE_PATTERNS.items()
                    if re.search(pattern, text)
                )
            else:
                matched_storage = []
            if matched_storage:
                storage.update(matched_storage)
                ref = "structure:storage:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"markers": matched_storage},
                    ", ".join(matched_storage), file_hashes, 0.85,
                ))
                refs["storage"].append(ref)

            table_matches = sorted(set(re.findall(
                r"(?im)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([a-z_][a-z0-9_]*)",
                text,
            )))
            if table_matches:
                tables.extend(
                    {"name": name, "source": item.relative_path} for name in table_matches
                )
                ref = "structure:tables:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"table_names": table_matches},
                    ", ".join(table_matches), file_hashes, 0.95,
                ))
                refs["tables"].append(ref)

            route_matches = self._route_matches(text)
            if route_matches:
                interfaces.extend(
                    {**route, "source": item.relative_path} for route in route_matches
                )
                ref = "structure:routes:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"routes": route_matches},
                    "; ".join("{0} {1}".format(route["method"], route["path"])
                              for route in route_matches[:20]),
                    file_hashes, 0.9,
                ))
                refs["interfaces"].append(ref)

            contract_matches = self._interface_contract_matches(text)
            if contract_matches:
                contracts.extend(
                    {**contract, "source": item.relative_path}
                    for contract in contract_matches
                )
                ref = "structure:contracts:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"contracts": contract_matches},
                    "; ".join(contract["handler"] for contract in contract_matches[:20]),
                    file_hashes, 0.8,
                ))
                refs["contracts"].append(ref)

            error_matches = self._http_error_matches(text)
            if error_matches:
                errors.extend({**error, "source": item.relative_path} for error in error_matches)
                ref = "structure:http-errors:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"http_errors": error_matches},
                    ", ".join(str(error["status_code"]) for error in error_matches),
                    file_hashes, 0.9,
                ))
                refs["errors"].append(ref)

            environment_keys = self._environment_key_matches(text)
            if environment_keys:
                config_keys.extend(
                    {"name": name, "source": item.relative_path} for name in environment_keys
                )
                ref = "structure:config:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"environment_keys": environment_keys},
                    ", ".join(environment_keys), file_hashes, 0.9,
                ))
                refs["config"].append(ref)

            file_entrypoints = self._entrypoint_matches(item.relative_path, text)
            if file_entrypoints:
                entrypoints.extend(file_entrypoints)
                ref = "structure:entrypoints:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"entrypoints": file_entrypoints},
                    ", ".join(entry["name"] for entry in file_entrypoints),
                    file_hashes, 0.9,
                ))
                refs["entrypoints"].append(ref)

            if PurePosixPath(item.relative_path).suffix.lower() == ".py":
                import_matches = self._internal_import_matches(
                    text, item.relative_path, python_modules
                )
                if import_matches:
                    module_dependencies.extend(import_matches)
                    ref = "structure:imports:{0}".format(item.relative_path)
                    evidence.append(self._pattern_evidence(
                        ref, item.relative_path, {"internal_imports": import_matches},
                        "; ".join("{0}->{1}".format(edge["source_module"],
                                                     edge["target_module"])
                                  for edge in import_matches),
                        file_hashes, 0.98,
                    ))
                    refs["module_dependencies"].append(ref)
                transition_matches = self._allowed_transition_matches(text)
                if transition_matches:
                    transitions.extend(
                        {**edge, "source": item.relative_path} for edge in transition_matches
                    )
                    ref = "structure:transitions:{0}".format(item.relative_path)
                    evidence.append(self._pattern_evidence(
                        ref, item.relative_path, {"transitions": transition_matches},
                        "; ".join("{0}->{1}".format(edge["from"], edge["to"])
                                  for edge in transition_matches),
                        file_hashes, 0.98,
                    ))
                    refs["transitions"].append(ref)

            transaction_matches, recovery_matches = self._transaction_recovery_matches(text)
            if transaction_matches:
                transaction_mechanisms.extend(
                    {**item_match, "source": item.relative_path}
                    for item_match in transaction_matches
                )
                ref = "structure:transactions:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"mechanisms": transaction_matches},
                    ", ".join(item_match["kind"] for item_match in transaction_matches),
                    file_hashes, 0.9,
                ))
                refs["transactions"].append(ref)
            if recovery_matches:
                recovery_mechanisms.extend(
                    {**item_match, "source": item.relative_path}
                    for item_match in recovery_matches
                )
                ref = "structure:recovery:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"mechanisms": recovery_matches},
                    ", ".join(item_match["kind"] for item_match in recovery_matches),
                    file_hashes, 0.85,
                ))
                refs["recovery"].append(ref)

            state_matches = self._state_matches(text)
            if state_matches:
                lifecycles.extend(
                    {**state, "source": item.relative_path} for state in state_matches
                )
                ref = "structure:states:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"state_models": state_matches},
                    "; ".join(state["name"] for state in state_matches),
                    file_hashes, 0.85,
                ))
                refs["lifecycles"].append(ref)

            deployment_label = DEPLOYMENT_FILES.get(PurePosixPath(item.relative_path).name.lower())
            if deployment_label:
                deployment.append({"kind": deployment_label, "source": item.relative_path})
                ref = "structure:deployment:{0}".format(item.relative_path)
                evidence.append(self._pattern_evidence(
                    ref, item.relative_path, {"deployment_kind": deployment_label},
                    deployment_label, file_hashes, 0.95,
                ))
                refs["deployment"].append(ref)

        if storage:
            facts.append(FactCandidate("data.storage", sorted(storage), 0.85, tuple(refs["storage"])))
            external_storage = sorted(storage & {"PostgreSQL", "MySQL", "Redis", "MongoDB"})
            if external_storage:
                facts.append(FactCandidate(
                    "deployment.dependencies", external_storage, 0.75,
                    tuple(refs["storage"]),
                ))
        if tables:
            facts.append(FactCandidate("data.entities", tables, 0.95, tuple(refs["tables"])))
        if interfaces:
            facts.append(FactCandidate(
                "interfaces.catalog", interfaces, 0.9, tuple(refs["interfaces"])
            ))
        if contracts:
            facts.append(FactCandidate(
                "interfaces.contracts", contracts, 0.8, tuple(refs["contracts"])
            ))
        if errors:
            facts.append(FactCandidate(
                "interfaces.errors", errors, 0.9, tuple(refs["errors"])
            ))
        if lifecycles:
            facts.append(FactCandidate(
                "data.lifecycle", lifecycles, 0.85, tuple(refs["lifecycles"])
            ))
        if deployment:
            facts.append(FactCandidate(
                "deployment.method", deployment, 0.95, tuple(refs["deployment"])
            ))
        if config_keys:
            facts.append(FactCandidate(
                "configuration.items", config_keys, 0.9, tuple(refs["config"])
            ))
        if entrypoints:
            facts.append(FactCandidate(
                "runtime.entrypoints", entrypoints, 0.9, tuple(refs["entrypoints"])
            ))
        if transitions:
            facts.append(FactCandidate(
                "workflow.transitions", transitions, 0.98, tuple(refs["transitions"])
            ))
        if transaction_mechanisms:
            facts.append(FactCandidate(
                "data.transactions", transaction_mechanisms, 0.9,
                tuple(refs["transactions"]),
            ))
        if recovery_mechanisms:
            facts.append(FactCandidate(
                "reliability.recovery", recovery_mechanisms, 0.85,
                tuple(refs["recovery"]),
            ))
        if python_modules:
            module_ref = "structure:python-modules"
            module_values = [
                {"name": item["name"], "source": path}
                for path, item in sorted(python_modules.items())
            ]
            evidence.append(EvidenceCandidate(
                ref=module_ref, kind="derived", relative_path=None,
                locator={"source": "manifest", "python_module_count": len(module_values)},
                excerpt=None, content_hash=scan.root_fingerprint, confidence=0.98,
            ))
            facts.append(FactCandidate(
                "architecture.modules", module_values, 0.98, (module_ref,)
            ))
        if module_dependencies:
            unique_dependencies = []
            seen_dependencies = set()
            for edge in module_dependencies:
                identity = (edge["source_module"], edge["target_module"])
                if identity in seen_dependencies:
                    continue
                seen_dependencies.add(identity)
                unique_dependencies.append(edge)
            facts.append(FactCandidate(
                "architecture.dependencies", unique_dependencies, 0.98,
                tuple(refs["module_dependencies"]),
            ))
        testing_evidence, testing_fact = self._testing_fact(scan)
        evidence.extend(testing_evidence)
        if testing_fact is not None:
            facts.append(testing_fact)
        return evidence, facts

    @staticmethod
    def _route_matches(text: str) -> List[dict]:
        matches = []
        patterns = (
            r"(?im)^\s*@(?:app|router|blueprint)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
            r"(?im)\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
            r"(?im)@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)",
        )
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = (match.group(1).upper(), match.group(2))
                if value not in seen:
                    seen.add(value)
                    matches.append({"method": value[0], "path": value[1],
                                    "line": text.count("\n", 0, match.start()) + 1})
        return matches

    @staticmethod
    def _state_matches(text: str) -> List[dict]:
        matches = []
        for match in re.finditer(
            r"(?m)^class\s+([A-Za-z_][A-Za-z0-9_]*(?:Status|State))\s*\([^)]*(?:Enum|str)[^)]*\)\s*:",
            text,
        ):
            start = match.end()
            following = text[start:]
            next_class = re.search(r"(?m)^class\s+", following)
            block = following[:next_class.start()] if next_class else following
            values = re.findall(r"(?m)^\s+[A-Z][A-Z0-9_]*\s*=\s*['\"]([^'\"]+)['\"]", block)
            if values:
                matches.append({"name": match.group(1), "states": values,
                                "line": text.count("\n", 0, match.start()) + 1})
        return matches

    @staticmethod
    def _interface_contract_matches(text: str) -> List[dict]:
        matches = []
        pattern = re.compile(
            r"(?ms)^\s*@(?:app|router|blueprint)\.(?:get|post|put|patch|delete)"
            r"\((.*?)\)\s*\n\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*\((.*?)\)\s*(?:->\s*([^:\n]+))?:"
        )
        primitive = {"str", "int", "float", "bool", "bytes", "request", "response"}
        for match in pattern.finditer(text):
            decorator_args, handler, parameters, return_annotation = match.groups()
            response_match = re.search(
                r"\bresponse_model\s*=\s*([A-Za-z_][A-Za-z0-9_.\[\], ]*)",
                decorator_args,
            )
            request_models = []
            for annotation in re.findall(
                r"[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_.\[\]]*)",
                parameters,
            ):
                base = annotation.rsplit(".", 1)[-1].split("[", 1)[0].lower()
                if base not in primitive and base not in {"depends", "optional", "list", "dict"}:
                    request_models.append(annotation)
            response_model = response_match.group(1).strip() if response_match else None
            if response_model is None and return_annotation:
                response_model = return_annotation.strip()
            if request_models or response_model:
                matches.append({
                    "handler": handler,
                    "request_models": list(dict.fromkeys(request_models)),
                    "response_model": response_model,
                    "line": text.count("\n", 0, match.start()) + 1,
                })
        return matches

    @staticmethod
    def _http_error_matches(text: str) -> List[dict]:
        matches = []
        seen = set()
        patterns = (
            r"HTTPException\s*\([^)]*?status_code\s*=\s*(?:status\.)?HTTP_([1-5][0-9]{2})",
            r"HTTPException\s*\([^)]*?status_code\s*=\s*([1-5][0-9]{2})",
            r"\babort\s*\(\s*([1-5][0-9]{2})",
            r"\b(?:res|response)\.status\s*\(\s*([1-5][0-9]{2})\s*\)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                value = (int(match.group(1)), text.count("\n", 0, match.start()) + 1)
                if value not in seen:
                    seen.add(value)
                    matches.append({"status_code": value[0], "line": value[1]})
        return matches

    @staticmethod
    def _environment_key_matches(text: str) -> List[str]:
        patterns = (
            r"\bos\.(?:getenv|environ\.get)\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]",
            r"\bos\.environ\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]",
            r"\bprocess\.env\.([A-Z][A-Z0-9_]*)",
            r"\bSystem\.getenv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]",
        )
        return sorted({match.group(1) for pattern in patterns
                       for match in re.finditer(pattern, text)})

    @staticmethod
    def _entrypoint_matches(relative_path: str, text: str) -> List[dict]:
        name = PurePosixPath(relative_path).name.lower()
        entries = []
        if name == "pyproject.toml":
            block = DeterministicFactExtractor._toml_section(text, "project.scripts")
            for match in re.finditer(
                r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*['\"]([^'\"]+)['\"]", block
            ):
                entries.append({"name": match.group(1), "target": match.group(2),
                                "source": relative_path})
        elif name == "package.json":
            try:
                scripts = json.loads(text).get("scripts", {})
            except (json.JSONDecodeError, AttributeError):
                scripts = {}
            if isinstance(scripts, dict):
                entries.extend(
                    {"name": str(key), "target": None, "source": relative_path}
                    for key in sorted(scripts) if str(key).strip()
                )
        elif name == "dockerfile":
            for match in re.finditer(r"(?im)^\s*(CMD|ENTRYPOINT)\b", text):
                entries.append({"name": match.group(1).upper(), "target": None,
                                "source": relative_path})
        return entries

    @staticmethod
    def _testing_fact(scan: ScanResult) -> Tuple[List[EvidenceCandidate], Optional[FactCandidate]]:
        test_files = []
        frameworks = set()
        analyzed_files = 0
        analyzed_bytes = 0
        for item in scan.files:
            parts = {part.lower() for part in PurePosixPath(item.relative_path).parts}
            stem = PurePosixPath(item.relative_path).stem.lower()
            if not (parts & {"test", "tests", "spec", "specs"} or
                    stem.startswith("test_") or stem.endswith("_test")):
                continue
            test_files.append(item.relative_path)
            path = scan.root / PurePosixPath(item.relative_path)
            if item.is_binary or item.size > STRUCTURAL_MAX_FILE_BYTES:
                continue
            if (analyzed_files >= STRUCTURAL_MAX_FILES or
                    analyzed_bytes + item.size > STRUCTURAL_MAX_TOTAL_BYTES):
                continue
            try:
                lowered = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                continue
            analyzed_files += 1
            analyzed_bytes += item.size
            for marker, label in (
                ("unittest", "unittest"), ("pytest", "pytest"),
                ("vitest", "Vitest"), ("jest", "Jest"),
                ("junit", "JUnit"), ("xunit", "xUnit"),
            ):
                if re.search(r"(?<![a-z0-9_]){0}(?![a-z0-9_])".format(marker), lowered):
                    frameworks.add(label)
        if not test_files:
            return [], None
        ref = "structure:testing"
        evidence = EvidenceCandidate(
            ref=ref, kind="derived", relative_path=None,
            locator={"source": "manifest", "test_file_count": len(test_files)},
            excerpt=None, content_hash=scan.root_fingerprint, confidence=0.9,
        )
        fact = FactCandidate(
            "testing.strategy",
            {"frameworks": sorted(frameworks), "test_file_count": len(test_files)},
            0.9, (ref,),
        )
        return [evidence], fact

    @staticmethod
    def _allowed_transition_matches(text: str) -> List[dict]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        edges = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "ALLOWED_TRANSITIONS"
                       for target in targets):
                continue
            value = node.value
            if not isinstance(value, ast.Dict):
                continue
            for source_node, target_node in zip(value.keys, value.values):
                source = DeterministicFactExtractor._status_attribute(source_node)
                if source is None:
                    continue
                candidates = []
                if isinstance(target_node, (ast.Set, ast.List, ast.Tuple)):
                    candidates = target_node.elts
                elif (isinstance(target_node, ast.Call) and target_node.args and
                      isinstance(target_node.args[0], (ast.Set, ast.List, ast.Tuple))):
                    candidates = target_node.args[0].elts
                for candidate in candidates:
                    target = DeterministicFactExtractor._status_attribute(candidate)
                    if target is not None:
                        edges.append({"from": source, "to": target,
                                      "line": getattr(source_node, "lineno", node.lineno)})
        return edges

    @staticmethod
    def _python_module_inventory(files: Iterable[object]) -> Dict[str, dict]:
        modules = {}
        for item in files:
            path = PurePosixPath(item.relative_path)
            if path.suffix.lower() != ".py" or item.is_binary:
                continue
            parts_lower = {part.lower() for part in path.parts}
            if parts_lower & STRUCTURAL_EXCLUDED_PARTS or path.stem.lower().startswith("test_"):
                continue
            parts = list(path.parts)
            if parts and parts[0] in {"src", "app", "lib"}:
                parts = parts[1:]
            is_package = bool(parts and parts[-1] == "__init__.py")
            if is_package:
                parts = parts[:-1]
            elif parts:
                parts[-1] = PurePosixPath(parts[-1]).stem
            if not parts or not all(part.isidentifier() for part in parts):
                continue
            modules[item.relative_path] = {
                "name": ".".join(parts), "is_package": is_package,
            }
        return modules

    @staticmethod
    def _internal_import_matches(text: str, relative_path: str,
                                 modules: Dict[str, dict]) -> List[dict]:
        current = modules.get(relative_path)
        if current is None:
            return []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        known = {item["name"] for item in modules.values()}
        current_parts = current["name"].split(".")
        package_parts = current_parts if current["is_package"] else current_parts[:-1]
        edges = []
        seen = set()
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    keep = max(0, len(package_parts) - (node.level - 1))
                    base_parts = package_parts[:keep]
                    if node.module:
                        base_parts.extend(node.module.split("."))
                    base = ".".join(base_parts)
                else:
                    base = node.module or ""
                candidates.extend(
                    "{0}.{1}".format(base, alias.name).strip(".")
                    for alias in node.names
                )
            for candidate in candidates:
                matches = sorted(
                    (name for name in known
                     if candidate == name or candidate.startswith(name + ".")),
                    key=len, reverse=True,
                )
                if not matches:
                    continue
                target = matches[0]
                identity = (current["name"], target)
                if target == current["name"] or identity in seen:
                    continue
                seen.add(identity)
                edges.append({
                    "source_module": current["name"], "target_module": target,
                    "source": relative_path, "line": node.lineno,
                })
        return edges

    @staticmethod
    def _status_attribute(node: ast.AST) -> Optional[str]:
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and
                node.value.id.endswith("Status")):
            return node.attr.lower()
        return None

    @staticmethod
    def _transaction_recovery_matches(text: str) -> Tuple[List[dict], List[dict]]:
        transactions = []
        recovery = []
        patterns = (
            (r"['\"]BEGIN IMMEDIATE['\"]", "sqlite_immediate_transaction", transactions),
            (r"\.(commit)\s*\(", "transaction_commit", transactions),
            (r"\.(rollback)\s*\(", "transaction_rollback", transactions),
            (r"\bos\.replace\s*\(", "atomic_file_replace", recovery),
            (r"\.unlink\s*\(\s*missing_ok\s*=\s*True", "failed_output_cleanup", recovery),
        )
        for pattern, kind, destination in patterns:
            for match in re.finditer(pattern, text):
                item = {"kind": kind, "line": text.count("\n", 0, match.start()) + 1}
                if item not in destination:
                    destination.append(item)
        return transactions, recovery

    @staticmethod
    def _pattern_evidence(ref: str, relative_path: str, locator: dict,
                          excerpt: str, file_hashes: Dict[str, str],
                          confidence: float) -> EvidenceCandidate:
        return EvidenceCandidate(
            ref=ref, kind="source_pattern", relative_path=relative_path,
            locator=locator, excerpt=excerpt[:500],
            content_hash=file_hashes.get(relative_path), confidence=confidence,
        )

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
