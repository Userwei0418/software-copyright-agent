import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List

from PIL import Image, ImageChops

from .font_assets import FontAsset


QA_POLICY_VERSION = "source-docx-qa-v3"
MINIMUM_BODY_FILL_RATIO = 0.86


class SourceDocumentQaError(RuntimeError):
    pass


@dataclass(frozen=True)
class QaCheck:
    key: str
    passed: bool
    expected: object
    actual: object
    message: str


@dataclass(frozen=True)
class RenderResult:
    pdf_path: Path
    page_paths: tuple


@dataclass(frozen=True)
class QaResult:
    passed: bool
    checks: tuple
    summary: dict
    render: RenderResult


class LibreOfficeRenderer:
    def __init__(self, timeout_seconds: int = 120, dpi: int = 100,
                 cjk_font: FontAsset = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.dpi = dpi
        self.cjk_font = cjk_font or FontAsset.bundled_cjk()

    def render(self, document_path: Path, output_dir: Path) -> RenderResult:
        configured_soffice = os.environ.get("COPYRIGHT_AGENT_SOFFICE")
        soffice = configured_soffice or shutil.which("soffice")
        pdftoppm = shutil.which("pdftoppm")
        if not soffice or not Path(soffice).is_file() or not pdftoppm:
            raise SourceDocumentQaError("LibreOffice and pdftoppm are required for QA")
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_base = "/private/tmp" if Path("/private/tmp").is_dir() else None
        with tempfile.TemporaryDirectory(prefix="docx-qa-profile-", dir=temp_base) as profile, \
                tempfile.TemporaryDirectory(prefix="docx-qa-convert-", dir=temp_base) as convert_dir:
            profile_uri = Path(profile).resolve().as_uri()
            environment = os.environ.copy()
            environment["HOME"] = profile
            environment["XDG_CONFIG_HOME"] = str(Path(profile) / "xdg_config")
            environment["XDG_CACHE_HOME"] = str(Path(profile) / "xdg_cache")
            Path(environment["XDG_CONFIG_HOME"]).mkdir()
            Path(environment["XDG_CACHE_HOME"]).mkdir()
            user_fonts = Path(profile) / "Library" / "Fonts"
            user_fonts.mkdir(parents=True)
            shutil.copyfile(self.cjk_font.path, user_fonts / self.cjk_font.path.name)
            stable_temp = "/private/tmp" if Path("/private/tmp").is_dir() else profile
            environment["TMPDIR"] = stable_temp
            environment["TEMP"] = stable_temp
            environment["TMP"] = stable_temp
            environment["SAL_FONTPATH"] = os.pathsep.join(
                [str(self.cjk_font.path.parent), str(user_fonts)]
            )
            converted = subprocess.run(
                [soffice, "-env:UserInstallation={0}".format(profile_uri),
                 "--invisible", "--headless", "--norestore", "--convert-to", "pdf",
                 "--outdir", convert_dir, str(document_path)],
                capture_output=True, text=True, timeout=self.timeout_seconds,
                env=environment, check=False,
            )
            converted_pdf = Path(convert_dir) / (document_path.stem + ".pdf")
            if not converted_pdf.is_file():
                candidates = sorted(Path(convert_dir).glob("*.pdf"))
                converted_pdf = candidates[0] if candidates else converted_pdf
            if not converted_pdf.is_file() or converted_pdf.stat().st_size == 0:
                raise SourceDocumentQaError(
                    "LibreOffice conversion failed (exit {0}): {1}".format(
                        converted.returncode,
                        (converted.stderr or converted.stdout or "no diagnostic output").strip()[-500:]
                    )
                )
            pdf_path = output_dir / (document_path.stem + ".pdf")
            shutil.copyfile(converted_pdf, pdf_path)
            prefix = output_dir / "page"
            rasterized = subprocess.run(
                [pdftoppm, "-png", "-r", str(self.dpi), str(pdf_path), str(prefix)],
                capture_output=True, text=True, timeout=self.timeout_seconds,
                check=False,
            )
            if rasterized.returncode != 0:
                raise SourceDocumentQaError(
                    "PDF rasterization failed: {0}".format(rasterized.stderr.strip()[-500:])
                )
        pages = sorted(
            output_dir.glob("page-*.png"),
            key=lambda item: int(item.stem.rsplit("-", 1)[1]),
        )
        if not pages:
            raise SourceDocumentQaError("Renderer produced no page images")
        return RenderResult(pdf_path, tuple(pages))


class SourceDocumentQaInspector:
    def __init__(self, expected_pages: int = 60, cjk_font: FontAsset = None) -> None:
        self.expected_pages = expected_pages
        self.cjk_font = cjk_font or FontAsset.bundled_cjk()

    def inspect(self, document_path: Path, expected_sha256: str, render: RenderResult) -> QaResult:
        checks: List[QaCheck] = []
        actual_sha = hashlib.sha256(document_path.read_bytes()).hexdigest()
        checks.append(self._check("artifact.sha256", expected_sha256, actual_sha))
        with zipfile.ZipFile(document_path) as archive:
            document_bytes = archive.read("word/document.xml")
            document_xml = document_bytes.decode("utf-8")
            footer_xml = "\n".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist() if name.startswith("word/footer")
            )
            header_count = sum(
                1 for name in archive.namelist() if name.startswith("word/header")
            )
            embedded_fonts = [name for name in archive.namelist() if name.endswith(".odttf")]
            font_table_xml = archive.read("word/fontTable.xml").decode("utf-8")
            settings_xml = archive.read("word/settings.xml").decode("utf-8")
            content_types_xml = archive.read("[Content_Types].xml").decode("utf-8")
        checks.append(self._check("structure.sections", 2, document_xml.count("<w:sectPr")))
        checks.append(self._check("structure.page_breaks", 58, document_xml.count('w:type="page"')))
        checks.append(self._check("structure.a4_sections", 2, self._a4_section_count(document_xml)))
        checks.append(self._check("structure.header_parts", 1, header_count))
        checks.append(self._check("structure.page_field", True, "PAGE" in footer_xml))
        checks.append(self._check("structure.numpages_field", True, "NUMPAGES" in footer_xml))
        checks.append(self._check("font.embedded_parts", 1, len(embedded_fonts)))
        checks.append(self._check("font.embed_regular", True, "embedRegular" in font_table_xml))
        checks.append(self._check("font.embed_setting", True, "embedTrueTypeFonts" in settings_xml))
        checks.append(self._check("font.content_type", True, "obfuscatedFont" in content_types_xml))
        root = ET.fromstring(document_bytes)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
        document_text = "".join(node.text or "" for node in root.iter(namespace))
        font_summary = self.cjk_font.validate(document_text)
        checks.append(self._check("font.sha256", self.cjk_font.expected_sha256, font_summary["sha256"]))
        checks.append(self._check("font.missing_codepoints", 0, font_summary["missing_codepoints"]))
        checks.append(self._check("render.page_count", self.expected_pages, len(render.page_paths)))

        dimensions = []
        blank_pages = []
        body_fill_ratios = []
        underfilled_pages = []
        for index, page_path in enumerate(render.page_paths, start=1):
            with Image.open(page_path) as image:
                rgb = image.convert("RGB")
                dimensions.append(rgb.size)
                background = Image.new("RGB", rgb.size, "white")
                difference = ImageChops.difference(rgb, background).convert("L")
                bbox = difference.point(lambda value: 255 if value > 12 else 0).getbbox()
                if bbox is None:
                    blank_pages.append(index)
                if index > 1:
                    body_top = int(rgb.height * 0.06)
                    body_bottom = int(rgb.height * 0.92)
                    body = difference.crop((0, body_top, rgb.width, body_bottom))
                    body_bbox = body.point(lambda value: 255 if value > 12 else 0).getbbox()
                    fill_ratio = 0.0 if body_bbox is None else (body_top + body_bbox[3]) / rgb.height
                    body_fill_ratios.append(fill_ratio)
                    if fill_ratio < MINIMUM_BODY_FILL_RATIO:
                        underfilled_pages.append(index)
        consistent_dimensions = len(set(dimensions)) == 1
        checks.append(self._check("render.consistent_dimensions", True, consistent_dimensions))
        a4_ratio = all(abs((width / height) - (210 / 297)) < 0.01 for width, height in dimensions)
        checks.append(self._check("render.a4_ratio", True, a4_ratio))
        checks.append(self._check("render.blank_pages", [], blank_pages))
        minimum_fill = min(body_fill_ratios) if body_fill_ratios else 0.0
        checks.append(self._minimum_check(
            "render.minimum_body_fill_ratio", MINIMUM_BODY_FILL_RATIO, minimum_fill
        ))
        passed = all(check.passed for check in checks)
        summary = {
            "passed": passed,
            "check_count": len(checks),
            "failed_check_count": sum(1 for check in checks if not check.passed),
            "rendered_pages": len(render.page_paths),
            "blank_pages": blank_pages,
            "minimum_body_fill_ratio": round(minimum_fill, 4),
            "underfilled_pages": underfilled_pages,
            "page_dimensions": list(dimensions[0]) if dimensions else None,
            "document_sha256": actual_sha,
            "visual_review_required": True,
            "cjk_font": font_summary,
        }
        return QaResult(passed, tuple(checks), summary, render)

    @staticmethod
    def _a4_section_count(xml: str) -> int:
        return len(re.findall(r'<w:pgSz[^>]*w:w="1190[56]"[^>]*w:h="1683[78]"', xml))

    @staticmethod
    def _check(key: str, expected: object, actual: object) -> QaCheck:
        passed = expected == actual
        return QaCheck(
            key, passed, expected, actual,
            "passed" if passed else "Expected {0!r}, got {1!r}".format(expected, actual),
        )

    @staticmethod
    def _minimum_check(key: str, minimum: float, actual: float) -> QaCheck:
        passed = actual >= minimum
        return QaCheck(
            key, passed, ">= {0:.2f}".format(minimum), round(actual, 4),
            "passed" if passed else "Expected >= {0:.2f}, got {1:.4f}".format(minimum, actual),
        )


def write_qa_report(path: Path, task_id: str, document_run_id: str,
                    qa_version: int, result: QaResult) -> None:
    payload = {
        "schema_version": 1,
        "policy_version": QA_POLICY_VERSION,
        "task_id": task_id,
        "source_document_run_id": document_run_id,
        "qa_version": qa_version,
        "summary": result.summary,
        "checks": [check.__dict__ for check in result.checks],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")
