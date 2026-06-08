#!/usr/bin/env python3
"""check_bat.py — lint / auto-fix a Windows .bat for the defects that
actually break double-clicked batch files (verified empirically on Win11 cmd).

Hard errors (exit 1): UTF-8 BOM, CR-only ("old Mac") line endings.
Warnings:             LF-only / mixed endings, non-ASCII text without `chcp 65001`.
Heuristic hints:      for /f single-percent var, unescaped ) after text inside a
                      block, bare .bat invocation without `call`, no cd/pushd %~dp0 anchor.

Usage:
    python check_bat.py path\\to\\file.bat [more.bat ...]
    python check_bat.py --fix path\\to\\file.bat      # strip BOM + normalize to CRLF in place
    python check_bat.py --strict ...                  # treat warnings as failures too

Only --fix writes anything, and it only touches the MECHANICAL issues
(BOM + line endings). Content issues (escaping, delayed expansion, paths)
are judgment calls you must fix by hand — see the windows-bat-guardrails skill.

Vendored from the global windows-bat-guardrails skill so CI does not depend on a
machine-local skill path. Keep in sync if the skill's checker changes.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"


def classify_endings(body: bytes) -> str:
    """Return 'crlf' | 'lf' | 'cr' | 'mixed' | 'none' for the byte body (BOM stripped)."""
    crlf = body.count(b"\r\n")
    lone_lf = body.count(b"\n") - crlf
    lone_cr = body.count(b"\r") - crlf
    kinds = [k for k, n in (("crlf", crlf), ("lf", lone_lf), ("cr", lone_cr)) if n > 0]
    if not kinds:
        return "none"
    if len(kinds) > 1:
        return "mixed"
    return kinds[0]


def decode_lines(body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    # normalize for line-by-line content scanning
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def strip_escaped_parens(s: str) -> str:
    """Remove ^( and ^) so naive paren tracking ignores escaped (literal) parens."""
    return s.replace("^(", "").replace("^)", "")


COMMENT_RE = re.compile(r"^\s*(rem\b|::|@?echo\b|set\b|:|@?title\b|goto\b)", re.IGNORECASE)
# a line that is essentially just a path-to-.bat (optionally with args)
BARE_BAT_RE = re.compile(r'^"?[\w.%~\\/+-]+\.bat"?(\s+\S.*)?$', re.IGNORECASE)


def check_content(lines: list[str]) -> list[tuple[str, int, str]]:
    """Best-effort heuristic content checks. Returns (level, lineno, message)."""
    findings: list[tuple[str, int, str]] = []
    joined_lower = "\n".join(lines).lower()

    has_anchor = bool(re.search(r'(cd\s+/d|pushd)\s+"?%~dp0', joined_lower))
    has_delayed = "enabledelayedexpansion" in joined_lower

    # Block-depth tracker. cmd opens a (...) block with a '(' in COMMAND position
    # (typically at end of an `if`/`for`/`) else (` line) and closes with a ')' at
    # the START of a line. Parens *inside* an echo argument do NOT open a block
    # (verified empirically) so we deliberately track only line-edge parens.
    depth = 0
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        low = line.lower()
        edge = strip_escaped_parens(line).rstrip()

        # --- for /f single-percent loop variable (needs %%X in a .bat) ---
        if re.search(r"\bfor\b", low) and " in " in low:
            if re.search(r"(?<!%)%[A-Za-z]\b", line):
                findings.append((
                    "HINT", i,
                    "for-loop variable looks single-percent (%X); in a .bat it must be %%X. "
                    f"line: {line}",
                ))

        # --- unescaped ) in an echo INSIDE a block ---
        # Inside a block, ANY unescaped ) ends the block: crash if text follows it,
        # silently-dropped char if it is the last token. Escape every paren as ^( ^).
        if depth > 0 and low.startswith("echo") and re.search(r"(?<!\^)\)", line):
            findings.append((
                "HINT", i,
                "echo inside a (...) block has an unescaped ')'. cmd treats it as the block "
                "close — it crashes if text follows, or drops the char if last. Escape as ^). "
                f"line: {line}",
            ))

        # --- %errorlevel% read inside a block without delayed expansion ---
        if depth > 0 and "%errorlevel%" in low and not has_delayed:
            findings.append((
                "HINT", i,
                "%errorlevel% inside a (...) block is the block-START value (parse-time). Use "
                "`setlocal enabledelayedexpansion` + !errorlevel!, or `if errorlevel N`. "
                f"line: {line}",
            ))

        # --- bare .bat invocation (implicit goto, never returns) ---
        if depth == 0 and not COMMENT_RE.match(line) and BARE_BAT_RE.match(line):
            if not re.match(r"^(call|start|cmd)\b", low):
                findings.append((
                    "HINT", i,
                    "bare .bat invocation transfers control and never returns — prefix with `call`. "
                    f"line: {line}",
                ))

        # update block depth using only line-edge parens
        if edge.endswith("("):
            depth += 1
        if line.startswith(")"):
            depth -= 1
        if depth < 0:
            depth = 0

    # Only warn about a missing anchor when the script actually depends on cwd
    # (relative `python -m` / .venv / npm / pip) AND never uses %~dp0 for its paths.
    cwd_dep = re.search(r"python\s+-m|\\\.venv\\|\bnpm\b|\bpip\b|-m\s+scripts", joined_lower)
    uses_dp0 = "%~dp0" in joined_lower
    if not has_anchor and cwd_dep and not uses_dp0:
        findings.append((
            "HINT", 0,
            "no `cd /d \"%~dp0\"` (or `pushd \"%~dp0\"`) anchor, but the script runs cwd-relative "
            "commands. A double-clicked or scheduled .bat may run from C:\\Windows\\System32, so "
            "relative paths / `python -m` break. (Exception: a sub-script whose caller cd's first.)",
        ))

    return findings


def check_file(path: Path, strict: bool) -> int:
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM)
    body = raw[len(BOM):] if has_bom else raw
    endings = classify_endings(body)
    lines = decode_lines(body)

    errors: list[str] = []
    warns: list[str] = []
    hints: list[str] = []

    if has_bom:
        errors.append("UTF-8 BOM at start of file — cmd glues it onto the first token "
                      "(`'<BOM>@echo' is not recognized`). Save as UTF-8 WITHOUT BOM. (fix: --fix)")
    if endings == "cr":
        errors.append("CR-only line endings — cmd reads the whole file as ONE line (it will not run). "
                      "Convert to CRLF. (fix: --fix)")
    elif endings in ("lf", "mixed", "none"):
        warns.append(f"line endings = {endings}; Windows .bat convention is CRLF. (fix: --fix)")

    body_text = body.decode("utf-8", errors="replace")
    # Only non-ASCII in *echoed* text causes mojibake; ignore non-ASCII that lives
    # solely in rem/:: comment lines (never printed).
    def is_comment(s: str) -> bool:
        return bool(re.match(r"^\s*(rem\b|::)", s, re.IGNORECASE))
    nonascii_output = any(any(ord(c) > 127 for c in l) for l in lines if not is_comment(l))
    if nonascii_output and "chcp 65001" not in body_text.lower():
        warns.append("file echoes non-ASCII text but has no `chcp 65001` — console output will be "
                     "mojibake. Add `chcp 65001 >nul` after `@echo off` (and keep the file UTF-8, no BOM).")

    for level, lineno, msg in check_content(lines):
        loc = f"line {lineno}: " if lineno else ""
        hints.append(loc + msg)

    label = str(path)
    print(f"\n=== {label} ===")
    print(f"    endings={endings}  bom={'YES' if has_bom else 'no'}  "
          f"chcp={'yes' if 'chcp 65001' in body_text.lower() else 'no'}")
    for e in errors:
        print(f"  [ERROR] {e}")
    for w in warns:
        print(f"  [WARN ] {w}")
    for h in hints:
        print(f"  [HINT ] {h}")
    if not (errors or warns or hints):
        print("  [OK] no issues found")

    if errors:
        return 1
    if strict and warns:
        return 1
    return 0


def fix_file(path: Path) -> None:
    raw = path.read_bytes()
    body = raw[len(BOM):] if raw.startswith(BOM) else raw
    # normalize every ending to LF first, then to CRLF
    normalized = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(normalized)
    print(f"[fixed] {path}: stripped BOM (if any) + normalized to CRLF")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Lint / auto-fix Windows .bat files.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--fix", action="store_true", help="strip BOM + normalize to CRLF in place")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args(argv)

    rc = 0
    for f in args.files:
        if not f.exists():
            print(f"[ERROR] not found: {f}")
            rc = 1
            continue
        if args.fix:
            fix_file(f)
        rc |= check_file(f, args.strict)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
