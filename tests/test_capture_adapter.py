import json
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.capture_adapter import (
    CaptureAdapterError, ProjectCaptureAdapterService,
)
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ProjectCaptureAdapterServiceTests(unittest.TestCase):
    def _fixture(self, root: Path, package: dict):
        project = root / "project"
        project.mkdir()
        (project / "package.json").write_text(
            json.dumps(package, ensure_ascii=False), encoding="utf-8"
        )
        (project / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")
        data_root = root / "data"
        database = Database(data_root / "app.db")
        database.initialize()
        now = "2026-08-11T00:00:00Z"
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO project_sources(id,kind,original_path,display_name,
                created_at,last_opened_at) VALUES('source','directory',?,'界面项目',?,?)""",
                (str(project), now, now),
            )
            connection.execute(
                """INSERT INTO project_snapshots(id,source_id,root_fingerprint,
                scanner_version,rules_version,summary_json,manifest_relative_path,
                scan_root_mode,scan_root_path,created_at) VALUES
                ('snapshot','source','hash','v1','v1','{}','manifest.jsonl','external',?,?)""",
                (str(project), now),
            )
            connection.execute(
                """INSERT INTO tasks(id,source_id,snapshot_id,status,workflow_version,
                quality_policy_version,created_at,updated_at) VALUES
                ('task','source','snapshot','completed','v1','v1',?,?)""", (now, now),
            )
            connection.execute(
                """INSERT INTO model_configs(id,name,protocol_id,base_url,model_name,
                settings_json,enabled,created_at,updated_at) VALUES
                ('model-config','Local','ollama','http://127.0.0.1:11434','model','{}',1,?,?)""",
                (now, now),
            )
        job = ManualPipelineService(database).create("task", "model-config")
        return data_root, database, job

    def test_plan_only_exposes_declared_safe_node_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database, job = self._fixture(Path(temporary), {
                "packageManager": "npm@11.0.0",
                "scripts": {
                    "dev": "vite --port 4310",
                    "build": "node destructive-build.js",
                    "preview": "vite preview",
                },
                "devDependencies": {"vite": "^7.0.0"},
            })
            service = ProjectCaptureAdapterService(database, data_root)
            plan = service.launch_plan(job["id"])
            self.assertTrue(plan["policy"]["requires_explicit_authorization"])
            self.assertFalse(plan["policy"]["allows_arbitrary_commands"])
            self.assertEqual([item["command_preview"] for item in plan["candidates"]],
                             ["npm run dev", "npm run preview"])
            self.assertEqual(plan["candidates"][0]["default_url"], "http://127.0.0.1:4310")
            self.assertNotIn("build", json.dumps(plan["candidates"]))
            resolved = service.candidate(job["id"], plan["candidates"][0]["id"])
            self.assertEqual(resolved["working_directory"], plan["project_root"])

    def test_changed_or_unknown_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root, database, job = self._fixture(Path(temporary), {
                "scripts": {"dev": "vite"}, "devDependencies": {"vite": "^7"},
            })
            service = ProjectCaptureAdapterService(database, data_root)
            with self.assertRaises(CaptureAdapterError):
                service.candidate(job["id"], "not-a-real-candidate")

    def test_plan_finds_shallow_frontend_package_without_entering_node_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database, job = self._fixture(root, {"name": "workspace"})
            project = root / "project"
            frontend = project / "nichangyunshu-fronted"
            frontend.mkdir()
            (frontend / "package.json").write_text(json.dumps({
                "scripts": {"dev": "vite", "preview": "vite preview"},
                "devDependencies": {"vite": "^6"},
            }), encoding="utf-8")
            dependency = frontend / "node_modules" / "ignored"
            dependency.mkdir(parents=True)
            (dependency / "package.json").write_text(json.dumps({
                "scripts": {"start": "do-not-run"}
            }), encoding="utf-8")
            plan = ProjectCaptureAdapterService(database, data_root).launch_plan(job["id"])
            self.assertEqual(len(plan["candidates"]), 2)
            self.assertTrue(all(item["working_directory"] == str(frontend.resolve())
                                for item in plan["candidates"]))
            self.assertIn("nichangyunshu-fronted", plan["candidates"][0]["title"])

    def test_plan_recommends_frontend_backend_bundle_and_static_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database, job = self._fixture(root, {"name": "workspace"})
            project = root / "project"
            frontend = project / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(json.dumps({
                "scripts": {"dev": "vite"}, "devDependencies": {"vite": "^6"},
            }), encoding="utf-8")
            router = frontend / "src" / "router"
            router.mkdir(parents=True)
            (router / "index.ts").write_text(
                "const routes = [{ path: '/' }, { path: '/showcase' }, { path: '/detail/:id' }]",
                encoding="utf-8",
            )
            backend = project / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text(
                "<project><artifactId>spring-boot-maven-plugin</artifactId></project>",
                encoding="utf-8",
            )
            plan = ProjectCaptureAdapterService(database, data_root).launch_plan(job["id"])
            bundle = plan["candidates"][0]
            self.assertEqual(bundle["kind"], "service_bundle")
            self.assertEqual(len(bundle["services"]), 2)
            self.assertEqual([item["path"] for item in plan["routes"]], ["/", "/showcase"])

    def test_routes_are_ranked_by_persisted_manual_ui_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root, database, job = self._fixture(root, {
                "scripts": {"dev": "vite"}, "devDependencies": {"vite": "^6"},
            })
            router = root / "project" / "src" / "router"
            router.mkdir(parents=True)
            (router / "index.ts").write_text(
                """const routes = [
                { path: '/', name: 'Home', component: () => import('@/pages/HomePage.vue') },
                { path: '/waterfall', name: 'Waterfall',
                  component: () => import('@/pages/waterfall/WaterFullPage.vue') }
                ]""", encoding="utf-8",
            )
            blocks = [{
                "type": "subheading", "title": "瀑布流布局与内容检索交互",
                "evidence_refs": ["source:src/pages/waterfall/WaterFullPage.vue:L1-L90"],
            }, {
                "type": "paragraph", "text": "瀑布流页面支持检索和分页加载。",
                "evidence_refs": ["source:src/pages/waterfall/WaterFullPage.vue:L1-L90"],
            }]
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO manual_section_artifacts(id,job_id,section_key,title,
                    ordinal,status,content_json,evidence_refs_json,inference_notes_json,
                    figure_requests_json,updated_at) VALUES
                    ('ui-section',?,'ui_operations','用户界面与操作说明',7,'generated',
                    ?,?,'[]','[]','2026-08-11T00:00:00Z')""",
                    (job["id"], json.dumps(blocks, ensure_ascii=False),
                     json.dumps(["source:src/pages/waterfall/WaterFullPage.vue:L1-L90"])),
                )
            plan = ProjectCaptureAdapterService(database, data_root).launch_plan(job["id"])
            matched, unmatched = plan["routes"]
            self.assertEqual(matched["path"], "/waterfall")
            self.assertEqual(matched["relevance"], "document_evidence")
            self.assertEqual(matched["title"], "瀑布流布局与内容检索交互")
            self.assertIn("WaterFullPage.vue", matched["reason"])
            self.assertEqual(unmatched["relevance"], "route_only")


if __name__ == "__main__":
    unittest.main()
