import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_sidecar.py"
SPEC = importlib.util.spec_from_file_location("build_sidecar", SCRIPT)
assert SPEC and SPEC.loader
build_sidecar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_sidecar)


class BuildSidecarTests(unittest.TestCase):
    def test_tauri_artifact_names(self) -> None:
        self.assertEqual(
            build_sidecar.artifact_name("aarch64-apple-darwin"),
            "copyright-agent-sidecar-aarch64-apple-darwin",
        )
        self.assertEqual(
            build_sidecar.artifact_name("x86_64-pc-windows-msvc"),
            "copyright-agent-sidecar-x86_64-pc-windows-msvc.exe",
        )

    @patch.object(build_sidecar.shutil, "which", return_value=None)
    @patch.object(build_sidecar.platform, "machine", return_value="arm64")
    def test_detects_apple_silicon_without_rustc(self, _machine, _which) -> None:
        with patch.object(build_sidecar.sys, "platform", "darwin"):
            self.assertEqual(build_sidecar.detect_target_triple(), "aarch64-apple-darwin")

    @patch.object(build_sidecar.shutil, "which", return_value=None)
    @patch.object(build_sidecar.platform, "machine", return_value="mips")
    def test_unknown_architecture_is_rejected(self, _machine, _which) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsupported CPU architecture"):
            build_sidecar.detect_target_triple()


if __name__ == "__main__":
    unittest.main()
