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


def source_fingerprint() -> str:
    """Fingerprint every source/data file that PyInstaller embeds in the sidecar."""
    digest = hashlib.sha256()
    roots = [ROOT / "src" / "software_copyright_agent", ENTRY_POINT]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(path for path in root.rglob("*") if path.is_file()
                         and "__pycache__" not in path.parts
                         and path.suffix not in {".pyc", ".pyo"})
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


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
        "source_fingerprint": source_fingerprint(),
        "sha256": sha256(final_path),
        "size_bytes": final_path.stat().st_size,
    }
    manifest = output_dir / f"{final_path.name}.json"
    manifest.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def ensure_current(target_triple: str, output_dir: Path) -> dict[str, object]:
    final_path = output_dir / artifact_name(target_triple)
    manifest = output_dir / f"{final_path.name}.json"
    expected_fingerprint = source_fingerprint()
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        metadata = {}
    if (
        final_path.is_file()
        and final_path.stat().st_size > 0
        and metadata.get("source_fingerprint") == expected_fingerprint
        and metadata.get("sha256") == sha256(final_path)
    ):
        return {**metadata, "rebuilt": False}
    return {**build(target_triple, output_dir), "rebuilt": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-triple", help="Override the detected Rust target triple")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--if-stale", action="store_true",
                        help="Only rebuild when embedded sidecar sources changed")
    args = parser.parse_args(argv)
    if not args.dry_run and importlib.util.find_spec("PyInstaller") is None:
        candidates = [
            ROOT / ".venv" / "bin" / "python",
            ROOT / ".venv" / "Scripts" / "python.exe",
        ]
        project_python = next((path for path in candidates if path.is_file()), None)
        if project_python and Path(sys.executable) != project_python:
            os.execv(str(project_python), [str(project_python), str(Path(__file__).resolve()),
                                           *(argv if argv is not None else sys.argv[1:])])
        raise RuntimeError("PyInstaller is required to build the sidecar")
    target_triple = args.target_triple or detect_target_triple()
    if args.dry_run:
        print(json.dumps({"target_triple": target_triple,
                          "artifact": artifact_name(target_triple)}, sort_keys=True))
        return 0
    operation = ensure_current if args.if_stale else build
    print(json.dumps(operation(target_triple, args.output_dir.resolve()),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
