import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from software_copyright_agent.manual_drafting import ManualDraftingService
from software_copyright_agent.manual_pipeline import ManualPipelineService
from software_copyright_agent.storage import Database


class ManualDraftingServiceTests(unittest.TestCase):
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

            def fake_call(config, mode, api_key, prompt):
                section_key = prompt.split("章节 key 为 ", 1)[1].split("。", 1)[0]
                blocks = [
                    {"type": "paragraph", "text": "本系统围绕项目证据组织核心能力，并通过清晰的职责边界完成处理。",
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "paragraph", "text": "服务接收业务输入，执行必要校验后形成稳定输出，同时保留异常恢复路径。",
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "list", "lead": "主要职责包括：",
                     "items": ["接收并校验任务输入信息", "保存阶段结果并支持后续恢复"],
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                    {"type": "table", "title": "模块职责", "headers": ["模块", "职责"],
                     "rows": [["任务模块", "管理执行状态"], ["文档模块", "管理生成产物"]],
                     "evidence_refs": ["source:service.py:L1-L80"], "inference": False},
                ]
                if section_key == "architecture":
                    blocks.append({"type": "figure_request", "figure_key": "system_architecture",
                                   "figure_type": "architecture", "title": "系统架构图",
                                   "purpose": "展示主要模块与协作关系",
                                   "evidence_refs": ["source:service.py:L1-L80"]})
                return json.dumps({"section_key": section_key,
                                   "title": "章节 " + section_key, "blocks": blocks},
                                  ensure_ascii=False)

            service = ManualDraftingService(database, data_root, model_call=fake_call)
            result = service.generate_all(job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["sections"]), 7)
            architecture = next(item for item in result["sections"]
                                if item["section_key"] == "architecture")
            self.assertEqual(architecture["figure_requests"][0]["figure_type"], "architecture")
            regenerated = service.regenerate(job["id"], "modules")
            self.assertEqual(regenerated["version"], 2)
            edited_blocks = regenerated["blocks"]
            edited_blocks[0]["text"] = "人工确认后的正文内容完整描述模块职责、输入、处理、输出和异常恢复方式。"
            edited = service.save_edit(job["id"], "modules", "功能与模块设计", edited_blocks)
            self.assertEqual((edited["version"], edited["origin"], edited["status"]),
                             (3, "user", "confirmed"))
            revisions = service.revisions(job["id"], "modules")
            self.assertEqual([item["version"] for item in revisions], [3, 2, 1])
            refreshed = ManualPipelineService(database).get(job["id"])
            self.assertEqual(refreshed["current_step"], "diagrams")
            self.assertEqual(refreshed["progress"]["completed"], 2)


if __name__ == "__main__":
    unittest.main()
