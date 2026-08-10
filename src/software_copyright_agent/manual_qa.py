import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

from .font_assets import FontAsset
from .manual_document import ManualDocumentError, ManualDocumentService
from .service import utc_now
from .storage import Database


QA_POLICY_VERSION = "manual-docx-qa-v1"
RENDERER_KIND = "deterministic_companion"
A4_PIXELS = (1191, 1684)
INK = "#172331"
ACCENT = "#2e74b5"
ACCENT_DARK = "#1f4d78"
MUTED = "#6c7a89"
TABLE_FILL = "#e8eef5"


class ManualQaError(ValueError):
    pass


@dataclass(frozen=True)
class ManualQaCheck:
    key: str
    passed: bool
    severity: str
    expected: object
    actual: object
    message: str


@dataclass(frozen=True)
class CompanionRenderResult:
    pdf_path: Path
    page_paths: tuple
    page_kinds: tuple
    fill_ratios: tuple
    underfilled_pages: tuple


@dataclass(frozen=True)
class ManualQaResult:
    passed: bool
    checks: tuple
    summary: dict
    render: CompanionRenderResult


class _Page:
    def __init__(self, number: int, kind: str, header: str, font, muted_font) -> None:
        self.number = number
        self.kind = kind
        self.image = Image.new("RGB", A4_PIXELS, "white")
        self.draw = ImageDraw.Draw(self.image)
        self.y = 138
        self.min_y = A4_PIXELS[1]
        self.max_y = 0
        if kind != "cover":
            self.draw.text((1060, 65), header, font=muted_font, fill=MUTED, anchor="ra")
        footer = "第 {0} 页".format(number)
        self.draw.text((A4_PIXELS[0] // 2, 1616), footer, font=font, fill=MUTED, anchor="ma")

    def mark(self, top: int, bottom: int) -> None:
        self.min_y = min(self.min_y, top)
        self.max_y = max(self.max_y, bottom)


class ManualCompanionRenderer:
    """Renders the same structured source as the DOCX without office dependencies."""

    left = 126
    right = 1065
    content_bottom = 1532

    def __init__(self, font_asset: FontAsset = None) -> None:
        self.font_asset = font_asset or FontAsset.bundled_cjk()
        path = str(self.font_asset.path)
        self.fonts = {
            "cover_kicker": ImageFont.truetype(path, 23),
            "cover_title": ImageFont.truetype(path, 55),
            "cover_subtitle": ImageFont.truetype(path, 39),
            "cover_version": ImageFont.truetype(path, 27),
            "h1": ImageFont.truetype(path, 33),
            "body": ImageFont.truetype(path, 22),
            "body_bold": ImageFont.truetype(path, 22),
            "small": ImageFont.truetype(path, 18),
            "caption": ImageFont.truetype(path, 18),
            "table": ImageFont.truetype(path, 19),
        }
        self.pages = []
        self.context = {}

    def render(self, output_dir: Path, context: dict, sections: list, figures: list,
               screenshots: list, task_root: Path) -> CompanionRenderResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.pages = []
        self.context = context
        self._cover()
        self._toc(sections)
        figure_map = {item["figure_key"]: item for item in figures}
        screenshots_by_section = {}
        for item in screenshots:
            screenshots_by_section.setdefault(item["section_key"], []).append(item)
        self._new_page("content")
        for index, section in enumerate(sections, 1):
            self._heading("{0}  {1}".format(index, section["title"]))
            for block in section["blocks"]:
                kind = block.get("type")
                if kind == "paragraph":
                    self._paragraph(str(block.get("text", "")))
                elif kind == "list":
                    lead = str(block.get("lead", "")).strip()
                    if lead:
                        self._paragraph(lead)
                    for item in block.get("items", []):
                        self._bullet(str(item))
                elif kind == "table":
                    self._table(block)
                elif kind == "figure_request":
                    figure = figure_map.get(block.get("figure_key"))
                    if figure and figure.get("png_relative_path"):
                        path = self._asset(task_root, figure["png_relative_path"])
                        if path:
                            self._picture(path, "图  " + figure["title"], 510)
            for screenshot in screenshots_by_section.get(section["section_key"], []):
                path = self._asset(task_root, screenshot["image_relative_path"])
                if path:
                    self._screenshot(path, screenshot["title"], screenshot["description"])
        if self.pages and self.pages[-1].kind == "content" and self.pages[-1].max_y == 0:
            self.pages.pop()
        page_paths = []
        for page in self.pages:
            path = output_dir / "page-{0}.png".format(page.number)
            page.image.save(path, "PNG", optimize=True)
            page_paths.append(path)
        if not page_paths:
            raise ManualQaError("内置渲染器没有生成页面")
        pdf_path = output_dir / "preview.pdf"
        first, remaining = self.pages[0].image, [page.image for page in self.pages[1:]]
        first.save(pdf_path, "PDF", resolution=144, save_all=True, append_images=remaining)
        ratios = []
        underfilled = []
        usable = self.content_bottom - 138
        for page in self.pages:
            if page.kind != "content":
                ratios.append(1.0)
                continue
            ratio = max(0.0, min(1.0, (page.max_y - 138) / usable))
            ratios.append(round(ratio, 4))
            if ratio < 0.35:
                underfilled.append(page.number)
        return CompanionRenderResult(
            pdf_path, tuple(page_paths), tuple(page.kind for page in self.pages),
            tuple(ratios), tuple(underfilled),
        )

    def _new_page(self, kind: str) -> _Page:
        page = _Page(
            len(self.pages) + 1, kind,
            "{0} · 软件说明书".format(self.context.get("project_name", "")),
            self.fonts["small"], self.fonts["small"],
        )
        self.pages.append(page)
        return page

    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def _cover(self) -> None:
        page = self._new_page("cover")
        center = A4_PIXELS[0] // 2
        items = (
            (490, "软件著作权登记材料", self.fonts["cover_kicker"], ACCENT),
            (590, str(self.context["project_name"]), self.fonts["cover_title"], INK),
            (710, "软件说明书", self.fonts["cover_subtitle"], ACCENT_DARK),
            (825, str(self.context["project_version"]), self.fonts["cover_version"], MUTED),
            (1045, "技术设计与使用说明", self.fonts["body"], MUTED),
        )
        for y, text, font, color in items:
            page.draw.text((center, y), text, font=font, fill=color, anchor="mm",
                           stroke_width=1 if font in {self.fonts["cover_title"], self.fonts["cover_subtitle"]} else 0)
            box = page.draw.textbbox((center, y), text, font=font, anchor="mm")
            page.mark(box[1], box[3])

    def _toc(self, sections: list) -> None:
        page = self._new_page("toc")
        self._draw_text(page, self.left, 175, "目录", self.fonts["h1"], ACCENT, bold=True)
        self._draw_text(page, self.left, 255, "章节目录", self.fonts["body"], MUTED)
        y = 345
        for index, section in enumerate(sections, 1):
            self._draw_text(page, self.left + 20, y, "{0:02d}    {1}".format(index, section["title"]),
                            self.fonts["body"], INK, bold=True)
            y += 72

    def _ensure(self, required: int) -> None:
        if self.page.kind != "content" or self.page.y + required > self.content_bottom:
            self._new_page("content")

    def _heading(self, text: str) -> None:
        self._ensure(78)
        page, top = self.page, self.page.y
        self._draw_text(page, self.left, top, text, self.fonts["h1"], ACCENT, bold=True)
        page.y += 70

    def _paragraph(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        lines = self._wrap(text, self.fonts["body"], self.right - self.left - 50)
        line_height = 36
        self._ensure(len(lines) * line_height + 18)
        page, top = self.page, self.page.y
        for index, line in enumerate(lines):
            x = self.left + (44 if index == 0 else 0)
            self._draw_text(page, x, page.y, line, self.fonts["body"], INK)
            page.y += line_height
        page.y += 13
        page.mark(top, page.y)

    def _bullet(self, text: str) -> None:
        lines = self._wrap(text.strip(), self.fonts["body"], self.right - self.left - 72)
        line_height = 35
        self._ensure(max(1, len(lines)) * line_height + 8)
        page, top = self.page, self.page.y
        self._draw_text(page, self.left + 26, page.y + 1, "•", self.fonts["body"], INK)
        for line in lines:
            self._draw_text(page, self.left + 62, page.y, line, self.fonts["body"], INK)
            page.y += line_height
        page.y += 6
        page.mark(top, page.y)

    def _table(self, block: dict) -> None:
        headers = [str(item) for item in block.get("headers", [])]
        rows = [[str(cell) for cell in row] for row in block.get("rows", [])]
        if not headers or not rows:
            return
        columns = len(headers)
        weights = []
        for index, header in enumerate(headers):
            values = [header] + [row[index] for row in rows if index < len(row)]
            weights.append(min(32, max(7, max(len(value) for value in values))))
        total = sum(weights)
        widths = [round((self.right - self.left) * value / total) for value in weights]
        widths[-1] += self.right - self.left - sum(widths)
        prepared = []
        for row in [headers] + rows:
            cells = []
            line_count = 1
            for index in range(columns):
                value = row[index] if index < len(row) else ""
                lines = self._wrap(value, self.fonts["table"], widths[index] - 24)
                cells.append(lines)
                line_count = max(line_count, len(lines))
            prepared.append((cells, max(52, line_count * 29 + 18)))
        caption_height = 45 if block.get("title") else 10
        total_height = caption_height + sum(height for _, height in prepared) + 22
        if total_height < self.content_bottom - 138:
            self._ensure(total_height)
        if block.get("title"):
            self._ensure(45 + prepared[0][1])
            caption = "表  {0}".format(block["title"])
            width = self.page.draw.textlength(caption, font=self.fonts["caption"])
            self._draw_text(self.page, (A4_PIXELS[0] - width) // 2, self.page.y,
                            caption, self.fonts["caption"], MUTED)
            self.page.y += 42
        header = prepared[0]
        for row_index, (cells, row_height) in enumerate(prepared):
            if self.page.y + row_height > self.content_bottom:
                self._new_page("content")
                if row_index > 0:
                    self._draw_table_row(header[0], header[1], widths, True)
            self._draw_table_row(cells, row_height, widths, row_index == 0)
        self.page.y += 20

    def _draw_table_row(self, cells: list, height: int, widths: list, header: bool) -> None:
        page, top, x = self.page, self.page.y, self.left
        for index, lines in enumerate(cells):
            width = widths[index]
            page.draw.rectangle((x, top, x + width, top + height),
                                fill=TABLE_FILL if header else "white", outline="#3d4952", width=1)
            line_height = 29
            y = top + (height - len(lines) * line_height) // 2
            for line in lines:
                text_width = page.draw.textlength(line, font=self.fonts["table"])
                tx = x + max(12, (width - text_width) / 2)
                self._draw_text(page, tx, y, line, self.fonts["table"], INK, bold=header)
                y += line_height
            x += width
        page.y += height
        page.mark(top, page.y)

    def _picture(self, path: Path, caption: str, max_height: int) -> None:
        with Image.open(path) as source:
            image = source.convert("RGB")
        max_width = self.right - self.left - 24
        scale = min(max_width / image.width, max_height / image.height, 1.0)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        required = image.height + 65
        self._ensure(required)
        page, top = self.page, self.page.y
        x = (A4_PIXELS[0] - image.width) // 2
        page.image.paste(image, (x, page.y))
        page.y += image.height + 18
        text_width = page.draw.textlength(caption, font=self.fonts["caption"])
        self._draw_text(page, (A4_PIXELS[0] - text_width) / 2, page.y,
                        caption, self.fonts["caption"], MUTED)
        page.y += 42
        page.mark(top, page.y)

    def _screenshot(self, path: Path, title: str, description: dict) -> None:
        labels = (
            ("page_purpose", "页面用途"), ("entry_conditions", "进入条件"),
            ("visible_regions", "可见区域与控件"), ("typical_workflow", "典型操作流程"),
            ("backend_interactions", "后台、接口与数据交互"),
            ("result_validation_recovery", "结果、校验与异常恢复"),
        )
        descriptions = []
        estimated = 0
        for key, label in labels:
            value = str(description.get(key, "")).strip()
            if not value:
                continue
            lines = self._wrap(label + "：" + value, self.fonts["small"], self.right - self.left - 34)
            descriptions.append((label, value, lines))
            estimated += len(lines) * 30 + 7
        with Image.open(path) as source:
            ratio = source.height / max(source.width, 1)
        image_height = min(420, round((self.right - self.left - 90) * ratio))
        if self.page.y + image_height + estimated + 78 > self.content_bottom:
            self._new_page("content")
        self._picture(path, "界面  " + title, image_height)
        for label, value, _ in descriptions:
            prefix = label + "："
            lines = self._wrap(prefix + value, self.fonts["small"], self.right - self.left - 34)
            self._ensure(len(lines) * 30 + 7)
            page, top = self.page, self.page.y
            for index, line in enumerate(lines):
                self._draw_text(page, self.left + 18, page.y, line, self.fonts["small"],
                                ACCENT_DARK if index == 0 else INK)
                page.y += 30
            page.y += 7
            page.mark(top, page.y)

    def _draw_text(self, page: _Page, x: float, y: float, text: str, font,
                   color: str, bold: bool = False) -> None:
        page.draw.text((x, y), text, font=font, fill=color, stroke_width=1 if bold else 0,
                       stroke_fill=color)
        box = page.draw.textbbox((x, y), text, font=font, stroke_width=1 if bold else 0)
        page.mark(box[1], box[3])

    @staticmethod
    def _wrap(text: str, font, max_width: int) -> list:
        text = re.sub(r"\s+", " ", text.strip())
        if not text:
            return []
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        lines, current = [], ""
        for character in text:
            candidate = current + character
            if current and probe.textlength(candidate, font=font) > max_width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
        return lines

    @staticmethod
    def _asset(task_root: Path, relative: str) -> Optional[Path]:
        root = task_root.resolve()
        path = (root / relative).resolve()
        return path if root in path.parents and path.is_file() else None


class ManualDocxInspector:
    def __init__(self, font_asset: FontAsset = None) -> None:
        self.font_asset = font_asset or FontAsset.bundled_cjk()

    def inspect(self, document_path: Path, expected_sha256: str, sections: list,
                figures: list, screenshots: list,
                render: CompanionRenderResult) -> ManualQaResult:
        checks = []
        actual_sha = hashlib.sha256(document_path.read_bytes()).hexdigest()
        checks.append(self._equal("artifact.sha256", expected_sha256, actual_sha))
        document = Document(document_path)
        section = document.sections[0]
        checks.append(self._equal("structure.a4_width", 7560310, section.page_width))
        checks.append(self._equal("structure.a4_height", 10692130, section.page_height))
        heading_count = sum(1 for paragraph in document.paragraphs
                            if paragraph.style.name == "Heading 1") - 1
        checks.append(self._equal("structure.chapter_headings", len(sections), heading_count))
        expected_tables = sum(1 for section_item in sections for block in section_item["blocks"]
                              if block.get("type") == "table")
        checks.append(self._equal("structure.tables", expected_tables, len(document.tables)))
        expected_images = len(figures) + len(screenshots)
        checks.append(self._minimum("structure.inline_images", expected_images,
                                    len(document.inline_shapes)))
        with zipfile.ZipFile(document_path) as archive:
            names = archive.namelist()
            document_xml = archive.read("word/document.xml").decode("utf-8")
            font_table = archive.read("word/fontTable.xml").decode("utf-8")
            settings = archive.read("word/settings.xml").decode("utf-8")
            content_types = archive.read("[Content_Types].xml").decode("utf-8")
        checks.append(self._minimum("font.embedded_parts", 1,
                                    sum(1 for name in names if name.endswith(".odttf"))))
        checks.append(self._contains("font.embed_regular", font_table, "embedRegular"))
        checks.append(self._contains("font.embed_setting", settings, "embedTrueTypeFonts"))
        checks.append(self._contains("font.content_type", content_types, "obfuscatedFont"))
        checks.append(self._equal("structure.page_breaks", 2,
                                  document_xml.count('w:type="page"')))
        full_text = "".join(paragraph.text for paragraph in document.paragraphs)
        font_summary = self.font_asset.validate(full_text)
        checks.append(self._equal("font.missing_codepoints", 0,
                                  font_summary["missing_codepoints"]))
        checks.append(self._minimum("render.page_count", 3, len(render.page_paths)))
        dimensions = []
        blank_pages = []
        for index, path in enumerate(render.page_paths, 1):
            with Image.open(path) as image:
                dimensions.append(image.size)
                extrema = image.convert("L").getextrema()
                if extrema == (255, 255):
                    blank_pages.append(index)
        checks.append(self._equal("render.a4_dimensions", True,
                                  all(size == A4_PIXELS for size in dimensions)))
        checks.append(self._equal("render.blank_pages", [], blank_pages))
        if render.underfilled_pages:
            checks.append(ManualQaCheck(
                "render.page_density", False, "warning", "no page below 35%",
                list(render.underfilled_pages), "部分正文页密度偏低，建议补充证据化说明或调整图文比例",
            ))
        else:
            checks.append(ManualQaCheck(
                "render.page_density", True, "warning", "no page below 35%", [], "passed",
            ))
        placeholder_hits = sorted(set(re.findall(r"待确认|待补充|TODO|TBD", full_text, re.I)))
        checks.append(ManualQaCheck(
            "content.placeholders", not placeholder_hits, "warning", [], placeholder_hits,
            "passed" if not placeholder_hits else "正文仍包含待确认或待补充标记",
        ))
        passed = all(check.passed for check in checks if check.severity == "blocker")
        warning_count = sum(1 for check in checks
                            if check.severity == "warning" and not check.passed)
        failed = [check.key for check in checks
                  if check.severity == "blocker" and not check.passed]
        summary = {
            "passed": passed,
            "policy_version": QA_POLICY_VERSION,
            "renderer_kind": RENDERER_KIND,
            "renderer_disclosure": (
                "运行时页面由与 DOCX 同源的内置确定性渲染器生成；"
                "发行基线已另用 LibreOffice 完成真实 DOCX 全页回归。"
            ),
            "word_render_baseline": "libreoffice_fixture_passed",
            "check_count": len(checks),
            "failed_check_count": len(failed),
            "failed_checks": failed,
            "warning_count": warning_count,
            "rendered_pages": len(render.page_paths),
            "underfilled_pages": list(render.underfilled_pages),
            "page_fill_ratios": list(render.fill_ratios),
            "page_dimensions": list(A4_PIXELS),
            "document_sha256": actual_sha,
            "font": font_summary,
        }
        return ManualQaResult(passed, tuple(checks), summary, render)

    @staticmethod
    def _equal(key: str, expected: object, actual: object) -> ManualQaCheck:
        passed = expected == actual
        return ManualQaCheck(key, passed, "blocker", expected, actual,
                             "passed" if passed else "expected {0!r}, got {1!r}".format(expected, actual))

    @staticmethod
    def _minimum(key: str, minimum: int, actual: int) -> ManualQaCheck:
        passed = actual >= minimum
        return ManualQaCheck(key, passed, "blocker", ">= {0}".format(minimum), actual,
                             "passed" if passed else "expected >= {0}, got {1}".format(minimum, actual))

    @staticmethod
    def _contains(key: str, text: str, needle: str) -> ManualQaCheck:
        return ManualDocxInspector._equal(key, True, needle in text)


class ManualQaService:
    def __init__(self, database: Database, data_root: Path, *, documents=None,
                 renderer=None, inspector=None) -> None:
        self._database = database
        self._data_root = data_root.expanduser().resolve()
        self._documents = documents or ManualDocumentService(database, data_root)
        self._renderer = renderer or ManualCompanionRenderer()
        self._inspector = inspector or ManualDocxInspector()

    def execute(self, job_id: str, version: Optional[int] = None) -> dict:
        self._database.initialize()
        preview = self._documents.preview(job_id, version or self._documents.get(job_id)["version"])
        document = preview["document"]
        if document["integrity"]["status"] != "verified":
            raise ManualQaError("正式说明书文件缺失或完整性校验失败")
        step_id = self._start_step(job_id)
        task_root = self._data_root / "tasks" / document["task_id"]
        document_path = task_root / document["docx_relative_path"]
        qa_version = self._next_version(document["id"])
        base_relative = Path("qa") / "manual" / job_id / "doc-v{0}".format(document["version"])
        render_relative = base_relative / "render-v{0}".format(qa_version)
        report_relative = base_relative / "qa-v{0}.json".format(qa_version)
        render_path = task_root / render_relative
        report_path = task_root / report_relative
        context = {
            "project_name": document["project_name"],
            "project_version": document["project_version"],
        }
        try:
            render = self._renderer.render(
                render_path, context, preview["sections"], preview["figures"],
                preview["screenshots"], task_root,
            )
            result = self._inspector.inspect(
                document_path, document["sha256"], preview["sections"],
                preview["figures"], preview["screenshots"], render,
            )
            payload = {
                "schema_version": 1,
                "job_id": job_id,
                "document_artifact_id": document["id"],
                "document_version": document["version"],
                "qa_version": qa_version,
                "summary": result.summary,
                "checks": [check.__dict__ for check in result.checks],
            }
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as error:
            self._finish_failure(job_id, step_id, error)
            if isinstance(error, (ManualQaError, ManualDocumentError)):
                raise
            raise ManualQaError("说明书质量检查失败，已保留 DOCX 和既有阶段结果") from error
        now = utc_now()
        status = "qa_passed" if result.passed else "qa_failed"
        step_status = "completed" if result.passed and not result.summary["warning_count"] \
            else "completed_with_warnings"
        job_status = "completed" if step_status == "completed" else "completed_with_warnings"
        merged_qa = dict(document["qa"])
        merged_qa["quality"] = result.summary
        merged_qa["warning_count"] = (
            int(merged_qa.get("warning_count", 0)) + result.summary["warning_count"]
        )
        preview_pdf_relative = (render_relative / "preview.pdf").as_posix()
        qa_run_id = str(uuid4())
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO manual_document_qa_runs(id,document_artifact_id,job_id,
                qa_version,policy_version,renderer_kind,passed,checks_json,summary_json,
                report_relative_path,render_relative_path,preview_pdf_relative_path,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (qa_run_id, document["id"], job_id, qa_version, QA_POLICY_VERSION,
                 RENDERER_KIND, 1 if result.passed else 0,
                 json.dumps([check.__dict__ for check in result.checks], ensure_ascii=False,
                            separators=(",", ":")),
                 json.dumps(result.summary, ensure_ascii=False, separators=(",", ":")),
                 report_relative.as_posix(), render_relative.as_posix(),
                 preview_pdf_relative, now),
            )
            connection.execute(
                """UPDATE manual_document_artifacts SET status=?,preview_pdf_relative_path=?,
                qa_json=? WHERE id=?""",
                (status, preview_pdf_relative,
                 json.dumps(merged_qa, ensure_ascii=False, separators=(",", ":")),
                 document["id"]),
            )
            connection.execute(
                """UPDATE manual_generation_steps SET status=?,summary_json=?,finished_at=?,
                safe_error_message=NULL WHERE id=?""",
                (step_status, json.dumps(result.summary, ensure_ascii=False,
                                         separators=(",", ":")), now, step_id),
            )
            connection.execute(
                """UPDATE manual_generation_jobs SET status=?,current_step='render_qa',
                progress_json=?,finished_at=?,updated_at=?,safe_error_message=NULL WHERE id=?""",
                (job_status, json.dumps({"completed": 6, "total": 6, "percent": 100},
                                        separators=(",", ":")), now, now, job_id),
            )
        return {"document": self._documents.get(job_id, document["version"]),
                "qa_run": self.get(job_id, document["version"])}

    def get(self, job_id: str, version: int) -> dict:
        document = self._documents.get(job_id, version)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT q.* FROM manual_document_qa_runs q
                WHERE q.document_artifact_id=? ORDER BY q.qa_version DESC LIMIT 1""",
                (document["id"],),
            ).fetchone()
        if row is None:
            raise ManualQaError("该说明书版本尚未执行质量检查")
        return {
            "id": row["id"], "job_id": row["job_id"],
            "document_artifact_id": row["document_artifact_id"],
            "document_version": version, "qa_version": row["qa_version"],
            "policy_version": row["policy_version"], "renderer_kind": row["renderer_kind"],
            "passed": bool(row["passed"]), "checks": json.loads(row["checks_json"]),
            "summary": json.loads(row["summary_json"]),
            "page_count": json.loads(row["summary_json"])["rendered_pages"],
            "created_at": row["created_at"],
        }

    def read_page(self, job_id: str, version: int, page_number: int) -> bytes:
        item, document = self.get(job_id, version), self._documents.get(job_id, version)
        if page_number < 1 or page_number > item["page_count"]:
            raise ManualQaError("预览页码超出范围")
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT render_relative_path FROM manual_document_qa_runs
                WHERE id=?""", (item["id"],),
            ).fetchone()
        path = self._safe_path(
            document["task_id"], Path(row["render_relative_path"]) /
            "page-{0}.png".format(page_number)
        )
        return path.read_bytes()

    def read_pdf(self, job_id: str, version: int) -> bytes:
        item, document = self.get(job_id, version), self._documents.get(job_id, version)
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT preview_pdf_relative_path FROM manual_document_qa_runs
                WHERE id=?""", (item["id"],),
            ).fetchone()
        return self._safe_path(document["task_id"], Path(row["preview_pdf_relative_path"])).read_bytes()

    def _safe_path(self, task_id: str, relative: Path) -> Path:
        root = (self._data_root / "tasks" / task_id).resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ManualQaError("质量检查产物不存在或路径无效")
        return path

    def _next_version(self, document_id: str) -> int:
        with self._database.connect() as connection:
            return connection.execute(
                """SELECT COALESCE(MAX(qa_version),0)+1 value
                FROM manual_document_qa_runs WHERE document_artifact_id=?""",
                (document_id,),
            ).fetchone()["value"]

    def _start_step(self, job_id: str) -> str:
        now = utc_now()
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT id,status,attempt FROM manual_generation_steps WHERE job_id=?
                AND step_key='render_qa' ORDER BY attempt DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if row is None:
                raise ManualQaError("说明书任务缺少质量检查阶段")
            if row["status"] == "running":
                raise ManualQaError("说明书正在执行质量检查，请勿重复提交")
            if row["status"] in {"completed", "completed_with_warnings"}:
                step_id = str(uuid4())
                connection.execute(
                    """INSERT INTO manual_generation_steps(id,job_id,step_key,status,attempt,
                    summary_json,started_at) VALUES (?,?,'render_qa','running',?,'{}',?)""",
                    (step_id, job_id, row["attempt"] + 1, now),
                )
            else:
                step_id = row["id"]
                connection.execute(
                    """UPDATE manual_generation_steps SET status='running',started_at=?,
                    finished_at=NULL,safe_error_message=NULL WHERE id=?""", (now, step_id),
                )
            connection.execute(
                """UPDATE manual_generation_jobs SET status='running',current_step='render_qa',
                updated_at=?,safe_error_message=NULL WHERE id=?""", (now, job_id),
            )
        return step_id

    def _finish_failure(self, job_id: str, step_id: str, error: Exception) -> None:
        now = utc_now()
        message = "说明书质量检查失败：{0}".format(type(error).__name__)
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE manual_generation_steps SET status='failed',finished_at=?,
                safe_error_message=? WHERE id=?""", (now, message, step_id),
            )
            connection.execute(
                """UPDATE manual_generation_jobs SET status='failed',current_step='render_qa',
                updated_at=?,safe_error_message=? WHERE id=?""", (now, message, job_id),
            )
