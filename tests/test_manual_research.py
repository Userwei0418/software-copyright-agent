import json
import re
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.manual_research import ManualResearchService
from software_copyright_agent.storage import Database


class ManualResearchServiceTests(unittest.TestCase):
    def test_execute_persists_evidence_bound_research_and_advances_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            project = root / "project"
            project.mkdir()
            source = project / "service.py"
            source.write_text(
                "class ProjectService:\n"
                "    def create(self, name):\n"
                "        if not name:\n"
                "            raise ValueError('name required')\n"
                "        return {'name': name}\n",
                encoding="utf-8",
            )
            task_root = data_root / "tasks" / "task"
            manifest = task_root / "input" / "manifest.jsonl"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "path": "service.py", "category": "source", "is_binary": False,
                "language": "Python", "size": source.stat().st_size,
            }) + "\n", encoding="utf-8")
            database = Database(data_root / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id, kind, original_path, display_name,
                    created_at, last_opened_at) VALUES
                    ('source', 'directory', ?, 'Example', ?, ?)""",
                    (str(project), now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id, source_id, root_fingerprint,
                    scanner_version, rules_version, summary_json, manifest_relative_path,
                    scan_root_mode, scan_root_path, created_at) VALUES
                    ('snapshot', 'source', 'hash', 'v1', 'v1', '{}',
                    'input/manifest.jsonl', 'external', ?, ?)""",
                    (str(project), now),
                )
                connection.execute(
                    """INSERT INTO tasks(id, source_id, snapshot_id, status, workflow_version,
                    quality_policy_version, created_at, updated_at) VALUES
                    ('task', 'source', 'snapshot', 'completed', 'v1', 'v1', ?, ?)""",
                    (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                    settings_json, enabled, created_at, updated_at) VALUES
                    ('model-config', 'Local', 'ollama', 'http://127.0.0.1:11434',
                    'research-model', '{}', 1, ?, ?)""",
                    (now, now),
                )
                connection.execute(
                    """INSERT INTO evidence(id, snapshot_id, kind, relative_path, locator_json,
                    excerpt, content_hash, extractor, confidence, sensitivity, created_at)
                    VALUES ('evidence-1', 'snapshot', 'derived', 'service.py', '{}',
                    'ProjectService', 'hash', 'test', 1.0, 'normal', ?)""", (now,),
                )
                connection.execute(
                    """INSERT INTO facts(id, task_id, fact_key, value_json, status, source,
                    confidence, evidence_ids_json, created_at) VALUES
                    ('fact-1', 'task', 'project.modules', '["service"]', 'confirmed',
                    'deterministic', 1.0, '["evidence-1"]', ?)""", (now,),
                )
            job = ManualPipelineService(database).create("task", "model-config")
            captured = {}

            def fake_call(config, mode, api_key, prompt):
                captured["prompt"] = prompt
                source_ref = re.search(
                    r'"ref":"(source:service\.py:[^"]+)"', prompt
                ).group(1)
                return json.dumps({
                    "research_notes": [
                        {"topic": "模块", "classification": "verified",
                         "statement": "项目包含服务模块。",
                         "evidence_refs": ["evidence-1"], "confidence": 0.98},
                        {"topic": "校验", "classification": "verified",
                         "statement": "创建前校验名称。",
                         "evidence_refs": [source_ref], "confidence": 0.9},
                        {"topic": "规模", "classification": "verified",
                         "statement": "支持百万用户。",
                         "evidence_refs": ["invented-ref"], "confidence": 1.0},
                    ],
                    "section_guidance": [{
                        "section_key": "modules", "focus": ["说明服务职责"],
                        "evidence_refs": ["evidence-1"], "open_questions": [],
                    }],
                }, ensure_ascii=False)

            service = ManualResearchService(database, data_root, model_call=fake_call)
            result = service.execute(job["id"])
            self.assertIn("representative_source", captured["prompt"])
            self.assertEqual(result["summary"]["source_file_count"], 1)
            self.assertEqual(result["summary"]["research_note_count"], 3)
            unsupported = result["research_notes"][2]
            self.assertEqual(unsupported["classification"], "pending_confirmation")
            self.assertEqual(unsupported["evidence_refs"], [])
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "draft")
            self.assertEqual(refreshed["progress"]["completed"], 1)
            self.assertEqual(service.latest(job["id"])["version"], 1)
            self.assertTrue(result["summary"]["artifact_relative_path"].startswith(
                "intermediate/manual-research/job-v1/"))

    def test_research_sampling_round_robins_project_layers(self) -> None:
        candidates = [
            {"relative_path": "backend/service/large.py", "score": 100},
            {"relative_path": "backend/service/other.py", "score": 99},
            {"relative_path": "frontend/pages/Home.vue", "score": 60},
            {"relative_path": "backend/controller/user.py", "score": 70},
            {"relative_path": "backend/model/user.py", "score": 50},
            {"relative_path": "frontend/main.ts", "score": 65},
        ]
        ordered = ManualResearchService._diversify_candidates(candidates)
        first = [item["relative_path"] for item in ordered[:5]]
        self.assertIn("frontend/pages/Home.vue", first)
        self.assertIn("backend/controller/user.py", first)
        self.assertIn("backend/service/large.py", first)
        self.assertIn("backend/model/user.py", first)


if __name__ == "__main__":
    unittest.main()
