import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class QuickStartUiContractTests(unittest.TestCase):
    def test_stable_dev_command_is_visibly_distinct_from_installed_app(self):
        package = (ROOT / "package.json").read_text(encoding="utf-8")
        config = (ROOT / "src-tauri" / "tauri.dev.conf.json").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "run_tauri.py").read_text(encoding="utf-8")
        rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("tauri.dev.conf.json", package)
        self.assertIn("软著材料助手 · 开发版", config)
        self.assertIn("com.local.software-copyright-agent.dev", config)
        self.assertIn("COPYRIGHT_AGENT_DATA_DIR", runner)
        self.assertIn("COPYRIGHT_AGENT_DATA_DIR", rust)

    def test_sidebar_exposes_quick_start_and_page_is_wired(self):
        app = (ROOT / "ui" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("快速开始", app)
        self.assertIn("<QuickStart", app)
        self.assertIn('page === "quick"', app)
        self.assertIn("本地服务正在完成冷启动，稍后自动重连", app)
        self.assertIn("connect(attempt + 1)", app)

    def test_sidebar_exposes_exportable_redacted_run_diagnostics(self):
        app = (ROOT / "ui" / "App.tsx").read_text(encoding="utf-8")
        page = (ROOT / "ui" / "RunLogs.tsx").read_text(encoding="utf-8")
        api = (ROOT / "src" / "software_copyright_agent" / "sidecar.py").read_text(
            encoding="utf-8")
        diagnostics = (ROOT / "src" / "software_copyright_agent" /
                       "run_diagnostics.py").read_text(encoding="utf-8")
        rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("运行日志", app)
        self.assertIn("<RunLogs", app)
        self.assertIn("导出诊断包", page)
        self.assertIn("/api/v1/run-diagnostics", api)
        self.assertIn("[REDACTED]", diagnostics)
        self.assertIn("export_run_diagnostics", rust)

    def test_quick_start_requires_explicit_screenshot_authorization(self):
        page = (ROOT / "ui" / "QuickStart.tsx").read_text(encoding="utf-8")
        self.assertIn("截图安全确认", page)
        self.assertIn("授权自动采用", page)
        self.assertIn("vision_verified", page)
        self.assertIn("一键恢复并继续", page)
        self.assertIn("前往我的资产", page)
        self.assertIn("清空并重新开始", page)
        self.assertIn("discardQuickStartRun", page)
        self.assertIn("清空流程并开始新任务", page)

    def test_quick_start_has_animated_execution_visualization(self):
        css = (ROOT / "ui" / "quick-start.css").read_text(encoding="utf-8")
        page = (ROOT / "ui" / "QuickStart.tsx").read_text(encoding="utf-8")
        self.assertIn(".quick-stage-track", css)
        self.assertIn("@keyframes quick-flow", css)
        self.assertIn(".quick-live-nodes", css)
        self.assertIn(".quick-activity-log", css)
        self.assertIn("运行日志", page)
        self.assertIn("默认收起", page)
        self.assertIn("截图证据", page)
        self.assertIn("执行流程画布", page)
        self.assertIn("定位当前任务", page)
        self.assertIn("quick-canvas-viewport", css)
        self.assertIn("scrollIntoView", page)

    def test_quick_start_shows_parallel_deliverables_and_real_dependencies(self):
        page = (ROOT / "ui" / "QuickStart.tsx").read_text(encoding="utf-8")
        css = (ROOT / "ui" / "quick-start-enhancements.css").read_text(encoding="utf-8")
        service = (ROOT / "src" / "software_copyright_agent" / "quick_start.py").read_text(
            encoding="utf-8")
        self.assertIn("ThreadPoolExecutor(max_workers=2", service)
        self.assertIn('executor.submit(source_branch)', service)
        self.assertIn('executor.submit(manual_branch)', service)
        self.assertIn("源码文档线", page)
        self.assertIn("软件说明书线", page)
        self.assertIn("项目证据研究", page)
        self.assertIn("撰写说明书正文", page)
        self.assertIn("生成专业图表", page)
        self.assertIn('aria-label="正文与专业图表并行生成"', page)
        self.assertIn('kinds: ["research", "profile"]', page)
        self.assertIn('kinds: ["section"]', page)
        self.assertIn('kinds: ["figure"]', page)
        self.assertNotIn('title: "装配软件说明书"', page)
        self.assertIn("quick-dependency-layer", page)
        self.assertIn("target.dependencies", page)
        self.assertIn("target.dependencies", page)
        self.assertIn("path.sourceKey === activeRelationKey", page)
        self.assertIn("hoveredRelationKey", page)
        self.assertIn("来源章节 ·", page)
        self.assertIn("pipeline-branches", page)
        self.assertIn("查看产物", page)
        self.assertIn('return "查看详情"', page)
        self.assertIn('onPointerDown={(event) => event.stopPropagation()}', page)
        self.assertIn('void openNodeArtifact(node)', page)
        self.assertIn("stage-help", page)
        self.assertIn("onNavigate", page)
        self.assertIn(".quick-parallel-pipeline", css)
        self.assertIn(".manual-parallel-work", css)
        self.assertIn(".manual-generation-cluster", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn("overflow-y:auto", css)
        self.assertIn("overflow-x:clip", css)
        self.assertIn("grid-template-columns:minmax(242px,.92fr)", css)
        self.assertIn('marker id="quick-endpoint"', page)
        self.assertIn("run.manual_job.progress.percent", page)
        self.assertIn("quick-artifact-preview", page)
        self.assertIn("listFormalManualSections", page)
        self.assertIn("loadFormalFigureAsset", page)
        self.assertIn("listFormalManualDocuments", page)
        self.assertIn("loadFormalManualQaPage", page)
        self.assertIn("查看终稿", page)
        self.assertIn("loadScreenshotEvidenceWorkspace", page)
        self.assertIn("loadScreenshotEvidenceImage", page)
        self.assertIn("loadFormalScreenshotImage", page)
        self.assertIn('kind: "summary"', page)
        self.assertIn('kind: "screenshots"', page)
        self.assertIn("快速产物预览", page)
        self.assertIn("快速截图预览", page)
        self.assertIn("进入截图工作台", page)
        self.assertIn("changeQuickScreenshot", page)
        self.assertIn(".catch(() => null)", page)
        self.assertIn(".catch(() => [])", page)
        self.assertIn("document.elementFromPoint", page)
        self.assertIn('window.addEventListener("scroll", reconcileAfterScroll, true)', page)
        self.assertIn('window.addEventListener("blur", clearRelation)', page)
        self.assertIn('onPointerLeave={() => { lastPointerPosition.current = null;', page)
        self.assertIn('onScroll={() => setHoveredRelationKey("")}', page)
        self.assertIn("const activeRelationKey = hoveredRelationKey", page)
        self.assertNotIn("selectedRelationKey", page)
        self.assertIn('event.key === "Escape"', page)
        self.assertNotIn('title={meta.help}', page)
        self.assertNotIn("min-width:1540px", css)

    def test_quick_start_does_not_present_stage_retry_as_a_new_document_version(self):
        page = (ROOT / "ui" / "QuickStart.tsx").read_text(encoding="utf-8")
        service = (ROOT / "src" / "software_copyright_agent" / "quick_start.py").read_text(
            encoding="utf-8")
        self.assertIn("节点重试", page)
        self.assertNotIn("run.config.retry_limit + 1", page)
        self.assertIn('retry_limit=0', service)
        self.assertNotIn('previous_job["status"]', service)

    def test_quick_start_caches_the_post_qa_final_document(self):
        service = (ROOT / "src" / "software_copyright_agent" / "quick_start.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('final = final_qa["document"]', service)

    def test_quick_start_never_downgrades_qa_blockers_to_warnings(self):
        service = (ROOT / "src" / "software_copyright_agent" / "quick_start.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('qa_summary.get("failed_check_count")', service)
        self.assertIn('final_summary.get("failed_check_count")', service)
        self.assertIn('or not final_qa["qa_run"].get("passed")', service)

    def test_preferences_expose_verified_default_screenshot_model(self):
        settings = (ROOT / "ui" / "Settings.tsx").read_text(encoding="utf-8")
        self.assertIn("截图识别默认模型", settings)
        self.assertIn("vision_model_id", settings)
        self.assertIn("vision_verified", settings)

    def test_preferences_expose_guarded_advanced_style_prompts(self):
        settings = (ROOT / "ui" / "Settings.tsx").read_text(encoding="utf-8")
        self.assertIn("高级风格提示词", settings)
        self.assertIn("专业人员使用", settings)
        self.assertIn("document_style_prompt", settings)
        self.assertIn("diagram_style_prompt", settings)
        self.assertIn("window.confirm", settings)


if __name__ == "__main__":
    unittest.main()
