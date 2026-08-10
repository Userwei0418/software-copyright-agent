import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from software_copyright_agent.domain import SourceKind
from software_copyright_agent.ingestion import (
    ArchiveBudgetExceededError,
    IngestionLimits,
    InputIngestor,
    UnsafeArchiveError,
)


class InputIngestorTests(unittest.TestCase):
    def test_directory_is_used_without_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()

            result = InputIngestor().ingest(project, root / "task")

            self.assertEqual(result.kind, SourceKind.DIRECTORY)
            self.assertEqual(result.scan_root, project.resolve())

    def test_zip_with_single_chinese_root_is_extracted_and_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "项目.zip"
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr("示例项目/README.md", "# 示例\n")
                output.writestr("示例项目/src/main.py", "print('ok')\n")

            result = InputIngestor().ingest(archive, root / "task")

            self.assertEqual(result.kind, SourceKind.ZIP)
            self.assertEqual(result.scan_root.name, "示例项目")
            self.assertTrue((result.scan_root / "src" / "main.py").is_file())

    def test_zip_slip_is_rejected_without_writing_outside_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "malicious.zip"
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr("../escaped.txt", "bad")

            with self.assertRaises(UnsafeArchiveError):
                InputIngestor().ingest(archive, root / "task")

            self.assertFalse((root / "escaped.txt").exists())

    def test_symbolic_link_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "link.zip"
            link = zipfile.ZipInfo("project/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr(link, "../../outside")

            with self.assertRaises(UnsafeArchiveError):
                InputIngestor().ingest(archive, root / "task")

    def test_case_colliding_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "collision.zip"
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr("src/App.py", "one")
                output.writestr("src/app.py", "two")

            with self.assertRaises(UnsafeArchiveError):
                InputIngestor().ingest(archive, root / "task")

    def test_file_count_and_size_budgets_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "large.zip"
            with zipfile.ZipFile(str(archive), "w") as output:
                output.writestr("one.txt", "12345")
                output.writestr("two.txt", "12")

            with self.assertRaises(ArchiveBudgetExceededError):
                InputIngestor(
                    IngestionLimits(max_files=1, max_file_bytes=10, max_total_bytes=20)
                ).ingest(archive, root / "task-files")

            with self.assertRaises(ArchiveBudgetExceededError):
                InputIngestor(
                    IngestionLimits(max_files=10, max_file_bytes=4, max_total_bytes=20)
                ).ingest(archive, root / "task-size")
