import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from software_copyright_agent.cli import main


class InspectionCliTests(unittest.TestCase):
    def test_inspect_latest_returns_facts_evidence_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "README.md").write_text("# Demo Tool\n", encoding="utf-8")
            data_dir = root / "data"

            with redirect_stdout(StringIO()):
                scan_exit = main(
                    ["--data-dir", str(data_dir), "scan", str(project), "--json"]
                )
            output = StringIO()
            with redirect_stdout(output):
                inspect_exit = main(
                    ["--data-dir", str(data_dir), "inspect", "--json"]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(scan_exit, 0)
            self.assertEqual(inspect_exit, 0)
            self.assertEqual(payload["task"]["status"], "waiting_for_user")
            self.assertEqual(payload["facts"][0]["key"], "project.name")
            self.assertEqual(
                {item["field_key"] for item in payload["confirmations"]},
                {"project.name", "project.version"},
            )
            self.assertEqual(payload["evidence"][0]["relative_path"], "README.md")
