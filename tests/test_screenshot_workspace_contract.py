import unittest
from pathlib import Path


class ScreenshotWorkspaceContractTests(unittest.TestCase):
    def test_manual_evidence_is_primary_and_experimental_capture_requires_authorization(self):
        source = (Path(__file__).parents[1] / "ui" / "ScreenshotAssetWorkspace.tsx").read_text(
            encoding="utf-8")
        self.assertIn("多选图片", source)
        self.assertIn("选择文件夹", source)
        self.assertIn("粘贴截图", source)
        self.assertIn("批量分析", source)
        self.assertIn("审核并采用当前截图", source)
        self.assertIn("editableInterpretation", source)
        self.assertIn("立即重试当前截图", source)
        self.assertIn("重试失败截图", source)
        self.assertIn("截图处理进度", source)
        self.assertIn("采用集变化会自动更新第 7 章", source)
        self.assertIn("立即同步 ${adoptedCount} 张截图", source)
        self.assertIn("请留在当前页，状态会自动更新", source)
        self.assertIn("refresh(selectedId)", source)
        self.assertIn("inspector-secondary-actions", source)
        self.assertNotIn('<details className="inspector-more">', source)
        self.assertIn("去设置视觉模型", source)
        self.assertIn("onOpenSettings", source)
        self.assertIn("const [captureOpen, setCaptureOpen] = useState(false)", source)
        self.assertIn('className="experimental-capture" open={captureOpen}', source)
        self.assertIn("开发测试中 · 不稳定 · 不推荐。复杂项目可能依赖数据库、中间件、测试账号及业务数据，建议优先导入已经准备好的真实截图。", source)
        self.assertIn("captureAuthorized", source)
        self.assertIn("我明确授权运行上述项目脚本", source)
        self.assertIn('await runImport([captured.path], "automated")', source)
        self.assertNotIn("useEffect(() => launchCaptureProject", source)

    def test_failed_screenshot_node_has_direct_retry_action(self):
        root = Path(__file__).parents[1]
        manual = (root / "ui" / "ManualWorkspace.tsx").read_text(encoding="utf-8")
        api = (root / "ui" / "api.ts").read_text(encoding="utf-8")
        self.assertIn('return "重试此截图"', manual)
        self.assertIn("retryScreenshotAnalysisNode", manual)
        self.assertIn("/screenshots/retry-analysis", api)


if __name__ == "__main__":
    unittest.main()
