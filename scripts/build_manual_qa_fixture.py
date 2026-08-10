"""Build a deterministic formal-manual fixture for render regression review."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from software_copyright_agent.manual_document import FormalManualBuilder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    asset_root = output.parent / "fixture-assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    diagram = asset_root / "architecture.png"
    screenshot = asset_root / "workspace.png"
    make_diagram(diagram)
    make_screenshot(screenshot)
    base_blocks = [
        {"type": "paragraph", "text": "本系统采用本地优先的证据化处理方式，将项目扫描、事实提取、模型生成、图表渲染和文档装配拆分为可独立恢复的阶段。每个阶段均保存输入摘要、版本、状态和安全错误信息，从而避免一次失败破坏已经完成的工作。"},
        {"type": "paragraph", "text": "业务数据保存在本地 SQLite 数据库中，源代码只在用户设备内读取。模型调用仅接收经过筛选的项目线索和代表性源码片段，敏感文件、凭据、构建产物和第三方依赖不会进入说明书生成上下文。"},
        {"type": "list", "lead": "核心设计目标包括：", "items": ["保持项目证据、正文段落、图表语义和最终文档之间的可追溯关系", "允许正文、图表、截图和 Word 文档分别重试并保留历史版本", "使用真实 Word 结构生成标题、列表、表格、图注、页眉页脚和页码"]},
        {"type": "table", "title": "主要模块及职责", "headers": ["模块", "输入", "主要职责", "输出"], "rows": [["项目扫描", "本地目录或 ZIP", "建立文件清单并提取结构事实", "项目快照"], ["说明书研究", "项目事实与代表性源码", "形成事实、推断和待确认线索", "研究产物"], ["文档装配", "结构化章节及图片资产", "生成正式 Word 文档并登记完整性", "DOCX 版本"]]},
    ]
    sections = [
        {"section_key": "introduction", "title": "引言", "ordinal": 1, "status": "generated", "blocks": base_blocks},
        {"section_key": "architecture", "title": "总体设计", "ordinal": 2, "status": "generated", "blocks": base_blocks + [{"type": "figure_request", "figure_key": "architecture", "title": "系统总体架构图"}]},
        {"section_key": "modules", "title": "功能与模块设计", "ordinal": 3, "status": "generated", "blocks": base_blocks},
        {"section_key": "ui_operations", "title": "用户界面与操作说明", "ordinal": 4, "status": "generated", "blocks": base_blocks},
    ]
    figures = [{"figure_key": "architecture", "section_key": "architecture", "title": "系统总体架构图", "png_relative_path": diagram.relative_to(output.parent).as_posix()}]
    description = {
        "page_purpose": "项目工作台用于选择当前项目、启动正式说明书生成并查看已经完成的文档版本。",
        "entry_conditions": "用户已经完成项目扫描，并在右上角项目选择器中选择需要处理的本地项目。",
        "visible_regions": "界面包含项目与模型选择区、一键生成操作区、文档结果区、版本列表和内部阶段留痕区。",
        "typical_workflow": "用户选择已验证模型后点击生成按钮，等待流程完成，再在结果卡片中预览或导出正式 Word 文档。",
        "backend_interactions": "前端通过本地 Sidecar API 创建版本任务，依次调用研究、正文、图表、截图决策和文档装配服务。",
        "result_validation_recovery": "成功后显示文档完整性与版本；任一阶段失败时保留既有产物，并在阶段留痕中提供安全错误信息。",
    }
    screenshots = [{"screenshot_key": "workspace", "section_key": "ui_operations", "title": "说明书工作台", "source": "user", "image_relative_path": screenshot.relative_to(output.parent).as_posix(), "description": description}]
    context = {"software_name": "软著材料助手", "software_version": "V1.0"}
    FormalManualBuilder().build(output, context, sections, figures, screenshots, output.parent)
    print(output)


def make_diagram(path: Path) -> None:
    image = Image.new("RGB", (1600, 900), "#f7fafc")
    draw = ImageDraw.Draw(image)
    boxes = [(90, 330, 360, 530, "桌面界面"), (500, 170, 850, 370, "本地 Sidecar"),
             (500, 510, 850, 710, "SQLite 与资产目录"), (1010, 330, 1460, 530, "模型协议适配器")]
    for left, top, right, bottom, label in boxes:
        draw.rounded_rectangle((left, top, right, bottom), 24, fill="#ffffff", outline="#4f7893", width=5)
        draw.text((left + 60, top + 82), label, fill="#183247")
    for start, end in [((360, 430), (500, 270)), ((360, 430), (500, 610)), ((850, 270), (1010, 430))]:
        draw.line((start, end), fill="#d87840", width=7)
    image.save(path, "PNG")


def make_screenshot(path: Path) -> None:
    image = Image.new("RGB", (1600, 1000), "#f2f5f7")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 260, 1000), fill="#182531")
    draw.rectangle((300, 70, 1530, 220), fill="#ffffff", outline="#dce3e7", width=3)
    draw.rounded_rectangle((300, 260, 1530, 410), 20, fill="#263c4c")
    draw.rounded_rectangle((300, 450, 1530, 590), 20, fill="#ffffff", outline="#b9ddca", width=4)
    for top in (640, 740, 840):
        draw.rounded_rectangle((300, top, 1530, top + 70), 14, fill="#ffffff", outline="#dce3e7", width=3)
    image.save(path, "PNG")


if __name__ == "__main__":
    main()
