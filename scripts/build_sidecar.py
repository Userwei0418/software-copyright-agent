#!/usr/bin/env python3
"""Build the Python sidecar using Tauri's external-binary naming convention."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT = ROOT / "scripts" / "sidecar_entry.py"
OUTPUT_DIR = ROOT / "src-tauri" / "binaries"
BINARY_BASENAME = "copyright-agent-sidecar"


def detect_target_triple() -> str:
    rustc = shutil.which("rustc")
    if rustc:
        result = subprocess.run(
            [rustc, "-vV"], check=True, capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.startswith("host: "):
                return line.removeprefix("host: ").strip()
        raise RuntimeError("rustc did not report a host target triple")

    machine = platform.machine().lower()
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine)
    if not arch:
        raise RuntimeError(f"unsupported CPU architecture without rustc: {machine}")
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform == "win32":
        return f"{arch}-pc-windows-msvc"
    if sys.platform.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"
    raise RuntimeError(f"unsupported platform without rustc: {sys.platform}")


def artifact_name(target_triple: str) -> str:
    suffix = ".exe" if "windows" in target_triple else ""
    return f"{BINARY_BASENAME}-{target_triple}{suffix}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(target_triple: str, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / artifact_name(target_triple)
    docx_spec = importlib.util.find_spec("docx")
    if docx_spec is None or docx_spec.origin is None:
        raise RuntimeError("python-docx is required to build the sidecar")
    docx_parts = Path(docx_spec.origin).resolve().parent / "parts"
    if not docx_parts.is_dir():
        raise RuntimeError(f"python-docx parts directory not found: {docx_parts}")
    with tempfile.TemporaryDirectory(prefix="copyright-agent-sidecar-") as temporary:
        temporary_path = Path(temporary)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            BINARY_BASENAME,
            "--distpath",
            str(temporary_path / "dist"),
            "--workpath",
            str(temporary_path / "work"),
            "--specpath",
            str(temporary_path / "spec"),
            "--collect-data",
            "software_copyright_agent",
            "--collect-data",
            "docx",
            # python-docx resolves template paths through ``docx/parts/..``.
            # PyInstaller otherwise keeps Python modules in its in-memory archive,
            # so the intermediate directory is absent in one-file builds.
            "--add-data",
            f"{docx_parts}{os.pathsep}docx/parts",
            str(ENTRY_POINT),
        ]
        environment = os.environ.copy()
        source_path = str(ROOT / "src")
        existing_python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path if not existing_python_path
            else os.pathsep.join([source_path, existing_python_path])
        )
        environment["PYINSTALLER_CONFIG_DIR"] = str(temporary_path / "config")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        built_name = BINARY_BASENAME + (".exe" if sys.platform == "win32" else "")
        built_path = temporary_path / "dist" / built_name
        if not built_path.is_file() or built_path.stat().st_size == 0:
            raise RuntimeError("PyInstaller did not produce the expected executable")
        shutil.copy2(built_path, final_path)
    metadata = {
        "artifact": str(final_path.relative_to(ROOT)),
        "target_triple": target_triple,
        "sha256": sha256(final_path),
        "size_bytes": final_path.stat().st_size,
    }
    manifest = output_dir / f"{final_path.name}.json"
    manifest.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-triple", help="Override the detected Rust target triple")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target_triple = args.target_triple or detect_target_triple()
    if args.dry_run:
        print(json.dumps({"target_triple": target_triple,
                          "artifact": artifact_name(target_triple)}, sort_keys=True))
        return 0
    print(json.dumps(build(target_triple, args.output_dir.resolve()),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
