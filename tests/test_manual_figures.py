import base64
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from software_copyright_agent.manual_figures import (
    FigureGenerationFailure, ManualFigureError, ManualFigureService, _reader_label,
)
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ManualFigureServiceTests(unittest.TestCase):
    def test_semantic_generation_repairs_at_most_once(self) -> None:
        calls = []
        service = ManualFigureService.__new__(ManualFigureService)
        service._model_call = lambda config, mode, api_key, prompt: (
            calls.append(prompt) or "{}"
        )
        context = {"project_name": "项目", "protocol_id": "ollama", "model": {},
                   "endpoint_mode": "chat_completions"}
        request = {"figure_key": "flow", "title": "流程图", "figure_type": "workflow",
                   "section_title": "操作", "purpose": "说明操作", "section_blocks": [],
                   "section_evidence_refs": []}
        with self.assertRaises(FigureGenerationFailure) as raised:
            service._generate(context, request)
        self.assertEqual(len(calls), 2)
        self.assertEqual(raised.exception.attempt, 2)
        self.assertIn("一次结构修复", str(raised.exception))

    def test_reader_labels_hide_internal_identifiers_without_losing_known_technology(self) -> None:
        self.assertEqual(_reader_label("UserAuthPage"), "用户认证页面")
        self.assertEqual(_reader_label("WaterFullPage"), "首页瀑布流页面")
        self.assertEqual(_reader_label("loginUserStore"), "登录用户状态")
        self.assertEqual(_reader_label("userLoginBySessionUsingPost"), "用户登录接口")
        self.assertEqual(_reader_label("doSearch"), "搜索与筛选处理")
        self.assertEqual(_reader_label("/dress/uploadPicture"), "上传图片接口")
        self.assertEqual(_reader_label("Dress实体"), "服饰信息")
        self.assertEqual(_reader_label("Spring Boot"), "Spring Boot")

    def test_normalize_preserves_raw_label_and_adds_reader_facing_display_label(self) -> None:
        request = {
            "figure_key": "auth_flow", "title": "用户认证流程图",
            "figure_type": "workflow", "section_evidence_refs": ["ref"],
        }
        semantic = ManualFigureService._normalize({
            "layout": "flow-left-right",
            "nodes": [
                {"key": "page", "label": "UserAuthPage", "kind": "component",
                 "evidence_refs": ["ref"]},
                {"key": "api", "label": "userLoginBySessionUsingPost", "kind": "service",
                 "evidence_refs": ["ref"]},
                {"key": "store", "label": "loginUserStore", "kind": "datastore",
                 "evidence_refs": ["ref"]},
            ],
            "edges": [
                {"key": "submit", "source": "page", "target": "api", "label": "提交登录",
                 "evidence_refs": ["ref"]},
                {"key": "save", "source": "api", "target": "store", "label": "保存状态",
                 "evidence_refs": ["ref"]},
            ],
        }, request)
        page = semantic["nodes"][0]
        self.assertEqual(page["label"], "UserAuthPage")
        self.assertEqual(page["display_label"], "用户认证页面")

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
                connection.execute(
                    """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                    settings_json, enabled, created_at, updated_at) VALUES
                    ('model-alt', 'Alternate', 'ollama', 'http://127.0.0.1:11434',
                    'figure-model-alt', '{}', 1, ?, ?)""", (now, now),
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

            ai_prompts = []

            def fake_call(config, mode, api_key, prompt):
                if "白名单动作" in prompt:
                    ai_prompts.append(prompt)
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
            self.assertIn("artifacts/manual/jobs/job-v1/diagrams/",
                          architecture["drawio_relative_path"])
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
                "action": "node.move", "target": "entry", "payload": {"x": 220, "y": 88},
            }])
            self.assertEqual(edited["version"], 3)
            self.assertEqual(edited["edit_source"], "manual")
            current = next(item for item in service.list(job["id"])
                           if item["figure_key"] == "system_architecture")
            entry = next(item for item in current["semantic"]["nodes"]
                         if item["key"] == "entry")
            self.assertEqual(entry["visual_override"]["move"], {"x": 220, "y": 88})
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
            before_editor = next(item for item in service.list(job["id"])
                                 if item["figure_key"] == "system_architecture")
            task_root = data_root / "tasks" / "task"
            editor_xml = (task_root / before_editor["drawio_relative_path"]).read_text(
                encoding="utf-8").replace(
                    "</root>", '<mxCell id="custom-manual" value="手工新增图层" '
                    'vertex="1" parent="1"><mxGeometry x="20" y="20" width="100" '
                    'height="40" as="geometry" /></mxCell></root>', 1)
            ai_patch = service.ai_patch_editor_xml(
                job["id"], "system_architecture", "调整核心服务名称", editor_xml)
            self.assertIn("custom-manual", ai_patch["xml"])
            self.assertIn("核心业务服务", ai_patch["xml"])
            self.assertFalse(ai_patch["context_cache_hit"])
            self.assertIn("drawio-xml-editor-v4", ai_prompts[-1])
            self.assertIn("custom-manual", ai_prompts[-1])
            cached_patch = service.ai_patch_editor_xml(
                job["id"], "system_architecture", "保持上下文继续优化名称", ai_patch["xml"])
            self.assertTrue(cached_patch["context_cache_hit"])
            self.assertIn("调整核心服务名称", ai_prompts[-1])

            streamed_events = []
            streamed_models = []
            def fake_stream(config, mode, api_key, prompt, on_delta):
                streamed_models.append(config["id"])
                content = json.dumps({"operations": [{
                    "action": "node.label", "target": "service",
                    "payload": {"value": "流式核心服务"},
                }]}, ensure_ascii=False)
                for chunk in (content[:18], content[18:]):
                    on_delta(chunk)
                return content

            stream_service = ManualFigureService(
                database, data_root, model_call=fake_call, model_stream_call=fake_stream,
            )
            streamed_patch = stream_service.ai_patch_editor_xml(
                job["id"], "system_architecture", "流式调整核心服务", cached_patch["xml"],
                "model-alt", streamed_events.append,
            )
            self.assertEqual(streamed_patch["model_config_id"], "model-alt")
            self.assertEqual(streamed_models, ["model-alt"])
            self.assertIn("流式核心服务", streamed_patch["xml"])
            self.assertGreaterEqual(
                len([item for item in streamed_events if item["type"] == "delta"]), 2
            )
            self.assertEqual(streamed_events[0]["phase"], "xml")
            self.assertEqual(streamed_events[-1]["phase"], "validate")
            wrapped_payload = ManualFigureService._parse_editor_payload(
                "我先检查当前图。\n```json\n"
                '[{"action":"node.label","target":"service",'
                '"payload":{"value":"围栏内结果"}}]\n```\n请确认'
            )
            self.assertEqual(
                wrapped_payload["operations"][0]["payload"]["value"], "围栏内结果"
            )
            aliased_payload = ManualFigureService._parse_editor_payload(json.dumps({
                "result": {"changes": [{
                    "op": "route_edge", "cell_id": "submit",
                    "params": {"points": [{"x": 10, "y": 20}]},
                }]},
            }))
            self.assertEqual(aliased_payload["operations"][0], {
                "action": "edge.route", "target": "submit",
                "payload": {"points": [{"x": 10, "y": 20}]},
            })
            route_actions, route_limit = ManualFigureService._editor_operation_policy(
                "线条较乱，帮我调整一下"
            )
            self.assertEqual(
                route_actions, {"edge.route", "edge.style", "edge.label"}
            )
            self.assertEqual(route_limit, 6)
            with self.assertRaisesRegex(ManualFigureError, "超出本次请求范围"):
                ManualFigureService._validate_editor_operations([{
                    "action": "node.move", "target": "service",
                    "payload": {"x": 10, "y": 20},
                }], [{"id": "service", "kind": "node"}], route_actions)
            mixed_actions, mixed_limit = ManualFigureService._editor_operation_policy(
                "调整节点间距并整理连线"
            )
            self.assertIn("node.move", mixed_actions)
            self.assertEqual(mixed_limit, 12)
            structure_actions, structure_limit = ManualFigureService._editor_operation_policy(
                "当前图表结构不是很清晰，线条看不出条理性，帮我重新整理"
            )
            self.assertIn("node.move", structure_actions)
            self.assertEqual(structure_limit, 12)

            repair_events = []
            repair_prompts = []

            def repair_stream(config, mode, api_key, prompt, on_delta):
                repair_prompts.append(prompt)
                if len(repair_prompts) == 1:
                    content = '我会调整线条：{"operations":['
                else:
                    content = json.dumps({"operations": [{
                        "action": "node.label", "target": "service",
                        "payload": {"value": "格式修复后的服务"},
                    }]}, ensure_ascii=False)
                on_delta(content)
                return content

            repair_service = ManualFigureService(
                database, data_root, model_call=fake_call,
                model_stream_call=repair_stream,
            )
            repaired_patch = repair_service.ai_patch_editor_xml(
                job["id"], "system_architecture", "修复一次格式后调整名称",
                streamed_patch["xml"], "model-alt", repair_events.append,
            )
            self.assertEqual(len(repair_prompts), 2)
            self.assertIn("格式修复器", repair_prompts[1])
            self.assertEqual(
                [item["phase"] for item in repair_events
                 if item["type"] == "phase"],
                ["xml", "repair", "validate"],
            )
            self.assertIn("格式修复后的服务", repaired_patch["xml"])
            editor_xml = streamed_patch["xml"].replace(
                "流式核心服务", "Draw.io 完整编辑服务", 1)
            editor_svg = (task_root / before_editor["svg_relative_path"]).read_text(
                encoding="utf-8")
            editor_png = "data:image/png;base64," + base64.b64encode(
                (task_root / before_editor["png_relative_path"]).read_bytes()
            ).decode("ascii")
            editor_saved = service.save_editor_revision(
                job["id"], "system_architecture", editor_xml, editor_svg, editor_png)
            self.assertEqual(editor_saved["version"], 6)
            self.assertTrue(editor_saved["editor_managed"])
            current_editor = next(item for item in service.list(job["id"])
                                  if item["figure_key"] == "system_architecture")
            self.assertTrue(current_editor["editor_managed"])
            saved_xml = (task_root / current_editor["drawio_relative_path"]).read_text(
                encoding="utf-8")
            self.assertIn("Draw.io 完整编辑服务", saved_xml)
            self.assertIn("custom-manual", saved_xml)
            restored_editor = service.rollback(job["id"], "system_architecture", 6)
            self.assertEqual(restored_editor["version"], 7)
            restored_current = next(item for item in service.list(job["id"])
                                    if item["figure_key"] == "system_architecture")
            self.assertEqual(
                (task_root / restored_current["drawio_relative_path"]).read_bytes(),
                (task_root / current_editor["drawio_relative_path"]).read_bytes(),
            )
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "screenshots")
            self.assertEqual(refreshed["progress"]["completed"], 2)
            self.assertEqual(refreshed["progress"]["total"], 2)
            self.assertEqual(refreshed["progress"]["percent"], 100)
            diagram_step = next(item for item in refreshed["steps"] if item["key"] == "diagrams")
            self.assertEqual(diagram_step["summary"]["completed_items"], 2)
            self.assertEqual(diagram_step["summary"]["total_items"], 2)

    def test_architecture_semantic_fallback_has_evidence_bound_relations(self) -> None:
        semantic = ManualFigureService._deterministic_semantic({
            "figure_key": "system_architecture_overview",
            "title": "系统架构图", "figure_type": "architecture",
            "section_evidence_refs": ["evidence:architecture"],
            "section_blocks": [{"type": "paragraph", "text": (
                "用户通过 Vue 3 与 TypeScript 前端访问系统，Nginx Web 服务器转发至 "
                "Spring Boot 后端；业务数据写入 MySQL，Redis 保存缓存与会话，"
                "腾讯云 COS 对象存储保存上传文件。"
            )}],
        })
        self.assertIsNotNone(semantic)
        self.assertGreaterEqual(len(semantic["nodes"]), 6)
        self.assertGreaterEqual(len(semantic["edges"]), 5)
        self.assertTrue(all(edge["evidence_refs"] for edge in semantic["edges"]))
        self.assertIn(("backend", "mysql"), {
            (edge["source"], edge["target"]) for edge in semantic["edges"]
        })

    def test_full_editor_rejects_active_svg_and_compressed_or_external_xml(self) -> None:
        with self.assertRaisesRegex(Exception, "DOCTYPE"):
            ManualFigureService._validate_editor_xml(
                '<!DOCTYPE mxfile [<!ENTITY x SYSTEM "file:///etc/passwd">]><mxfile/>'
            )
        with self.assertRaisesRegex(Exception, "未压缩"):
            ManualFigureService._validate_editor_xml(
                '<mxfile><diagram>compressed-payload</diagram></mxfile>'
            )
        with self.assertRaisesRegex(Exception, "不允许脚本"):
            ManualFigureService._validate_editor_svg(
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
            )


if __name__ == "__main__":
    unittest.main()
