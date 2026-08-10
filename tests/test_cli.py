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

    def test_confirm_command_completes_pending_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "README.md").write_text("# Demo\n", encoding="utf-8")
            data_dir = root / "data"
            scan_output = StringIO()
            with redirect_stdout(scan_output):
                main(["--data-dir", str(data_dir), "scan", str(project), "--json"])
            task_id = json.loads(scan_output.getvalue())["task_id"]

            with redirect_stdout(StringIO()):
                first = main(
                    [
                        "--data-dir", str(data_dir), "confirm", task_id,
                        "project.name", "正式名称", "--json",
                    ]
                )
            output = StringIO()
            with redirect_stdout(output):
                second = main(
                    [
                        "--data-dir", str(data_dir), "confirm", task_id,
                        "project.version", "V1.0", "--json",
                    ]
                )

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(json.loads(output.getvalue())["task_status"], "completed")

    def test_source_plan_command_outputs_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            feature = project / "src" / "features"
            feature.mkdir(parents=True)
            (project / "package.json").write_text(
                '{"name":"demo","version":"1.0.0"}', encoding="utf-8"
            )
            (feature / "orders.py").write_text(
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
                    ["--data-dir", str(data_dir), "source-plan", task_id, "--json"]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["selected_files"], 1)
            self.assertEqual(payload["grades"]["A"], 1)

            preview_output = StringIO()
            with redirect_stdout(preview_output):
                preview_exit = main(
                    ["--data-dir", str(data_dir), "code-preview", task_id, "--json"]
                )
            preview_payload = json.loads(preview_output.getvalue())
            self.assertEqual(preview_exit, 0)
            self.assertFalse(preview_payload["sufficient"])
            self.assertEqual(preview_payload["target_pages"], 59)
