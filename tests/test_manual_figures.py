import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from software_copyright_agent.manual_figures import ManualFigureService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ManualFigureServiceTests(unittest.TestCase):
    def test_generates_versioned_drawio_svg_and_png_from_section_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            database = Database(data_root / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id, kind, original_path, display_name,
                    created_at, last_opened_at) VALUES
                    ('source', 'directory', '/tmp/example', '图表系统', ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(id, source_id, root_fingerprint,
                    scanner_version, rules_version, summary_json, manifest_relative_path,
                    created_at) VALUES ('snapshot', 'source', 'hash', 'v1', 'v1', '{}',
                    'manifest.jsonl', ?)""", (now,),
                )
                connection.execute(
                    """INSERT INTO tasks(id, source_id, snapshot_id, status, workflow_version,
                    quality_policy_version, created_at, updated_at) VALUES
                    ('task', 'source', 'snapshot', 'completed', 'v1', 'v1', ?, ?)""", (now, now),
                )
                connection.execute(
                    """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                    settings_json, enabled, created_at, updated_at) VALUES
                    ('model-config', 'Local', 'ollama', 'http://127.0.0.1:11434',
                    'figure-model', '{}', 1, ?, ?)""", (now, now),
                )
            job = ManualPipelineService(database).create("task", "model-config")
            ref = "source:service.py:L1-L80"
            blocks = [{"type": "paragraph", "text": "入口调用业务服务，业务服务持久化任务状态。",
                       "evidence_refs": [ref], "inference": False}]
            request = [{"type": "figure_request", "figure_key": "system_architecture",
                        "figure_type": "architecture", "title": "系统总体架构图",
                        "purpose": "展示入口、服务与存储", "evidence_refs": [ref]}]
            with database.connect() as connection:
                for ordinal, (key, title, figures) in enumerate((
                    ("architecture", "总体设计", request),
                    ("modules", "功能与模块设计", []),
                ), 1):
                    connection.execute(
                        """INSERT INTO manual_section_artifacts(id, job_id, section_key, title,
                        ordinal, status, content_json, evidence_refs_json, inference_notes_json,
                        figure_requests_json, updated_at) VALUES (?, ?, ?, ?, ?, 'generated',
                        ?, ?, '[]', ?, ?)""",
                        (key, job["id"], key, title, ordinal, json.dumps(blocks),
                         json.dumps([ref]), json.dumps(figures), now),
                    )
                connection.execute(
                    "UPDATE manual_generation_steps SET status='completed' WHERE job_id=? AND step_key='draft'",
                    (job["id"],),
                )

            def fake_call(config, mode, api_key, prompt):
                if "白名单动作" in prompt:
                    return json.dumps({"operations": [{
                        "action": "node.label", "target": "service",
                        "payload": {"value": "核心业务服务"},
                    }]}, ensure_ascii=False)
                return json.dumps({
                    "layout": "layered-vertical",
                    "nodes": [
                        {"key": "entry", "label": "项目入口", "kind": "actor", "layer": 0,
                         "evidence_refs": [ref]},
                        {"key": "service", "label": "业务服务", "kind": "service", "layer": 1,
                         "evidence_refs": [ref]},
                        {"key": "store", "label": "本地存储", "kind": "datastore", "layer": 2,
                         "evidence_refs": [ref]},
                    ],
                    "edges": [
                        {"key": "submit", "source": "entry", "target": "service",
                         "label": "提交任务", "evidence_refs": [ref]},
                        {"key": "persist", "source": "service", "target": "store",
                         "label": "持久化", "evidence_refs": [ref]},
                    ],
                }, ensure_ascii=False)

            service = ManualFigureService(database, data_root, model_call=fake_call)
            result = service.generate_all(job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["figures"]), 2)
            architecture = next(item for item in result["figures"]
                                if item["figure_key"] == "system_architecture")
            for key in ("drawio_relative_path", "svg_relative_path", "png_relative_path"):
                self.assertTrue((data_root / "tasks" / "task" / architecture[key]).is_file())
            png_bytes, media_type = service.read_asset(
                job["id"], "system_architecture", "png"
            )
            self.assertEqual(media_type, "image/png")
            self.assertTrue(png_bytes.startswith(b"\x89PNG"))
            with Image.open(data_root / "tasks" / "task" / architecture["png_relative_path"]) as png:
                self.assertGreater(png.width, 1500)
            regenerated = service.regenerate(job["id"], "system_architecture")
            self.assertEqual(regenerated["version"], 2)
            edited = service.create_revision(job["id"], "system_architecture", [{
                "action": "node.move", "target": "entry", "payload": {"x": 88, "y": 144},
            }])
            self.assertEqual(edited["version"], 3)
            self.assertEqual(edited["edit_source"], "manual")
            current = next(item for item in service.list(job["id"])
                           if item["figure_key"] == "system_architecture")
            entry = next(item for item in current["semantic"]["nodes"]
                         if item["key"] == "entry")
            self.assertEqual(entry["visual_override"]["move"], {"x": 88, "y": 144})
            revisions = service.revisions(job["id"], "system_architecture")
            self.assertEqual([item["version"] for item in revisions], [3, 2, 1])
            self.assertEqual(revisions[0]["operation_count"], 1)
            restored = service.rollback(job["id"], "system_architecture", 1)
            self.assertEqual(restored["version"], 4)
            self.assertNotIn("visual_override", next(
                item for item in service.list(job["id"])[0]["semantic"]["nodes"]
                if item["key"] == "entry"))
            preview = service.ai_preview(job["id"], "system_architecture", "突出核心服务")
            self.assertIn("<svg", preview["preview_svg"])
            self.assertEqual(preview["operations"][0]["action"], "node.label")
            applied = service.create_revision(job["id"], "system_architecture",
                                              preview["operations"], "ai")
            self.assertEqual(applied["version"], 5)
            self.assertEqual(applied["edit_source"], "ai")
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "screenshots")
            self.assertEqual(refreshed["progress"]["completed"], 3)


if __name__ == "__main__":
    unittest.main()
