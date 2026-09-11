import unittest
from types import SimpleNamespace

from software_copyright_agent.quick_start import QuickStartBlocked, QuickStartService
from software_copyright_agent.screenshot_claims import (
    screenshot_analysis_reminders, unresolved_screenshot_claims,
)


class ScreenshotClaimTests(unittest.TestCase):
    def test_reminders_are_distinct_from_unresolved_facts_in_old_and_new_revisions(self):
        value = {"warnings": ["未观察到失败或异常状态提示", "具体后台结果不可见，仅依据按钮推断"],
                 "unresolved_claims": ["保存是否成功需要核实"]}
        self.assertEqual(screenshot_analysis_reminders(value), ["未观察到失败或异常状态提示"])
        self.assertEqual(unresolved_screenshot_claims(value),
                         ["保存是否成功需要核实", "具体后台结果不可见，仅依据按钮推断"])
        self.assertEqual(unresolved_screenshot_claims({
            "warnings": ["本图未显示错误提示，不作推断", "由旧版已确认说明兼容迁移"]}), [])

    def test_quick_start_does_not_auto_adopt_or_reuse_unresolved_claims(self):
        for status in ("pending", "adopted"):
            calls = []
            asset = {"id": "screen", "title": "源码材料", "adoption_status": status,
                     "analysis_status": "completed", "review_status": "reviewed",
                     "sensitive_status": "confirmed_safe",
                     "interpretation": {"warnings": ["依据按钮推断后台保存成功"]}}
            service = QuickStartService.__new__(QuickStartService)
            service._screenshots = SimpleNamespace(
                list_assets=lambda _: [asset], review=lambda *a, **k: calls.append(a))
            with self.assertRaises(QuickStartBlocked) as caught:
                service._adopt_analyzed_screenshots("task")
            self.assertEqual(calls, [])
            self.assertEqual(caught.exception.details["screenshots"][0]["asset_id"], "screen")

    def test_quick_start_preserves_reminders_and_manual_exclusion(self):
        calls = []
        assets = [{"id": "safe", "title": "设置", "adoption_status": "pending",
                   "analysis_status": "completed", "interpretation": {
                       "warnings": ["未观察到失败状态"]}},
                  {"id": "excluded", "title": "未采用", "adoption_status": "excluded",
                   "analysis_status": "completed", "interpretation": {
                       "unresolved_claims": ["保存成功尚需确认"]}}]
        def review(*args, **kwargs):
            calls.append((args, kwargs))
            return {"id": args[1]}
        service = QuickStartService.__new__(QuickStartService)
        service._screenshots = SimpleNamespace(list_assets=lambda _: assets, review=review)
        adopted, reused = service._adopt_analyzed_screenshots("task")
        self.assertEqual(adopted, [{"id": "safe"}])
        self.assertEqual(reused, [])
        self.assertEqual(calls[0][0][2]["warnings"], ["未观察到失败状态"])
