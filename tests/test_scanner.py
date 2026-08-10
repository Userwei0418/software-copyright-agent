import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.domain import FileCategory
from software_copyright_agent.scanner import ProjectScanner, ScanError


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
