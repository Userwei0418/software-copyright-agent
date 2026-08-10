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
            self.assertEqual(
                [item.field_key for item in extraction.confirmations],
                ["project.name", "project.version"],
            )
