import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from software_copyright_agent.source_document import SourceDocumentBuilder
from software_copyright_agent.source_document_qa import (
    LibreOfficeRenderer,
    RenderResult,
    SourceDocumentQaInspector,
    SourceDocumentQaError,
)


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
    def test_renderer_retry_replaces_stale_pages_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "source.docx"
            document.write_bytes(b"fixture")
            output = root / "render"
            output.mkdir()
            (output / "source.pdf").write_bytes(b"previous PDF")
            (output / "page-01.png").write_bytes(b"previous first page")
            (output / "page-60.png").write_bytes(b"stale last page")

            def process(args, **_kwargs):
                if "--outdir" in args:
                    folder = Path(args[args.index("--outdir") + 1])
                    (folder / "source.pdf").write_bytes(b"current PDF")
                else:
                    Path(str(args[-1]) + "-1.png").write_bytes(b"current first page")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(LibreOfficeRenderer, "_resolve_tools", return_value=("soffice", "pdftoppm")), \
                    patch("software_copyright_agent.source_document_qa.subprocess.run", side_effect=process):
                result = LibreOfficeRenderer().render(document, output)
            self.assertEqual([path.name for path in result.page_paths], ["page-1.png"])
            self.assertEqual(result.pdf_path.read_bytes(), b"current PDF")
            self.assertEqual(result.page_paths[0].read_bytes(), b"current first page")
            self.assertFalse((output / "page-60.png").exists())

    def test_failed_rasterization_keeps_previous_render_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "source.docx"
            document.write_bytes(b"fixture")
            output = root / "render"
            output.mkdir()
            (output / "source.pdf").write_bytes(b"previous PDF")
            (output / "page-1.png").write_bytes(b"previous first page")

            def process(args, **_kwargs):
                if "--outdir" in args:
                    (Path(args[args.index("--outdir") + 1]) / "source.pdf").write_bytes(b"current PDF")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                Path(str(args[-1]) + "-1.png").write_bytes(b"incomplete new page")
                return SimpleNamespace(returncode=1, stdout="", stderr="interrupted")

            with patch.object(LibreOfficeRenderer, "_resolve_tools", return_value=("soffice", "pdftoppm")), \
                    patch("software_copyright_agent.source_document_qa.subprocess.run", side_effect=process):
                with self.assertRaises(SourceDocumentQaError):
                    LibreOfficeRenderer().render(document, output)
            self.assertEqual((output / "source.pdf").read_bytes(), b"previous PDF")
            self.assertEqual((output / "page-1.png").read_bytes(), b"previous first page")

    def test_renderer_capability_reports_missing_components_for_the_ui(self) -> None:
        with patch.object(LibreOfficeRenderer, "_resolve_tools", return_value=(None, None)):
            capability = LibreOfficeRenderer.capability()
        self.assertFalse(capability["available"])
        self.assertEqual(capability["missing"], ["LibreOffice", "PDF 逐页渲染组件"])
        self.assertIn("暂不能生成真实 Word 预览", capability["message"])

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
