import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.code_preview import (
    CodeInputFile,
    CodePreviewBuilder,
    CodePreviewConfig,
    SourceChangedError,
    hard_wrap_visual,
    visual_width,
)
from software_copyright_agent.code_preview_service import CodePreviewService
from software_copyright_agent.service import ScanProjectService
from software_copyright_agent.source_plan_service import SourcePlanService
from software_copyright_agent.storage import Database


class VisualWrappingTests(unittest.TestCase):
    def test_chinese_characters_use_double_visual_width(self) -> None:
        segments = hard_wrap_visual("ab中文c", 4)
        self.assertEqual(segments, ["ab中", "文c"])
        self.assertTrue(all(visual_width(segment) <= 4 for segment in segments))

    def test_builder_expands_tabs_and_paginates_without_padding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.py"
            source.write_text("\tvalue = 'abcdefghijkl'\nsecond\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            builder = CodePreviewBuilder(
                CodePreviewConfig(
                    max_visual_width=10,
                    lines_per_page=2,
                    target_code_pages=3,
                    tab_size=4,
                )
            )

            preview = builder.build(
                root,
                [CodeInputFile("main.py", "A", 90, "Python", digest)],
            )

            self.assertFalse(preview.sufficient)
            self.assertEqual(preview.required_visual_lines, 6)
            self.assertEqual(preview.generated_pages, 3)
            self.assertLess(preview.used_visual_lines, 6)
            code_entries = [
                entry
                for page in preview.pages
                for entry in page["entries"]
                if entry["kind"] == "code"
            ]
            self.assertTrue(any(entry["continuation"] for entry in code_entries))
            self.assertTrue(all(entry["visual_width"] <= 10 for entry in code_entries))

    def test_changed_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.py"
            source.write_text("before\n", encoding="utf-8")
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            source.write_text("after\n", encoding="utf-8")

            with self.assertRaises(SourceChangedError):
                CodePreviewBuilder().build(
                    root, [CodeInputFile("main.py", "A", 90, "Python", expected)]
                )

    def test_builder_rotates_backend_frontend_and_domain_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = (
                "demo-backend/src/controller/First.java",
                "demo-backend/src/controller/Second.java",
                "demo-frontend/src/views/Home.vue",
                "demo-backend/src/service/OrderService.java",
                "demo-backend/src/entity/Order.java",
            )
            files = []
            for index, relative in enumerate(paths):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("line one\nline two\nline three\n", encoding="utf-8")
                files.append(CodeInputFile(
                    relative, "A", 100 - index, path.suffix.lstrip("."),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ))
            preview = CodePreviewBuilder(CodePreviewConfig(
                max_visual_width=90, lines_per_page=4, target_code_pages=6,
            )).build(root, files)
            headers = [
                entry["path"] for page in preview.pages for entry in page["entries"]
                if entry["kind"] == "file_header"
            ]
            self.assertEqual(headers[:5], [paths[0], paths[2], paths[3], paths[1], paths[4]])
            self.assertIn("backend_controller", preview.included_buckets)
            self.assertIn("frontend_view", preview.included_buckets)
            self.assertIn("backend_domain", preview.included_buckets)

    def test_large_file_excerpts_do_not_monopolize_the_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = []
            paths = (
                "demo-backend/src/controller/LargeController.java",
                "demo-frontend/src/views/Home.vue",
                "demo-backend/src/service/OrderService.java",
            )
            for index, relative in enumerate(paths):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "\n".join("line {0}".format(line) for line in range(20)),
                    encoding="utf-8",
                )
                files.append(CodeInputFile(
                    relative, "A", 100 - index, path.suffix.lstrip("."),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ))
            preview = CodePreviewBuilder(CodePreviewConfig(
                max_visual_width=90, lines_per_page=5, target_code_pages=8,
                preferred_excerpt_lines_per_file=10,
            )).build(root, files)
            paths_by_page = [
                {entry.get("path") for entry in page["entries"] if entry.get("path")}
                for page in preview.pages
            ]
            self.assertTrue(all(
                not (paths_by_page[index] == paths_by_page[index + 1] == paths_by_page[index + 2])
                for index in range(len(paths_by_page) - 2)
            ))
            self.assertEqual(preview.included_files, 3)

    def test_executable_layers_receive_more_page_budget_than_declarations(self) -> None:
        paths = [
            *(CodeInputFile(f"app-backend/controller/C{i}.java", "A", 90, "Java", "")
              for i in range(6)),
            *(CodeInputFile(f"app-backend/service/S{i}.java", "A", 90, "Java", "")
              for i in range(6)),
            *(CodeInputFile(f"app-frontend/pages/P{i}.vue", "B", 60, "Vue", "")
              for i in range(6)),
            *(CodeInputFile(f"app-backend/dto/D{i}.java", "B", 55, "Java", "")
              for i in range(6)),
        ]

        ordered = CodePreviewBuilder._balanced_files(paths)
        first_twenty = ordered[:20]
        executable = sum(
            CodePreviewBuilder._source_bucket(item.relative_path) in {
                "backend_controller", "backend_service", "frontend_view"
            }
            for item in first_twenty
        )
        declarations = sum(
            CodePreviewBuilder._source_bucket(item.relative_path) == "backend_domain"
            for item in first_twenty
        )

        self.assertGreaterEqual(executable, 15)
        self.assertLessEqual(declarations, 2)

    def test_java_excerpt_starts_at_implementation_instead_of_import_block(self) -> None:
        lines = [
            "package demo;",
            "",
            "import java.util.List;",
            "import java.util.Map;",
            "",
            "/** Project-specific behavior. */",
            "@Service",
            "public class OrderService {",
            "    public void submit() {}",
            "}",
        ]

        start_line = CodePreviewBuilder._preferred_start_line(
            "backend/service/OrderService.java", lines
        )

        self.assertEqual(start_line, 6)


class CodePreviewServiceTests(unittest.TestCase):
    def test_preview_is_versioned_and_short_source_completes_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            feature = project / "src" / "features"
            feature.mkdir(parents=True)
            (project / "package.json").write_text(
                '{"name":"preview-demo","version":"1.0.0"}', encoding="utf-8"
            )
            (feature / "orders.py").write_text(
                "def create_order():\n    return True\n", encoding="utf-8"
            )
            data_root = base / "data"
            database = Database(data_root / "app.db")
            task = ScanProjectService(database, data_root).execute(project)
            SourcePlanService(database, data_root).execute(task.task_id)
            service = CodePreviewService(database, data_root)

            first = service.execute(task.task_id)
            second = service.execute(task.task_id)

            self.assertEqual(first.version, 1)
            self.assertEqual(second.version, 2)
            self.assertFalse(first.preview.sufficient)
            self.assertTrue(first.artifact_path.is_file())

            connection = sqlite3.connect(str(database.path))
            try:
                task_row = connection.execute(
                    "SELECT status, current_stage_key, row_version FROM tasks WHERE id = ?",
                    (task.task_id,),
                ).fetchone()
                runs = connection.execute(
                    "SELECT COUNT(*) FROM code_preview_runs WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()[0]
                attempts = connection.execute(
                    """SELECT attempt FROM task_stages
                    WHERE task_id = ? AND stage_key = '06_prepare_source_doc'
                    ORDER BY attempt""",
                    (task.task_id,),
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(task_row, ("completed_with_warnings", "06_prepare_source_doc", 9))
            self.assertEqual(runs, 2)
            self.assertEqual(attempts, [(1,), (2,)])
