import hashlib
import struct
import os
import tempfile
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Tuple


NOTO_CJK_SC_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
NOTO_SYMBOLS_2_SHA256 = "630846d528dbe4c4981370a4d0a9475a1fd1491a129bb411f8e157cdb5de13c6"
NOTO_CJK_LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
DEFAULT_CJK_FAMILY = "Noto Sans CJK SC"
DEFAULT_SYMBOL_FAMILY = "Noto Sans Symbols 2"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


class FontAssetError(ValueError):
    pass


@dataclass(frozen=True)
class FontAsset:
    family: str
    path: Path
    expected_sha256: str

    @classmethod
    def bundled_cjk(cls) -> "FontAsset":
        path = Path(__file__).resolve().parent / "assets" / "fonts" / "noto-cjk" / (
            "NotoSansCJKsc-Regular.otf"
        )
        return cls(DEFAULT_CJK_FAMILY, path, NOTO_CJK_SC_SHA256)

    @classmethod
    def bundled_symbols(cls) -> "FontAsset":
        path = Path(__file__).resolve().parent / "assets" / "fonts" / "noto-cjk" / (
            "NotoSansSymbols2-Regular.ttf"
        )
        return cls(DEFAULT_SYMBOL_FAMILY, path, NOTO_SYMBOLS_2_SHA256)

    def validate(self, required_characters: Iterable[str] = ()) -> dict:
        if not self.path.is_file():
            raise FontAssetError("Bundled CJK font is missing: {0}".format(self.path))
        actual_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if actual_hash != self.expected_sha256:
            raise FontAssetError("Bundled CJK font hash does not match the release manifest")
        license_path = self.path.with_name("OFL-1.1.txt")
        if not license_path.is_file() or hashlib.sha256(license_path.read_bytes()).hexdigest() != NOTO_CJK_LICENSE_SHA256:
            raise FontAssetError("Bundled CJK font license is missing or modified")
        codepoints = {
            ord(character) for character in required_characters
            if ord(character) > 127 and unicodedata.category(character) not in {"Cf", "Mn", "Me"}
        }
        coverage = OpenTypeCmap(self.path)
        missing = sorted(codepoint for codepoint in codepoints if not coverage.contains(codepoint))
        if missing:
            sample = "".join(chr(codepoint) for codepoint in missing[:20])
            raise FontAssetError("Bundled CJK font lacks required glyphs: {0}".format(sample))
        return {
            "family": self.family,
            "path": str(self.path),
            "sha256": actual_hash,
            "required_codepoints": len(codepoints),
            "missing_codepoints": 0,
            "license": "SIL Open Font License 1.1",
        }


def validate_font_bundle(
    required_characters: Iterable[str], assets: Sequence[FontAsset] = ()
) -> dict:
    """Validate integrity and union coverage for all fonts embedded in a DOCX."""
    selected = tuple(assets) or (FontAsset.bundled_cjk(), FontAsset.bundled_symbols())
    summaries = [asset.validate() for asset in selected]
    coverages = [(asset, OpenTypeCmap(asset.path)) for asset in selected]
    codepoints = {
        ord(character)
        for character in required_characters
        if ord(character) > 127
        and unicodedata.category(character) not in {"Cf", "Mn", "Me"}
    }
    missing = sorted(
        codepoint
        for codepoint in codepoints
        if not any(coverage.contains(codepoint) for _, coverage in coverages)
    )
    if missing:
        sample = "".join(chr(codepoint) for codepoint in missing[:20])
        raise FontAssetError("Bundled font set lacks required glyphs: {0}".format(sample))
    return {
        "families": [asset.family for asset in selected],
        "fonts": summaries,
        "required_codepoints": len(codepoints),
        "missing_codepoints": 0,
    }


