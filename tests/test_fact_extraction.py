import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.fact_extraction import DeterministicFactExtractor
from software_copyright_agent.scanner import ProjectScanner


class DeterministicFactExtractorTests(unittest.TestCase):
    def test_package_json_metadata_frameworks_languages_and_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"name":"desk-agent","version":"2.0.0","dependencies":{"react":"1","@tauri-apps/api":"1"}}',
                encoding="utf-8",
            )
            feature = root / "src" / "features"
            feature.mkdir(parents=True)
            (feature / "main.ts").write_text("export const ok = true;\n", encoding="utf-8")

            extraction = DeterministicFactExtractor().extract(ProjectScanner().scan(root))
            facts = {fact.key: fact for fact in extraction.facts}

            self.assertEqual(facts["project.name"].value, "desk-agent")
            self.assertEqual(facts["project.name"].status, "confirmed")
            self.assertEqual(facts["project.version"].value, "2.0.0")
            self.assertEqual(facts["tech.languages"].value, {"TypeScript": 1})
            self.assertEqual(facts["tech.frameworks"].value, ["React", "Tauri"])
            self.assertEqual(facts["project.modules"].value, ["features"])
            self.assertEqual(extraction.confirmations, ())

    def test_readme_name_and_missing_version_require_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("# 中文软件名称\n\n介绍\n", encoding="utf-8")

            extraction = DeterministicFactExtractor().extract(ProjectScanner().scan(root))
            facts = {fact.key: fact for fact in extraction.facts}

            self.assertEqual(facts["project.name"].value, "中文软件名称")
            self.assertNotIn("project.version", facts)
            version_confirmation = next(item for item in extraction.confirmations
                                        if item.field_key == "project.version")
            self.assertEqual(version_confirmation.candidates, ("V1.0",))
            self.assertEqual(
                [item.field_key for item in extraction.confirmations],
                ["project.name", "project.version"],
            )

    def test_structural_storage_routes_states_and_deployment_are_evidence_linked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"name":"api","version":"1.0.0","dependencies":{"redis":"1"},'
                '"scripts":{"start":"node server.js"}}',
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            source = root / "src"
            source.mkdir()
            (source / "api.py").write_text(
                """import sqlite3
from enum import Enum

@app.post('/orders', response_model=OrderOutput)
def create_order(payload: OrderInput) -> OrderOutput:
    token_name = os.getenv('SERVICE_TOKEN')
    raise HTTPException(status_code=409)

class OrderStatus(str, Enum):
    CREATED = 'created'
    PAID = 'paid'

SCHEMA = '''CREATE TABLE IF NOT EXISTS orders (id TEXT);'''
""",
                encoding="utf-8",
            )
            (source / "state_machine.py").write_text(
                """ALLOWED_TRANSITIONS = {
    TaskStatus.CREATED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED}),
}

def persist(connection, temporary, destination):
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.commit()
        os.replace(temporary, destination)
    except Exception:
        connection.rollback()
        temporary.unlink(missing_ok=True)
""",
                encoding="utf-8",
            )
            orders = source / "orders"
            orders.mkdir()
            (orders / "__init__.py").write_text("", encoding="utf-8")
            (orders / "repository.py").write_text(
                "class OrderRepository: pass\n", encoding="utf-8"
            )
            (orders / "service.py").write_text(
                "from .repository import OrderRepository\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_api.py").write_text(
                "import unittest\nclass ApiTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )

            extraction = DeterministicFactExtractor().extract(ProjectScanner().scan(root))
            facts = {fact.key: fact for fact in extraction.facts}
            evidence = {item.ref: item for item in extraction.evidence}

            self.assertEqual(facts["data.storage"].value, ["Redis", "SQLite"])
            self.assertEqual(facts["data.entities"].value[0]["name"], "orders")
            self.assertEqual(facts["interfaces.catalog"].value[0]["method"], "POST")
            self.assertEqual(facts["interfaces.catalog"].value[0]["path"], "/orders")
            self.assertEqual(
                facts["interfaces.contracts"].value[0]["request_models"], ["OrderInput"]
            )
            self.assertEqual(
                facts["interfaces.contracts"].value[0]["response_model"], "OrderOutput"
            )
            self.assertEqual(facts["interfaces.errors"].value[0]["status_code"], 409)
            self.assertEqual(facts["configuration.items"].value[0]["name"], "SERVICE_TOKEN")
            self.assertEqual(facts["runtime.entrypoints"].value[0]["name"], "start")
            self.assertEqual(facts["testing.strategy"].value["frameworks"], ["unittest"])
            self.assertEqual(
                {(edge["from"], edge["to"]) for edge in facts["workflow.transitions"].value},
                {("created", "running"), ("running", "completed"),
                 ("running", "failed")},
            )
            self.assertEqual(
                {item["kind"] for item in facts["data.transactions"].value},
                {"sqlite_immediate_transaction", "transaction_commit",
                 "transaction_rollback"},
            )
            self.assertEqual(
                {item["kind"] for item in facts["reliability.recovery"].value},
                {"atomic_file_replace", "failed_output_cleanup"},
            )
            self.assertIn(
                {"source_module": "orders.service", "target_module": "orders.repository",
                 "source": "src/orders/service.py", "line": 1},
                facts["architecture.dependencies"].value,
            )
            self.assertEqual(
                facts["data.lifecycle"].value[0]["states"], ["created", "paid"]
            )
            self.assertEqual(facts["deployment.method"].value[0]["kind"], "Dockerfile")
            for key in ("data.storage", "data.entities", "interfaces.catalog",
                        "interfaces.contracts", "interfaces.errors", "configuration.items",
                        "runtime.entrypoints", "testing.strategy", "data.lifecycle",
                        "workflow.transitions", "data.transactions",
                        "reliability.recovery", "architecture.modules",
                        "architecture.dependencies", "deployment.method"):
                self.assertTrue(facts[key].evidence_refs)
                self.assertTrue(all(ref in evidence for ref in facts[key].evidence_refs))
            self.assertTrue(all(item.content_hash for item in evidence.values()
                                if item.ref.startswith("structure:")))

    def test_keywords_and_test_fixtures_do_not_create_structural_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"name":"plain","version":"1.0.0"}', encoding="utf-8"
            )
            source = root / "src"
            source.mkdir()
            (source / "catalog.py").write_text(
                "MARKERS = ['sqlite3', 'redis', 'mongodb', 'postgres']\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_fixture.py").write_text(
                "@app.get('/fake')\n"
                "def fake(): pass\n"
                "SCHEMA = 'CREATE TABLE fake (id TEXT)'\n",
                encoding="utf-8",
            )

            extraction = DeterministicFactExtractor().extract(ProjectScanner().scan(root))
            fact_keys = {fact.key for fact in extraction.facts}

            self.assertNotIn("data.storage", fact_keys)
            self.assertNotIn("data.entities", fact_keys)
            self.assertNotIn("interfaces.catalog", fact_keys)
