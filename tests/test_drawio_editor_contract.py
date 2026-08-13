from pathlib import Path
import unittest


class DrawioEditorContractTests(unittest.TestCase):
    def test_host_confirm_uses_current_xml_export_instead_of_save_rpc(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "ui" / "DrawioEditor.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn('onClick={confirmAndAssemble}', source)
        self.assertIn('const currentXml = currentXmlRef.current || xml;', source)
        self.assertIn('post({ action: "export", format: "svg", xml: currentXml', source)
        self.assertNotIn('onClick={() => post({ action: "save" })}', source)
        self.assertIn('Draw.io ${stage} 导出超时', source)


if __name__ == "__main__":
    unittest.main()
