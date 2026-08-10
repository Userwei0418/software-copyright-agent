import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from software_copyright_agent.source_document import SourceDocumentBuilder
from software_copyright_agent.source_document_qa import RenderResult, SourceDocumentQaInspector


def pages() -> list:
    return [
        {
            "page_number": page,
            "line_count": 50,
            "entries": [
                {"kind": "code", "text": "value = {0}".format(line)}
                for line in range(50)
            ],
        }
        for page in range(1, 60)
    ]


class SourceDocumentQaInspectorTests(unittest.TestCase):
    def test_complete_document_passes_automatic_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "source.docx"
            SourceDocumentBuilder().build(document, "Demo", "V1.0", pages())
            page_paths = []
            for page in range(1, 61):
                path = root / "page-{0}.png".format(page)
                image = Image.new("RGB", (700, 990), "white")
                draw = ImageDraw.Draw(image)
                draw.text((30, 30), "page {0}".format(page), fill="black")
                if page > 1:
                    draw.text((30, 890), "last source line", fill="black")
                image.save(path)
                page_paths.append(path)
            pdf = root / "source.pdf"
            pdf.write_bytes(b"%PDF-fake")
            digest = hashlib.sha256(document.read_bytes()).hexdigest()

            result = SourceDocumentQaInspector().inspect(
                document, digest, RenderResult(pdf, tuple(page_paths))
            )

            self.assertTrue(result.passed)
            self.assertEqual(result.summary["rendered_pages"], 60)
            self.assertEqual(result.summary["blank_pages"], [])

    def test_blank_page_blocks_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "source.docx"
            SourceDocumentBuilder().build(document, "Demo", "V1.0", pages())
            page_paths = []
            for page in range(1, 61):
                path = root / "page-{0}.png".format(page)
                image = Image.new("RGB", (700, 990), "white")
                if page != 17:
                    draw = ImageDraw.Draw(image)
                    draw.text((30, 30), "content", fill="black")
                    if page > 1:
                        draw.text((30, 890), "last source line", fill="black")
                image.save(path)
                page_paths.append(path)
            pdf = root / "source.pdf"
            pdf.write_bytes(b"%PDF-fake")
            digest = hashlib.sha256(document.read_bytes()).hexdigest()

            result = SourceDocumentQaInspector().inspect(
                document, digest, RenderResult(pdf, tuple(page_paths))
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.summary["blank_pages"], [17])

    def test_underfilled_source_page_blocks_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "source.docx"
            SourceDocumentBuilder().build(document, "Demo", "V1.0", pages())
            page_paths = []
            for page in range(1, 61):
                path = root / "page-{0}.png".format(page)
                image = Image.new("RGB", (700, 990), "white")
                draw = ImageDraw.Draw(image)
                draw.text((30, 30), "content", fill="black")
                if page > 1 and page != 17:
                    draw.text((30, 890), "last source line", fill="black")
                image.save(path)
                page_paths.append(path)
            pdf = root / "source.pdf"
            pdf.write_bytes(b"%PDF-fake")
            digest = hashlib.sha256(document.read_bytes()).hexdigest()

            result = SourceDocumentQaInspector().inspect(
                document, digest, RenderResult(pdf, tuple(page_paths))
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.summary["underfilled_pages"], [17])
