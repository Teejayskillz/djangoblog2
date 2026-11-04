"""
Structural syntax checker for the WordPress plugin's PHP/JS/CSS files.

Run:  python wordpress-plugin/check-php-structure.py

There is no PHP runtime on this build machine (and no network to install one),
so this script performs the strongest check that is possible offline:

* PHP files start with ``<?php``; non-template PHP files contain no closing ``?>``
  (templates legitimately switch between PHP and HTML, so only their PHP blocks
  are parsed there),
* PHP/JS/CSS comments and strings are tokenised so braces/parentheses/brackets
  can be counted outside of them,
* nesting order is validated (not only totals), with line numbers on failure,
* the plugin header is present in the main file,
* every ``require_once`` target exists and the expected file list is complete.

It is not a substitute for ``php -l``; it catches the common breakages
(unbalanced braces/parentheses/quotes, stray closing tags, missing includes).
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE / "nzdworld-link-generator"
if not ROOT.is_dir():  # script kept in the project root/tmp for ad-hoc runs
    ROOT = HERE.parent / "wordpress-plugin" / "nzdworld-link-generator"
PAIRS = {"{": "}", "(": ")", "[": "]"}
JS_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")


def _skip_line(text: str, i: int) -> int:
    while i < len(text) and text[i] != "\n":
        i += 1
    return i


def _skip_block_comment(text: str, i: int):
    i += 2
    while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
        i += 1
    return i + 2


def _skip_string(text: str, i: int, quote: str):
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def _skip_regex(text: str, i: int) -> int:
    """Skip a JavaScript regex literal starting at '/', honouring char classes."""
    i += 1
    in_class = False
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < len(text) and text[i].isalpha():
                i += 1
            return i
        elif ch == "\n":
            return i
        i += 1
    return i


def tokenise(text: str, mode: str = "php"):
    """Yield (char, line) for code characters, skipping comments/strings/regex."""
    i = 0
    line = 1
    length = len(text)
    last_significant = ""

    while i < length:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < length else ""

        if ch == "\n":
            line += 1
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i = _skip_line(text, i)
            continue
        if ch == "/" and nxt == "*":
            i = _skip_block_comment(text, i)
            continue
        if mode == "php" and ch == "#":
            i = _skip_line(text, i)
            continue

        if ch in ("'", '"', "`"):
            start_line = line
            new_i = _skip_string(text, i, ch)
            line += text.count("\n", i, new_i)
            i = new_i
            last_significant = "string" if mode == "js" and ch != "`" else last_significant
            continue

        if mode == "js" and ch == "/":
            # A '/' starts a regex literal unless it follows an operand.
            if last_significant == "" or last_significant in JS_REGEX_PRECEDERS:
                new_i = _skip_regex(text, i)
                line += text.count("\n", i, new_i)
                i = new_i
                last_significant = "regex"
                continue

        if not ch.isspace():
            last_significant = ch

        yield ch, line
        i += 1


def check_block(name: str, text: str, mode: str):
    stack = []
    for ch, line in tokenise(text, mode):
        if ch in PAIRS:
            stack.append((ch, line))
        elif ch in PAIRS.values():
            if not stack:
                return f"{name}: unexpected '{ch}' at line {line}"
            opener, opened_at = stack.pop()
            if PAIRS[opener] != ch:
                return f"{name}: '{ch}' at line {line} closes '{opener}' opened at line {opened_at}"
    if stack:
        opener, opened_at = stack[-1]
        return f"{name}: unclosed '{opener}' opened at line {opened_at}"
    return None


PHP_BLOCK = re.compile(r"<\?php(.*?)(?:\?>|$)", re.DOTALL)


def main() -> int:
    problems = []
    checked = 0

    if not ROOT.is_dir():
        print(f"plugin directory missing: {ROOT}")
        return 2

    php_files = sorted(ROOT.rglob("*.php"))
    if not php_files:
        print("no PHP files found")
        return 2

    for path in php_files:
        rel = path.relative_to(ROOT)
        rel_posix = str(rel).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        checked += 1

        if not text.startswith("<?php"):
            problems.append(f"{rel_posix}: does not start with <?php")

        if rel_posix.startswith("templates/"):
            # Templates mix PHP and HTML: parse each PHP block separately.
            for index, match in enumerate(PHP_BLOCK.finditer(text), 1):
                block = match.group(1)
                offset_line = text.count("\n", 0, match.start())
                issue = check_block(f"{rel_posix} (php block {index}, line {offset_line})", block, "php")
                if issue:
                    problems.append(issue)
            if not PHP_BLOCK.search(text):
                problems.append(f"{rel_posix}: template contains no PHP block")
        else:
            if "?>" in text:
                for number, line in enumerate(text.splitlines(), 1):
                    if "?>" in line:
                        problems.append(f"{rel_posix}:{number}: contains a closing ?> tag")
            issue = check_block(rel_posix, text, "php")
            if issue:
                problems.append(issue)

    for path in sorted(list(ROOT.rglob("*.js")) + list(ROOT.rglob("*.css"))):
        rel = path.relative_to(ROOT)
        rel_posix = str(rel).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        checked += 1
        mode = "js" if rel_posix.endswith(".js") else "css"
        issue = check_block(rel_posix, text, mode)
        if issue:
            problems.append(issue)

    main_file = ROOT / "nzdworld-link-generator.php"
    main_text = main_file.read_text(encoding="utf-8")
    for header in ("Plugin Name:", "Version:", "License:", "Text Domain:"):
        if header not in main_text:
            problems.append(f"main plugin file is missing the '{header}' header line")

    for match in re.finditer(r"require_once\s+NZD_LG_DIR\s*\.\s*'([^']+)'", main_text):
        target = ROOT / match.group(1)
        if not target.is_file():
            problems.append(f"require_once target missing: {match.group(1)}")

    expected = {
        "nzdworld-link-generator.php",
        "uninstall.php",
        "readme.txt",
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
    }
    present = {str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("*") if p.is_file()}
    for missing in sorted(expected - present):
        problems.append(f"expected plugin file missing: {missing}")

    print(f"checked {checked} PHP/JS/CSS files in {ROOT}")
    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f" - {problem}")
        return 1

    print("no structural problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
