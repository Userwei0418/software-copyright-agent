import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from software_copyright_agent.cli import main
from software_copyright_agent.diagram_plan import DiagramPlanBuilder
from software_copyright_agent.manual_plan import PlanningFact


class DiagramPlanBuilderTests(unittest.TestCase):
    def test_workflow_edges_require_fact_and_evidence(self) -> None:
        plan = DiagramPlanBuilder().build((
            PlanningFact(
                "fact-transitions", "workflow.transitions",
                [{"from": "created", "to": "running"},
                 {"from": "running", "to": "completed"}],
                0.98, ("e-transition",),
            ),
            PlanningFact("fact-modules", "project.modules", ["engine"], 0.8,
                         ("e-modules",)),
            PlanningFact(
                "fact-architecture-modules", "architecture.modules",
                [{"name": "engine.service"}, {"name": "engine.repository"}],
                0.98, ("e-architecture-modules",),
            ),
            PlanningFact(
                "fact-dependencies", "architecture.dependencies",
                [{"source_module": "engine.service",
                  "target_module": "engine.repository",
                  "source": "src/engine/service.py", "line": 1}],
                0.98, ("e-dependency",),
            ),
        ), {"dependency:src/engine/service.py": ("e-dependency-exact",)})

        workflow = next(item for item in plan.diagrams if item.key == "core_business_flow")
        architecture = next(item for item in plan.diagrams if item.key == "system_architecture")
        self.assertEqual(workflow.status, "ready")
        self.assertEqual(len(workflow.nodes), 3)
        self.assertEqual(len(workflow.edges), 2)
        self.assertTrue(all(edge.evidence_ids == ("e-transition",)
                            for edge in workflow.edges))
        self.assertEqual(architecture.status, "ready")
        self.assertEqual(len(architecture.edges), 1)
        self.assertEqual(
            architecture.edges[0].source_locator,
            {"relative_path": "src/engine/service.py", "line": 1},
        )
        self.assertEqual(architecture.edges[0].evidence_ids, ("e-dependency-exact",))
        self.assertTrue(plan.validation["passed"])


class DiagramPlanCliTests(unittest.TestCase):
    def test_diagram_plan_is_versioned_after_manual_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            source = project / "src" / "engine"
            source.mkdir(parents=True)
            (project / "package.json").write_text(
                '{"name":"demo","version":"1.0.0"}', encoding="utf-8"
            )
            (source / "state.py").write_text(
                """ALLOWED_TRANSITIONS = {
    TaskStatus.CREATED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED}),
}
""",
                encoding="utf-8",
            )
            data_dir = root / "data"
            output = StringIO()
            with redirect_stdout(output):
                main(["--data-dir", str(data_dir), "scan", str(project), "--json"])
            task_id = json.loads(output.getvalue())["task_id"]
            with redirect_stdout(StringIO()):
                self.assertEqual(main(
                    ["--data-dir", str(data_dir), "manual-plan", task_id, "--json"]
                ), 0)
            diagram_output = StringIO()
            with redirect_stdout(diagram_output):
                exit_code = main(
                    ["--data-dir", str(data_dir), "diagram-plan", task_id, "--json"]
                )
            payload = json.loads(diagram_output.getvalue())
            artifact = Path(payload["artifact_path"])
            plan = json.loads(artifact.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["diagram_count"], 2)
            self.assertEqual(payload["ready_diagrams"], 1)
            self.assertEqual(payload["edge_count"], 2)
            self.assertTrue(payload["validation_passed"])
            self.assertEqual(plan["rules_version"], "diagram-plan-v1")
            self.assertTrue(all(edge["evidence_ids"] for diagram in plan["diagrams"]
                                for edge in diagram["edges"]))

            import sqlite3
            connection = sqlite3.connect(str(data_dir / "app.db"))
            try:
                row = connection.execute(
                    "SELECT version, artifact_relative_path FROM diagram_plan_runs"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], "intermediate/diagram-planning/plan.v1.json")
