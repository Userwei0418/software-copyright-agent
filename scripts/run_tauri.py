#!/usr/bin/env python3
"""Keep the frozen sidecar synchronized before starting a Tauri command."""

from __future__ import annotations

import os
import shutil
import sys
import importlib.util
from pathlib import Path

from build_sidecar import OUTPUT_DIR, detect_target_triple, ensure_current


def installed_app_data_dir() -> Path:
    """Use production assets while giving the dev window an isolated bundle id."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "com.local.software-copyright-agent"
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "com.local.software-copyright-agent"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "com.local.software-copyright-agent"


def main() -> int:
    arguments = sys.argv[1:] or ["dev", "--no-watch"]
    if importlib.util.find_spec("PyInstaller") is None:
        root = Path(__file__).resolve().parents[1]
        candidates = [root / ".venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe"]
        project_python = next((path for path in candidates if path.is_file()), None)
        if project_python and Path(sys.executable) != project_python:
            os.execv(str(project_python), [str(project_python), str(Path(__file__).resolve()),
                                           *arguments])
        raise RuntimeError("PyInstaller is required to prepare the desktop sidecar")
    metadata = ensure_current(detect_target_triple(), OUTPUT_DIR.resolve())
    action = "已重建" if metadata.get("rebuilt") else "已是最新"
    print(f"sidecar {action}：{metadata['artifact']}", flush=True)
    executable = shutil.which("tauri")
    if not executable:
        raise RuntimeError("未找到 Tauri CLI；请先安装项目依赖")
    if arguments and arguments[0] == "dev":
        os.environ["COPYRIGHT_AGENT_DATA_DIR"] = str(installed_app_data_dir())
    os.execv(executable, [executable, *arguments])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
