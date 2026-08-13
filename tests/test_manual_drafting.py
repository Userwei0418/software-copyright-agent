import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from software_copyright_agent.manual_drafting import ManualDraftingService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ManualDraftingServiceTests(unittest.TestCase):
    def test_unverified_test_outcomes_are_rewritten_to_evidence_bound_language(self) -> None:
        source = ("测试文件覆盖了用户权限模块的基础功能，并验证了状态流转的正确性。"
                  "源码中定义了相关断言目标和异常分支。")
        result = ManualDraftingService._sanitize_unverified_outcomes(source)
        self.assertNotIn("验证了状态流转的正确性", result)
        self.assertIn("不表述为已经执行的结果", result)
        self.assertIn("源码中定义了相关断言目标和异常分支", result)

        ensured = "此外，针对预签名 URL 的生成逻辑也进行了验证，确保客户端可以直接访问对象。"
        normalized = ManualDraftingService._sanitize_unverified_outcomes(ensured)
        self.assertNotIn("进行了验证", normalized)
        self.assertIn("不表述为已经执行的结果", normalized)

    def test_ui_payload_requires_every_screenshot_in_confirmed_group_order(self) -> None:
        refs = ["screenshot:a:v2", "screenshot:b:v3"]
        paragraph = "已审核截图显示页面包含明确的标题、内容区域和操作入口，用户可按当前可见控件完成页面内操作。" * 6
        payload = {"section_key": "ui_operations", "title": "用户界面与操作说明",
                   "blocks": [
                       {"type": "subheading", "title": "认证流程", "evidence_refs": [refs[0]]},
                       {"type": "paragraph", "text": paragraph, "evidence_refs": [refs[0]]},
                       {"type": "paragraph", "text": paragraph, "evidence_refs": [refs[1]]},
                       {"type": "list", "lead": "操作步骤", "items": ["查看当前页面状态并选择可见操作入口"],
                        "evidence_refs": [refs[1]]},
                   ]}
        normalized = ManualDraftingService._normalize_ui_payload(payload, refs)
        self.assertEqual(normalized["evidence_refs"], refs)
        payload["blocks"][0]["evidence_refs"] = [refs[1]]
        payload["blocks"][1]["evidence_refs"] = [refs[1]]
        payload["blocks"][2]["evidence_refs"] = [refs[0]]
        with self.assertRaisesRegex(Exception, "页面组顺序"):
            ManualDraftingService._normalize_ui_payload(payload, refs)

    def test_generates_structured_sections_versions_and_manual_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            database = Database(data_root / "app.db")
            database.initialize()
            now = "2026-08-10T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO project_sources(id, kind, original_path, display_name,
                    created_at, last_opened_at) VALUES
                    ('source', 'directory', '/tmp/example', '证据化系统', ?, ?)""", (now, now),
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
                    'draft-model', '{}', 1, ?, ?)""", (now, now),
                )
            job = ManualPipelineService(database).create("task", "model-config")
            research = {
                "schema_version": 1, "job_id": job["id"], "version": 1,
                "project_profile": {"project.modules": ["任务", "文档"]},
                "source_refs": [{"ref": "source:service.py:L1-L80", "path": "service.py",
                                 "start_line": 1, "end_line": 80, "sha256": "hash",
                                 "excerpt": "00001 | class Service:"}],
                "research_notes": [{"topic": "模块", "classification": "verified",
                                    "statement": "项目包含任务和文档模块。",
                                    "evidence_refs": ["source:service.py:L1-L80"],
                                    "confidence": 0.95}],
                "section_guidance": [{"section_key": "modules",
                                      "focus": ["说明模块协作"],
                                      "evidence_refs": ["source:service.py:L1-L80"],
                                      "open_questions": []}],
                "model": "research-model", "input_fingerprint": "fingerprint",
            }
            relative = "intermediate/manual-research/research.v1.json"
            path = data_root / "tasks" / "task" / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(research, ensure_ascii=False), encoding="utf-8")
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO manual_research_artifacts(id, job_id, version, status,
                    project_profile_json, source_refs_json, notes_json, artifact_relative_path,
                    input_fingerprint, model_name, elapsed_ms, created_at)
                    VALUES (?, ?, 1, 'completed', ?, ?, ?, ?, 'fingerprint',
                    'research-model', 10, ?)""",
                    (str(uuid4()), job["id"], json.dumps(research["project_profile"]),
                     json.dumps(research["source_refs"]),
                     json.dumps({"research_notes": research["research_notes"],
                                 "section_guidance": research["section_guidance"]}),
                     relative, now),
                )
                connection.execute(
                    """UPDATE manual_generation_steps SET status = 'completed'
                    WHERE job_id = ? AND step_key = 'research'""", (job["id"],),
                )

            calls = {}

            def fake_call(config, mode, api_key, prompt):
                section_key = prompt.split("章节 key 为 ", 1)[1].split("。", 1)[0]
                calls[section_key] = calls.get(section_key, 0) + 1
                if section_key == "introduction" and calls[section_key] == 1:
                    return '{"section_key":"introduction","blocks":['
                blocks = [
                    {"type": "subheading", "title": "任务与文档职责边界",
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "paragraph", "text": (
                        "本系统围绕项目证据组织任务管理与文档生成能力。任务模块接收项目输入，"
                        "校验必要字段并记录执行状态；文档模块读取已经持久化的阶段结果，按照版本"
                        "形成可追踪产物。两个模块通过明确的数据边界协作，避免界面状态直接替代"
                        "真实任务状态，使重新进入页面后仍能读取同一份执行记录。") * 3,
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "paragraph", "text": (
                        "服务接收业务输入后先执行格式与状态校验，再将处理中间结果写入本地存储。"
                        "后续步骤只消费已经确认的记录，并在输出中保留任务标识、版本和更新时间。"
                        "当一次处理失败时，调用方可以根据保存的状态定位失败阶段，而不需要重新"
                        "构造已经完成的输入，从而形成可解释的恢复路径。") * 3,
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "paragraph", "text": (
                        "文档生成以任务记录为入口，组合结构化章节、图表请求和版本信息。生成结果"
                        "不会覆盖既有版本，而是作为新的产物保存，便于用户比较修改前后的内容。"
                        "界面展示来自持久化记录的进度和结果，因而页面切换只影响当前视图，不会"
                        "终止后台工作或丢失已经生成的章节。最终产物继续引用同一任务的证据，"
                        "使正文、图表和质量检查之间保持可追踪关系。") * 3,
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "list", "lead": "主要职责包括：",
                     "items": ["接收并校验任务输入信息", "保存阶段结果并支持后续恢复"],
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "table", "title": "模块职责", "headers": ["模块", "职责"],
                     "rows": [["任务模块", "管理执行状态"], ["文档模块", "管理生成产物"]],
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                ]
                required_figures = {"architecture": "architecture", "modules": "module",
                                    "data_interfaces": "data_flow", "runtime": "deployment"}
                if section_key in required_figures:
                    blocks.append({"type": "figure_request", "figure_key": section_key + "_figure",
                                   "figure_type": required_figures[section_key], "title": "章节图表",
                                   "purpose": "展示主要模块与协作关系",
                                   "evidence_refs": ["source:service.py:L1-L80"]})
                return json.dumps({"section_key": section_key,
                                   "title": "章节 " + section_key, "blocks": blocks},
                                  ensure_ascii=False)

            service = ManualDraftingService(database, data_root, model_call=fake_call)
            result = service.generate_all(job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(calls["introduction"], 2)
            self.assertEqual(len(result["sections"]), 7)
            architecture = next(item for item in result["sections"]
                                if item["section_key"] == "architecture")
            self.assertEqual(architecture["figure_requests"][0]["figure_type"], "architecture")
            self.assertEqual(architecture["blocks"][0]["type"], "subheading")
            regenerated = service.regenerate(job["id"], "modules")
            self.assertEqual(regenerated["version"], 2)
            edited_blocks = regenerated["blocks"]
            first_paragraph = next(item for item in edited_blocks if item["type"] == "paragraph")
            first_paragraph["text"] = "人工确认后的正文内容完整描述模块职责、输入、处理、输出和异常恢复方式。"
            edited = service.save_edit(job["id"], "modules", "功能与模块设计", edited_blocks)
            self.assertEqual((edited["version"], edited["origin"], edited["status"]),
                             (3, "user", "confirmed"))
            revisions = service.revisions(job["id"], "modules")
            self.assertEqual([item["version"] for item in revisions], [3, 2, 1])
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "diagrams")
            self.assertEqual(refreshed["progress"]["completed"], 7)
            self.assertEqual(refreshed["progress"]["total"], 7)
            self.assertEqual(refreshed["progress"]["percent"], 100)
            draft_step = next(item for item in refreshed["steps"] if item["key"] == "draft")
            self.assertEqual(draft_step["summary"]["completed_items"], 7)
            self.assertEqual(draft_step["summary"]["total_items"], 7)


if __name__ == "__main__":
    unittest.main()
