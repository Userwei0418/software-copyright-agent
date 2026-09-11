from pathlib import Path
import unittest


class ManualQaUiContractTests(unittest.TestCase):
    def test_quality_actions_report_progress_inside_the_open_dialog(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "ui" / "ManualWorkspace.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn('className={`manual-qa-action ${qualityAction.state}`}', source)
        self.assertIn('role="status" aria-live="polite"', source)
        self.assertIn('kind: "defer", state: "working"', source)
        self.assertIn('正在生成缺失图表 ${index + 1}/${context.figureKeys.length}', source)
        self.assertIn('Word v${document.version} 已装配，正在逐页复检', source)
        self.assertIn('豁免已保存：该项已移出待处理列表', source)
        self.assertIn('decision.check_key === item.key', source)

    def test_final_document_export_requires_its_own_current_quality_gate(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "ui" / "ManualWorkspace.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn('!isFinalDocument && quality.checks.some', source)
        self.assertIn("const canExportFinal", source)
        self.assertIn('selectedDocument.quality.status === "passed"', source)
        self.assertIn('if (!reviewDraft && !canExportFinal)', source)
        self.assertIn("重新装配并复检", source)
        self.assertIn("终稿检查未通过", source)
        self.assertIn('selectedDocument.document_kind !== "final_document"', source)
        self.assertNotIn('(!deliveryReady && !reviewDraft)', source)
        self.assertIn('state: "choosing", message: "正在打开保存位置选择窗口', source)
        self.assertIn('重试导出', source)


if __name__ == "__main__":
    unittest.main()
