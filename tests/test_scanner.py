import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.domain import FileCategory
from software_copyright_agent.scanner import (
    ProjectScanner,
    ScanBudgetExceededError,
    ScanError,
    ScanLimits,
)


class ProjectScannerTests(unittest.TestCase):
    def test_scan_filters_dependencies_secrets_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "示例 项目"
            root.mkdir()
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            dependencies = root / "node_modules"
            dependencies.mkdir()
            (dependencies / "dependency.js").write_text("ignored", encoding="utf-8")
            try:
                (root / "outside-link").symlink_to(Path(temporary))
            except OSError:
                pass

            result = ProjectScanner().scan(root)

            self.assertEqual([item.relative_path for item in result.files], ["README.md", "app.py"])
            self.assertEqual(result.files[0].category, FileCategory.DOCUMENTATION)
            self.assertEqual(result.files[1].category, FileCategory.SOURCE)
            self.assertGreaterEqual(result.ignored_count, 2)

    def test_fingerprint_is_stable_and_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.py"
            source.write_text("one\n", encoding="utf-8")
            scanner = ProjectScanner()

            first = scanner.scan(root)
            second = scanner.scan(root)
            self.assertEqual(first.root_fingerprint, second.root_fingerprint)

            source.write_text("two\n", encoding="utf-8")
            third = scanner.scan(root)
            self.assertNotEqual(first.root_fingerprint, third.root_fingerprint)

    def test_missing_project_is_rejected(self) -> None:
        with self.assertRaises(ScanError):
            ProjectScanner().scan(Path("/definitely/not/a/project"))

    def test_root_and_nested_gitignore_rules_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text(
                "*.log\ncache/\n!important.log\n/root-only.txt\n",
                encoding="utf-8",
            )
            (root / "debug.log").write_text("ignored", encoding="utf-8")
            (root / "important.log").write_text("kept", encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            (cache / "data.txt").write_text("ignored", encoding="utf-8")
            nested = root / "src"
            nested.mkdir()
            (nested / ".gitignore").write_text("generated.py\n", encoding="utf-8")
            (nested / "generated.py").write_text("ignored", encoding="utf-8")
            (nested / "main.py").write_text("kept", encoding="utf-8")
            (root / "root-only.txt").write_text("ignored", encoding="utf-8")
            (nested / "root-only.txt").write_text("kept", encoding="utf-8")

            result = ProjectScanner().scan(root)

            paths = [item.relative_path for item in result.files]
            self.assertIn("important.log", paths)
            self.assertIn("src/main.py", paths)
            self.assertIn("src/root-only.txt", paths)
            self.assertNotIn("debug.log", paths)
            self.assertNotIn("src/generated.py", paths)
            self.assertEqual(result.ignored_by_reason["gitignore"], 4)

    def test_binary_language_and_secret_findings_are_reported_without_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_value = "super-secret-value-123"
            (root / "config.py").write_text(
                'api_key = "{0}"\n'.format(secret_value), encoding="utf-8"
            )
            (root / "image.bin").write_bytes(b"\x00\x01\x02")

            result = ProjectScanner().scan(root)

            source = next(item for item in result.files if item.relative_path == "config.py")
            binary = next(item for item in result.files if item.relative_path == "image.bin")
            self.assertEqual(source.language, "Python")
            self.assertFalse(source.is_binary)
            self.assertTrue(binary.is_binary)
            self.assertEqual(result.secret_findings[0].rule_id, "assigned_secret")
            self.assertEqual(result.secret_findings[0].line_number, 1)
            self.assertNotIn(secret_value, repr(result.secret_findings))

    def test_scan_file_and_total_byte_budgets_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.py").write_text("1234", encoding="utf-8")
            (root / "two.py").write_text("5678", encoding="utf-8")

            with self.assertRaises(ScanBudgetExceededError):
                ProjectScanner(limits=ScanLimits(max_files=1)).scan(root)

            with self.assertRaises(ScanBudgetExceededError):
                ProjectScanner(limits=ScanLimits(max_total_bytes=7)).scan(root)
