import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from software_copyright_agent.cli import main


class CliTests(unittest.TestCase):
    def test_scan_command_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--data-dir",
                        str(root / "data"),
                        "scan",
                        str(project),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["file_count"], 1)
            self.assertTrue(Path(payload["manifest_path"]).is_file())

    def test_missing_project_returns_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            error = StringIO()
            with redirect_stderr(error):
                exit_code = main(
                    [
                        "--data-dir",
                        str(Path(temporary) / "data"),
                        "scan",
                        str(Path(temporary) / "missing"),
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("does not exist", error.getvalue())
