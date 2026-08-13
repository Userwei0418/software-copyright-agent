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

    def test_final_document_is_a_ready_asset_without_qa_gate(self) -> None:
        source = (self.root / "ui" / "AssetLibrary.tsx").read_text(encoding="utf-8")
        self.assertIn('item.document_kind === "final_document"', source)
        self.assertIn('item.integrity.status === "verified"', source)
        self.assertIn("finalManual || passedManual", source)
        self.assertIn("已由人工定稿，可随时导出", source)


if __name__ == "__main__":
    unittest.main()
