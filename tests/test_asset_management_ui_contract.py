from pathlib import Path
import unittest


class AssetManagementUiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_sidebar_does_not_show_placeholder_quality_page(self) -> None:
        source = (self.root / "ui" / "App.tsx").read_text(encoding="utf-8")
        self.assertNotIn("质量检查 <small>待开发</small>", source)

    def test_recent_projects_are_navigation_only(self) -> None:
        source = (self.root / "ui" / "ProjectOverview.tsx").read_text(encoding="utf-8")
        self.assertIn('项目管理请前往“我的资产”', source)
        self.assertNotIn("clearTasks", source)
        self.assertNotIn("removeTask", source)
        self.assertNotIn("deleteTask", source)

    def test_assets_require_current_quality_and_offer_repair_for_failed_documents(self) -> None:
        source = (self.root / "ui" / "AssetLibrary.tsx").read_text(encoding="utf-8")
        self.assertIn('item.document_kind === "final_document"', source)
        self.assertIn('item.quality.status === "passed"', source)
        self.assertIn('item.freshness.status === "current"', source)
        self.assertIn('item.integrity.status === "verified"', source)
        self.assertIn("const manual = row.manuals[0] || null", source)
        self.assertIn("检查并修复说明书", source)
        self.assertIn("质量检查未通过，待修复", source)
        self.assertIn("manual.version, destination, reviewDraft", source)


if __name__ == "__main__":
    unittest.main()
