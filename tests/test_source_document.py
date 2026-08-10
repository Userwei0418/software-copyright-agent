import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document

from software_copyright_agent.source_document import (
    SourceDocumentBuilder,
    SourceDocumentError,
    SourceDocumentTemplate,
)


def make_pages(count: int, lines: int) -> list:
    return [
        {
            "page_number": page,
            "line_count": lines,
            "entries": [
                {
                    "kind": "file_header" if line == 0 else "code",
                    "text": "FILE: src/main.py" if line == 0 else "print({0})".format(line),
                }
                for line in range(lines)
            ],
        }
        for page in range(1, count + 1)
    ]


class SourceDocumentBuilderTests(unittest.TestCase):
    def test_builds_cover_and_fixed_code_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "source.docx"
            builder = SourceDocumentBuilder(
                SourceDocumentTemplate(code_pages=2, lines_per_page=3)
            )

            summary = builder.build(output, "示例软件", "V1.0", make_pages(2, 3))

            self.assertTrue(output.is_file())
            self.assertEqual(summary["total_pages_expected"], 3)
            document = Document(str(output))
            self.assertIn("示例软件", "\n".join(p.text for p in document.paragraphs))
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
                names = archive.namelist()
                font_table = archive.read("word/fontTable.xml").decode("utf-8")
            self.assertEqual(xml.count('w:type="page"'), 1)
            self.assertEqual(xml.count("<w:sectPr"), 2)
            self.assertEqual(sum(name.endswith(".odttf") for name in names), 1)
            self.assertIn("embedRegular", font_table)

    def test_rejects_incomplete_preview(self) -> None:
        builder = SourceDocumentBuilder(
            SourceDocumentTemplate(code_pages=2, lines_per_page=3)
        )
        with self.assertRaises(SourceDocumentError):
            builder.build(Path("unused.docx"), "Demo", "V1", make_pages(1, 3))
