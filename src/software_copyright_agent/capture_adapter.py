import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

from .storage import Database


class CaptureAdapterError(ValueError):
    pass


class ProjectCaptureAdapterService:
    """Derives executable launch choices without ever running project code."""

    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()

    def launch_plan(self, job_id: str) -> dict:
        root = self._project_root(job_id)
        node_candidates = self._node_candidates(root)
        backend_candidates = self._maven_candidates(root)
        candidates = self._service_bundles(node_candidates, backend_candidates)
        candidates.extend(node_candidates)
        candidates.extend(backend_candidates)
        routes = self._rank_routes_for_manual(job_id, self._route_candidates(root))
        return {
            "job_id": job_id,
            "project_root": str(root),
            "candidates": candidates,
            "routes": routes,
            "policy": {
                "requires_explicit_authorization": True,
                "allows_arbitrary_commands": False,
                "allows_external_urls": False,
                "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
                "credentials_are_injected": False,
                "process_tree_is_stopped_by_application": True,
                "multi_service_launch_supported": True,
            },
        }

    def candidate(self, job_id: str, candidate_id: str) -> dict:
        for candidate in self.launch_plan(job_id)["candidates"]:
            if candidate["id"] == candidate_id:
                return candidate
        raise CaptureAdapterError("启动候选不存在或项目内容已发生变化，请重新评估")

    def _project_root(self, job_id: str) -> Path:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT j.task_id, ps.scan_root_mode, ps.scan_root_path
                FROM manual_generation_jobs j JOIN tasks t ON t.id=j.task_id
                JOIN project_snapshots ps ON ps.id=t.snapshot_id WHERE j.id=?""",
                (job_id,),
            ).fetchone()
        if row is None:
            raise CaptureAdapterError("说明书任务或项目快照不存在")
        if row["scan_root_mode"] == "task":
            root = self._data_root / "tasks" / row["task_id"] / PurePosixPath(
                row["scan_root_path"]
            )
        else:
            root = Path(row["scan_root_path"])
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise CaptureAdapterError("项目目录已不可用，无法准备启动会话")
        return root

    def _node_candidates(self, root: Path) -> list:
        result = []
        for package_path in self._package_paths(root):
            try:
                if package_path.stat().st_size > 2 * 1024 * 1024:
                    continue
                package = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            working_directory = package_path.parent
            scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
            manager = self._package_manager(working_directory, package.get("packageManager"))
            for script_name in ("dev", "start", "serve", "preview"):
                script = scripts.get(script_name)
                if not isinstance(script, str) or not script.strip():
                    continue
                args = [script_name] if manager == "yarn" else ["run", script_name]
                port = self._infer_port(script, package)
                relative = working_directory.relative_to(root).as_posix()
                scope = "项目根目录" if relative == "." else relative
                candidate = {
                    "kind": "node_script",
                    "title": "{0} · {1} · {2}".format(scope, manager, script_name),
                    "program": manager,
                    "args": args,
                    "working_directory": str(working_directory),
                    "command_preview": " ".join([manager] + args),
                    "script_preview": script[:500],
                    "default_url": "http://127.0.0.1:{0}".format(port),
                    "services": [],
                }
                candidate["id"] = self._candidate_id(candidate)
                result.append(candidate)
        return result

    def _maven_candidates(self, root: Path) -> list:
        result = []
        for pom_path in sorted(root.glob("*/pom.xml")) + ([root / "pom.xml"] if
                (root / "pom.xml").is_file() else []):
            try:
                text = pom_path.read_text(encoding="utf-8", errors="ignore")[:2_000_000]
            except OSError:
                continue
            if "spring-boot" not in text:
                continue
            working_directory = pom_path.parent
            wrapper = working_directory / ("mvnw.cmd" if os.name == "nt" else "mvnw")
            program = str(wrapper) if wrapper.is_file() else "mvn"
            args = ["spring-boot:run"]
            relative = working_directory.relative_to(root).as_posix()
            scope = "项目根目录" if relative == "." else relative
            candidate = {
                "kind": "maven_spring_boot", "title": f"{scope} · Spring Boot",
                "program": program, "args": args,
                "working_directory": str(working_directory),
                "command_preview": " ".join([Path(program).name] + args),
                "script_preview": "由 pom.xml 中的 Spring Boot 插件识别",
                "default_url": "http://127.0.0.1:8080", "services": [],
            }
            candidate["id"] = self._candidate_id(candidate)
            result.append(candidate)
        return result

    def _service_bundles(self, frontends: list, backends: list) -> list:
        if not frontends or not backends:
            return []
        frontend = next((item for item in frontends if item["args"][-1] == "dev"), frontends[0])
        backend = backends[0]
        services = [{key: item[key] for key in (
            "program", "args", "working_directory", "command_preview"
        )} for item in (backend, frontend)]
        candidate = {
            "kind": "service_bundle", "title": "推荐：前端 + 后端（同一授权会话）",
            "program": frontend["program"], "args": frontend["args"],
            "working_directory": frontend["working_directory"],
            "command_preview": "；".join(item["command_preview"] for item in services),
            "script_preview": "先启动后端服务，再启动前端开发服务；任一退出都会显示异常",
            "default_url": frontend["default_url"], "services": services,
        }
        candidate["id"] = self._candidate_id(candidate)
        return [candidate]

    @staticmethod
    def _route_candidates(root: Path) -> list:
        """Extract static SPA routes and their component evidence."""
        routes, seen = [], set()
        ignored = {"node_modules", ".git", "dist", "build", "target"}
        for current, directories, filenames in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            directories[:] = [item for item in directories if item not in ignored] if depth < 5 else []
            for filename in filenames:
                if filename.lower() not in {"index.ts", "index.js", "router.ts", "router.js",
                                            "routes.ts", "routes.js"}:
                    continue
                path = current_path / filename
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")[:1_000_000]
                except OSError:
                    continue
                if "route" not in text.lower() and "path:" not in text:
                    continue
                matches = list(re.finditer(r"\bpath\s*:\s*['\"](/[^'\"]*)['\"]", text))
                for index, match in enumerate(matches):
                    route = match.group(1)
                    if (route in seen or "*" in route or ":" in route or
                            route in {"/403", "/404"}):
                        continue
                    seen.add(route)
                    end = matches[index + 1].start() if index + 1 < len(matches) else min(
                        len(text), match.end() + 700
                    )
                    snippet = text[match.start():end]
                    name_match = re.search(r"\bname\s*:\s*['\"]([^'\"]+)['\"]", snippet)
                    component_match = re.search(
                        r"\bcomponent\s*:\s*[^\n]{0,120}?import\(\s*['\"]([^'\"]+)['\"]",
                        snippet,
                    )
                    name = name_match.group(1).strip() if name_match else ""
                    component = component_match.group(1).strip() if component_match else ""
                    routes.append({
                        "path": route, "title": "首页" if route == "/" else (name or route),
                        "source": str(path.relative_to(root)), "name": name,
                        "component": component,
                        "requires_auth": bool(re.search(r"requiresAuth\s*:\s*true", snippet)),
                    })
                    if len(routes) >= 24:
                        return routes
        return routes

    def _rank_routes_for_manual(self, job_id: str, routes: list) -> list:
        """Rank capture pages against the persisted UI chapter evidence.

        Route discovery alone only says that a page exists.  A screenshot is
        recommended only when its component or route terms can be tied back to
        the generated UI chapter; unmatched routes remain visible as optional
        choices and are never presented as document-derived evidence.
        """
        context = self._manual_ui_context(job_id)
        evidence = context["evidence_refs"]
        content = context["content"].lower()
        topics = context["topics"]
        ranked = []
        for route in routes:
            component = route.get("component", "")
            component_suffix = component.replace("@/", "/").replace("\\", "/")
            component_suffix = component_suffix.lstrip("./")
            component_name = Path(component_suffix).name if component_suffix else ""
            matched_refs = [ref for ref in evidence if component_suffix and (
                component_suffix in ref.replace("\\", "/") or
                (component_name and component_name in ref)
            )]
            topic = next((item["title"] for item in topics if any(
                component_suffix and (component_suffix in ref or component_name in ref)
                for ref in item["evidence_refs"]
            )), "")
            tokens = [token.lower() for token in re.findall(
                r"[A-Za-z][A-Za-z0-9_-]{2,}", " ".join((
                    route.get("path", ""), route.get("name", ""), component_name,
                ))
            ) if token.lower() not in {
                "page", "index", "home", "user", "admin", "vue", "tsx", "jsx", "html",
            }]
            keyword_hits = sorted({token for token in tokens if token in content})
            if matched_refs:
                relevance = "document_evidence"
                reason = "与说明书 UI 章节引用的 {0} 直接对应".format(
                    component_name or route["path"]
                )
                score = 300 + len(matched_refs)
            elif keyword_hits:
                relevance = "document_keyword"
                reason = "页面名称与说明书 UI 章节关键词相符：{0}".format(
                    "、".join(keyword_hits[:3])
                )
                score = 200 + len(keyword_hits)
            else:
                relevance = "route_only"
                reason = "仅从源码路由识别，尚未与说明书章节建立直接证据关系"
                score = 0
            ranked.append({
                **route,
                "title": topic or route["title"],
                "section_key": "ui_operations",
                "relevance": relevance,
                "reason": reason,
                "matched_evidence_refs": matched_refs[:6],
                "score": score,
            })
        ranked.sort(key=lambda item: (-item["score"], item["requires_auth"], item["path"]))
        return ranked

    def _manual_ui_context(self, job_id: str) -> dict:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT content_json,evidence_refs_json FROM manual_section_artifacts
                WHERE job_id=? AND section_key='ui_operations'""", (job_id,),
            ).fetchone()
        if row is None:
            return {"content": "", "evidence_refs": [], "topics": []}
        try:
            blocks = json.loads(row["content_json"] or "[]")
            evidence = json.loads(row["evidence_refs_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            return {"content": "", "evidence_refs": [], "topics": []}
        topics = [{"title": str(item.get("title", "")).strip(),
                   "evidence_refs": item.get("evidence_refs", [])}
                  for item in blocks if item.get("type") == "subheading"]
        content = json.dumps(blocks, ensure_ascii=False)
        return {"content": content, "evidence_refs": evidence, "topics": topics}

    @staticmethod
    def _package_paths(root: Path) -> list:
        """Find shallow app packages without entering dependencies or build outputs."""
        ignored = {"node_modules", ".git", ".idea", ".vscode", "dist", "build",
                   "coverage", ".next", ".nuxt", "target"}
        result = []
        for current, directories, filenames in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            directories[:] = sorted(
                item for item in directories if item not in ignored and not item.startswith(".")
            ) if depth < 3 else []
            if "package.json" in filenames:
                result.append(current_path / "package.json")
            if len(result) >= 24:
                break
        return sorted(result, key=lambda path: (
            len(path.parent.relative_to(root).parts), path.as_posix()
        ))

    @staticmethod
    def _package_manager(root: Path, declared) -> str:
        if isinstance(declared, str):
            name = declared.split("@", 1)[0].strip().lower()
            if name in {"npm", "pnpm", "yarn", "bun"}:
                return name
        for filename, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                               ("bun.lockb", "bun"), ("bun.lock", "bun")):
            if (root / filename).is_file():
                return name
        return "npm"

    @staticmethod
    def _infer_port(script: str, package: dict) -> int:
        match = re.search(r"(?:--port(?:=|\s+)|-p\s+)(\d{2,5})\b", script)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
        dependencies = {}
        for key in ("dependencies", "devDependencies"):
            value = package.get(key)
            if isinstance(value, dict):
                dependencies.update(value)
        if "vite" in dependencies:
            return 5173
        if "@vue/cli-service" in dependencies:
            return 8080
        return 3000

    @staticmethod
    def _candidate_id(candidate: dict) -> str:
        payload = json.dumps(
            {key: candidate[key] for key in ("program", "args", "working_directory", "services")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:24]
