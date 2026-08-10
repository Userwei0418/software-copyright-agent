import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from software_copyright_agent.cli import main
from software_copyright_agent.manual_plan import ManualPlanBuilder, PlanningFact


class ManualPlanBuilderTests(unittest.TestCase):
    def test_plan_links_facts_evidence_missing_information_and_diagrams(self) -> None:
        plan = ManualPlanBuilder().build((
            PlanningFact("fact-name", "project.name", "Demo", 1.0, ("e-name",)),
            PlanningFact("fact-version", "project.version", "V1.0", 1.0, ("e-version",)),
            PlanningFact("fact-modules", "project.modules", ["orders", "billing"], 0.8,
                         ("e-modules",)),
        ))

        architecture = next(item for item in plan.sections if item.key == "architecture")
        functions = next(item for item in plan.sections if item.key == "functional_design")
        self.assertIn("fact-modules", architecture.fact_ids)
        self.assertIn("e-modules", architecture.evidence_ids)
        self.assertEqual(functions.subsections, ("orders模块", "billing模块"))
        self.assertEqual(
            {item["key"] for item in plan.diagram_requirements},
            {"system_architecture", "core_business_flow"},
        )
        self.assertIn("核心数据实体", plan.missing_information)
        self.assertEqual(plan.ready_sections + plan.needs_evidence_sections, 9)

    def test_section_becomes_ready_when_all_required_fact_keys_exist(self) -> None:
        keys = (
            "project.name", "project.version", "project.purpose",
            "project.target_users", "project.background", "project.scope",
        )
        facts = tuple(
            PlanningFact("fact-{0}".format(index), key, "value", 1.0, ())
            for index, key in enumerate(keys)
        )

        plan = ManualPlanBuilder().build(facts)
        overview = next(item for item in plan.sections if item.key == "overview")

        self.assertEqual(overview.status, "ready")
        self.assertEqual(overview.missing_information, ())


class ManualPlanCliTests(unittest.TestCase):
    def test_manual_plan_is_versioned_persisted_and_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src" / "orders").mkdir(parents=True)
            (project / "package.json").write_text(
                '{"name":"demo","version":"1.0.0","dependencies":{"react":"1"}}',
                encoding="utf-8",
            )
            (project / "src" / "orders" / "service.py").write_text(
                "def create_order():\n    return True\n", encoding="utf-8"
            )
            data_dir = root / "data"
            scan_output = StringIO()
            with redirect_stdout(scan_output):
                main(["--data-dir", str(data_dir), "scan", str(project), "--json"])
            task_id = json.loads(scan_output.getvalue())["task_id"]

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["--data-dir", str(data_dir), "manual-plan", task_id, "--json"]
                )
            payload = json.loads(output.getvalue())
            artifact = Path(payload["artifact_path"])
            plan = json.loads(artifact.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["section_count"], 9)
            self.assertEqual(payload["diagram_count"], 2)
            self.assertEqual(plan["rules_version"], "manual-plan-v1")
            self.assertTrue(any(section["fact_ids"] for section in plan["sections"]))
            self.assertTrue(plan["missing_information"])

            import sqlite3
            connection = sqlite3.connect(str(data_dir / "app.db"))
            try:
                row = connection.execute(
                    "SELECT version, artifact_relative_path FROM manual_plan_runs"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], "intermediate/manual-planning/plan.v1.json")
