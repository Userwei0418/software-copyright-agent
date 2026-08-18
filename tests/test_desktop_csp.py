import json
import unittest
from pathlib import Path


class DesktopContentSecurityPolicyTests(unittest.TestCase):
    def test_packaged_webview_allows_blob_image_previews(self) -> None:
        config = json.loads(
            (Path(__file__).resolve().parents[1] / "src-tauri" / "tauri.conf.json")
            .read_text(encoding="utf-8")
        )
        csp = config["app"]["security"]["csp"]
        image_policy = next(
            directive for directive in csp.split(";")
            if directive.strip().startswith("img-src")
        )

        self.assertIn("blob:", image_policy.split())
