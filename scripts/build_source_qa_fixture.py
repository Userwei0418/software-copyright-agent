"""Build and optionally render a deterministic long-path source pagination fixture.

Run with PYTHONPATH=src. The generated code is a synthetic layout regression
sample; it is never presented as a real software project's registration material.
"""

import argparse
import hashlib
import json
from pathlib import Path

from software_copyright_agent.code_preview import CodeInputFile, CodePreviewBuilder
from software_copyright_agent.source_document import SourceDocumentBuilder
from software_copyright_agent.source_document_qa import LibreOfficeRenderer, SourceDocumentQaInspector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    source_root = output.parent / "source-fixture"
    relative = "src/services/" + "/".join(
        ["business_module_with_long_package_name"] * 8
    ) + "/order_management_workflow.py"
    source = source_root / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("".join(
        "    result_{0} = '订单中文字段' + str({0})\n".format(index)
        for index in range(3100)
    ), encoding="utf-8")
    preview = CodePreviewBuilder().build(source_root, [CodeInputFile(
        relative, "A", 90, "Python", hashlib.sha256(source.read_bytes()).hexdigest(),
    )])
    summary = SourceDocumentBuilder().build(
        output, "源码分页排版回归样例", "V1.0", preview.pages,
    )
    report = {
        "fixture_kind": "synthetic_long_path_layout_regression",
        "disclosure": "合成排版样本，不代表真实项目代码或正式申请材料。",
        "preview_pages": len(preview.pages),
        "document": summary,
    }
    if args.render_dir:
        render = LibreOfficeRenderer().render(output, args.render_dir.expanduser().resolve())
        result = SourceDocumentQaInspector().inspect(
            output, hashlib.sha256(output.read_bytes()).hexdigest(), render,
        )
        report["qa"] = result.summary
        report["failed_checks"] = [check.__dict__ for check in result.checks if not check.passed]
    report_path = output.with_suffix(".qa.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"document": str(output), "report": str(report_path),
                      "qa_passed": report.get("qa", {}).get("passed")}, ensure_ascii=False))
    if args.render_dir and not report["qa"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
