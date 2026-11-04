#!/usr/bin/env python3
"""
Build the installable WordPress plugin ZIP.

Windows' Compress-Archive writes backslash path separators, which PHP's
ZipArchive (used by WordPress when installing an uploaded plugin) interprets as
part of the file name - the plugin then extracts into one broken folder. This
script always writes forward-slash entry names, so the resulting ZIP installs
correctly with Dashboard -> Plugins -> Add New -> Upload Plugin.

Usage (from the repository root):

    python wordpress-plugin/package-plugin.py

Output: nzdworld-link-generator.zip next to the repository root.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PLUGIN_DIR = HERE / "nzdworld-link-generator"
ZIP_PATH = HERE.parent / "nzdworld-link-generator.zip"
PLUGIN_SLUG = "nzdworld-link-generator"

REQUIRED = [
    "nzdworld-link-generator.php",
    "readme.txt",
    "uninstall.php",
    "includes/class-nzd-lg-options.php",
    "includes/class-nzd-lg-crypto.php",
    "includes/class-nzd-lg-sessions.php",
    "includes/class-nzd-lg-api-client.php",
    "includes/class-nzd-lg-entry.php",
    "includes/class-nzd-lg-frontend.php",
    "includes/class-nzd-lg-admin.php",
    "includes/class-nzd-lg-activator.php",
    "includes/class-nzd-lg-plugin.php",
    "templates/modal.php",
    "templates/admin-settings.php",
    "assets/css/generator.css",
    "assets/js/generator.js",
    "tests/run-tests.php",
]

SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".zip", ".bak", ".orig", ".rej", ".tmp", ".log"}


def iter_plugin_files():
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(PLUGIN_DIR)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path, rel.as_posix()


def main() -> int:
    if not PLUGIN_DIR.is_dir():
        print(f"missing plugin directory: {PLUGIN_DIR}")
        return 2

    files = list(iter_plugin_files())
    present = {rel for _path, rel in files}
    missing = [rel for rel in REQUIRED if rel not in present]
    if missing:
        print("missing required plugin files:")
        for rel in missing:
            print(f" - {rel}")
        return 2

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # Directory entries keep the archive tidy when extracted by hand.
        directories = sorted({str(pathlib.PurePosixPath(rel).parent) for _p, rel in files})
        for rel_dir in directories:
            if rel_dir == ".":
                continue
            info = zipfile.ZipInfo(f"{PLUGIN_SLUG}/{rel_dir}/")
            info.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(info, "")

        for path, rel in files:
            arcname = f"{PLUGIN_SLUG}/{rel}"
            info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())

    # --- verify ---------------------------------------------------------
    problems = []
    with zipfile.ZipFile(ZIP_PATH) as archive:
        bad = archive.testzip()
        if bad:
            problems.append(f"corrupt entry: {bad}")

        names = [entry.filename for entry in archive.infolist()]
        for name in names:
            if "\\" in name:
                problems.append(f"backslash separator in entry: {name}")
            if not name.startswith(f"{PLUGIN_SLUG}/"):
                problems.append(f"entry outside the plugin folder: {name}")

        file_entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        for required in REQUIRED:
            if f"{PLUGIN_SLUG}/{required}" not in names:
                problems.append(f"required entry missing from ZIP: {required}")

        for entry in file_entries:
            source = PLUGIN_DIR / entry.filename[len(PLUGIN_SLUG) + 1 :]
            if not source.is_file():
                problems.append(f"entry has no source file: {entry.filename}")
                continue
            if archive.read(entry) != source.read_bytes():
                problems.append(f"content mismatch: {entry.filename}")

        first = archive.read(f"{PLUGIN_SLUG}/nzdworld-link-generator.php")[:4096]
        if b"Plugin Name:" not in first:
            problems.append("main plugin file has no 'Plugin Name:' header")

    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    print(f"built {ZIP_PATH.name} ({ZIP_PATH.stat().st_size} bytes, {len(files)} files)")
    print(f"sha256 {digest}")

    if problems:
        print("\nZIP verification failed:")
        for problem in problems:
            print(f" - {problem}")
        return 1

    print("ZIP verified: forward-slash paths, plugin root folder, all required files, contents match source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
