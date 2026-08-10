import unittest

from software_copyright_agent.font_assets import FontAsset, FontAssetError


class BundledFontAssetTests(unittest.TestCase):
    def test_bundled_font_hash_license_and_common_chinese_glyphs(self) -> None:
        summary = FontAsset.bundled_cjk().validate("软件著作权源程序代码中文测试")

        self.assertEqual(summary["missing_codepoints"], 0)
        self.assertEqual(summary["license"], "SIL Open Font License 1.1")

    def test_missing_glyph_is_blocked(self) -> None:
        with self.assertRaises(FontAssetError):
            FontAsset.bundled_cjk().validate("𠀀")