class OpenTypeCmap:
    def __init__(self, path: Path) -> None:
        self._data = path.read_bytes()
        self._subtables = self._read_subtables()

    def contains(self, codepoint: int) -> bool:
        return any(self._format12_contains(table, codepoint) if fmt == 12
                   else self._format4_contains(table, codepoint)
                   for fmt, table in self._subtables)

    def _read_subtables(self) -> Tuple[tuple, ...]:
        data = self._data
        if data[:4] == b"ttcf":
            raise FontAssetError("Font collections are not supported by the bundled font gate")
        num_tables = struct.unpack_from(">H", data, 4)[0]
        cmap_offset = None
        for index in range(num_tables):
            offset = 12 + index * 16
            tag, _, table_offset, _ = struct.unpack_from(">4sLLL", data, offset)
            if tag == b"cmap":
                cmap_offset = table_offset
                break
        if cmap_offset is None:
            raise FontAssetError("Font has no cmap table")
        count = struct.unpack_from(">H", data, cmap_offset + 2)[0]
        seen = set()
        subtables = []
        for index in range(count):
            record = cmap_offset + 4 + index * 8
            platform, encoding, relative = struct.unpack_from(">HHL", data, record)
            absolute = cmap_offset + relative
            fmt = struct.unpack_from(">H", data, absolute)[0]
            if fmt in {4, 12} and (platform, encoding) in {(0, 3), (0, 4), (3, 1), (3, 10)}:
                key = (fmt, absolute)
                if key not in seen:
                    seen.add(key)
                    subtables.append(key)
        if not subtables:
            raise FontAssetError("Font has no supported Unicode cmap subtable")
        return tuple(subtables)

    def _format12_contains(self, offset: int, codepoint: int) -> bool:
        groups = struct.unpack_from(">L", self._data, offset + 12)[0]
        low, high = 0, groups - 1
        while low <= high:
            middle = (low + high) // 2
            start, end, _ = struct.unpack_from(">LLL", self._data, offset + 16 + middle * 12)
            if codepoint < start:
                high = middle - 1
            elif codepoint > end:
                low = middle + 1
            else:
                return True
        return False

    def _format4_contains(self, offset: int, codepoint: int) -> bool:
        if codepoint > 0xFFFF:
            return False
        seg_count = struct.unpack_from(">H", self._data, offset + 6)[0] // 2
        end_codes = offset + 14
        start_codes = end_codes + seg_count * 2 + 2
        for index in range(seg_count):
            end = struct.unpack_from(">H", self._data, end_codes + index * 2)[0]
            if codepoint > end:
                continue
            start = struct.unpack_from(">H", self._data, start_codes + index * 2)[0]
            return start <= codepoint <= end and codepoint != 0xFFFF
        return False


def embed_font_in_docx(document_path: Path, asset: FontAsset = None) -> dict:
    asset = asset or FontAsset.bundled_cjk()
    summary = asset.validate()
    font_key = uuid.UUID(asset.expected_sha256[:32])
    font_key_text = "{{{0}}}".format(str(font_key).upper())
    part_name = "word/fonts/{0}.odttf".format(str(font_key))
    embedded = bytearray(asset.path.read_bytes())
    xor_key = font_key.bytes[::-1]
    for index in range(min(32, len(embedded))):
        embedded[index] ^= xor_key[index % 16]

    ET.register_namespace("w", W_NS)
    ET.register_namespace("r", R_NS)
    ET.register_namespace("", PKG_REL_NS)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="font-embedded-", suffix=".docx", dir=str(document_path.parent)
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(document_path, "r") as source, zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            font_table = source.read("word/fontTable.xml")

            rels_name = "word/_rels/fontTable.xml.rels"
            if rels_name in source.namelist():
                relationships = ET.fromstring(source.read(rels_name))
            else:
                relationships = ET.Element("{{{0}}}Relationships".format(PKG_REL_NS))
            existing_ids = {node.get("Id") for node in relationships}
            relation_id = "rIdFont1"
            counter = 1
            while relation_id in existing_ids:
                counter += 1
                relation_id = "rIdFont{0}".format(counter)
            ET.SubElement(
                relationships, "{{{0}}}Relationship".format(PKG_REL_NS),
                {"Id": relation_id,
                 "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font",
                 "Target": "fonts/{0}.odttf".format(str(font_key))},
            )
            embed_xml = (
                '<w:embedRegular r:id="{0}" w:fontKey="{1}" w:subsetted="0"/>'
                .format(relation_id, font_key_text).encode("utf-8")
            )
            font_open = '<w:font w:name="{0}">'.format(asset.family).encode("utf-8")
            if font_open in font_table:
                font_table = font_table.replace(font_open, font_open + embed_xml, 1)
            else:
                font_xml = font_open + embed_xml + b"</w:font>"
                font_table = font_table.replace(b"</w:fonts>", font_xml + b"</w:fonts>", 1)

            settings = source.read("word/settings.xml")
            if b"<w:embedTrueTypeFonts" not in settings:
                root_end = settings.find(b">", settings.find(b"<w:settings")) + 1
                settings = settings[:root_end] + b"<w:embedTrueTypeFonts/>" + settings[root_end:]

            content_types = source.read("[Content_Types].xml")
            if b'Extension="odttf"' not in content_types:
                declaration = (
                    b'<Default Extension="odttf" '
                    b'ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/>'
                )
                content_types = content_types.replace(b"</Types>", declaration + b"</Types>", 1)

            replacements = {
                "word/fontTable.xml": font_table,
                rels_name: ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
                "word/settings.xml": settings,
                "[Content_Types].xml": content_types,
            }
            skipped = set(replacements) | {part_name}
            for item in source.infolist():
                if item.filename not in skipped:
                    target.writestr(item, source.read(item.filename))
            for name, data in replacements.items():
                target.writestr(name, data)
            target.writestr(part_name, bytes(embedded))
        os.replace(str(temporary_path), str(document_path))
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return {**summary, "embedded": True, "font_key": font_key_text, "part_name": part_name}
