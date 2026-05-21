#!/usr/bin/env python3
"""Warn-only audit for likely N+1 IO inside Python loops.

Scans Python files under backend/ and scripts/ for short-range loop patterns
from codegraph C5 / performance finding P-4. The audit is intentionally
text-based and conservative: findings are review queues, not CI blockers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN_ROOTS = (REPO_ROOT / "backend", REPO_ROOT / "scripts")
RESULTS_PATH = REPO_ROOT / "backend" / "scripts" / "audit_n_plus_one_results.json"
REPORT_PATH = REPO_ROOT / "backend" / "scripts" / "audit_n_plus_one_report.md"
DEFAULT_EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".optuna",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "Users",
    "tests",
}

P4_BASELINE_FINDINGS = 19
LOOKAHEAD_LINES = 5
KNOWN_FIXED_COMMIT = "76750c85"
KNOWN_FIXED_PATH_SUFFIX = "backend/services/labels/build.py"
KNOWN_FIXED_LINE_MIN = 293
KNOWN_FIXED_LINE_MAX = 313

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

FOR_LOOP_RE = re.compile(r"^(\s*)for\b.*:\s*(?:#.*)?$")
ITERROWS_RE = re.compile(r"\.iterrows\s*\(")
CHUNKED_RANGE_LOOP_RE = re.compile(
    r"\bfor\s+\w+\s+in\s+range\s*\(\s*0\s*,\s*len\s*\([^)]+\)\s*,\s*(?:\d+|[A-Z_][A-Z0-9_]*)\s*\)\s*:"
)
SCHEMA_STATEMENT_LOOP_RE = re.compile(
    r"\bfor\s+(?P<var>\w+)\s+in\s+"
    r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\.strip\s*\(\s*\))?\.split\s*\(\s*[\"'];[\"']\s*\)|"
    r"[A-Z_]*(?:DDL|MIGRATION|MIGRATIONS)[A-Z_]*)\s*:"
)

PATTERN1_SQL_RE = re.compile(
    r"\bconn\.execute\s*\(|\bcursor\.execute\s*\(|\bexecutemany\s*\("
)
PATTERN2_IO_RE = re.compile(
    r"\bpd\.read_sql\s*\(|\bread_parquet\s*\(|\brequests\.(?:get|post)\s*\("
)
PATTERN3_CONNECT_RE = re.compile(r"\bduck_connect\s*\(|\bduckdb\.connect\s*\(")

GENERIC_EXECUTE_RE = re.compile(r"\b(?!conn\b|cursor\b)[A-Za-z_][A-Za-z0-9_]*\.execute\s*\(")
READ_ONLY_SQL_RE = re.compile(
    r"\b(?:SELECT|WITH|DESCRIBE|SHOW|PRAGMA)\b",
    flags=re.IGNORECASE,
)
ITERROWS_IO_RE = re.compile(
    r"\b(?:conn|cursor|[A-Za-z_][A-Za-z0-9_]*)\.execute\s*\("
    r"|\bexecutemany\s*\("
    r"|\bpd\.read_sql\s*\("
    r"|\bread_parquet\s*\("
    r"|\brequests\.(?:get|post)\s*\("
    r"|\bopen\s*\("
    r"|\.read_text\s*\("
    r"|\.write_text\s*\("
    r"|\bread_csv\s*\("
    r"|\bto_csv\s*\("
    r"|\bto_parquet\s*\("
)


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    pattern: str
    severity: str
    snippet: str
    suggested_fix: str


def iter_python_files(roots: Sequence[Path], *, include_tests: bool = False) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            if not _is_excluded_path(root, include_tests=include_tests):
                files.append(root)
            continue
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if _is_excluded_path(path, include_tests=include_tests):
                continue
            files.append(path)
    return sorted(set(files))


def scan_files(files: Iterable[Path], repo_root: Path | None = None) -> list[Finding]:
    base = repo_root or REPO_ROOT
    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()
    for path in sorted(Path(p) for p in files):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        rel_path = _relative_path(path, base)
        file_findings = scan_text(text, rel_path)
        for finding in file_findings:
            key = (finding.file, finding.line, finding.pattern)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return sort_findings(findings)


def scan_text(text: str, rel_path: str) -> list[Finding]:
    lines = text.splitlines()
    findings: list[Finding] = []
    high_lines: set[int] = set()

    for idx, line in enumerate(lines):
        loop_match = FOR_LOOP_RE.match(line)
        if not loop_match:
            continue
        body = _body_window(lines, idx, _indent_width(loop_match.group(1)))
        for body_idx, body_line in body:
            if _is_comment_only(body_line):
                continue
            if PATTERN3_CONNECT_RE.search(body_line):
                findings.append(
                    _finding(
                        rel_path,
                        body_idx,
                        "DB_CONNECT_IN_FOR_LOOP",
                        "HIGH",
                        _snippet(lines[idx], body_line),
                        "Open one DuckDB connection before the loop and reuse it; close it after the loop.",
                    )
                )
                high_lines.add(body_idx)
            if PATTERN1_SQL_RE.search(body_line):
                if _is_chunked_batch_loop(lines[idx], body_line):
                    continue
                if _is_schema_statement_loop(lines[idx], body_line):
                    continue
                findings.append(
                    _finding(
                        rel_path,
                        body_idx,
                        "SQL_EXECUTE_IN_FOR_LOOP",
                        "HIGH",
                        _snippet(lines[idx], body_line),
                        "Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL.",
                    )
                )
                high_lines.add(body_idx)
            if PATTERN2_IO_RE.search(body_line):
                findings.append(
                    _finding(
                        rel_path,
                        body_idx,
                        "PER_ROW_IO_IN_FOR_LOOP",
                        "MEDIUM",
                        _snippet(lines[idx], body_line),
                        "Preload file/HTTP data before the loop, cache it, or batch requests outside per-row iteration.",
                    )
                )

        for body_idx, body_line in body:
            if body_idx in high_lines or _is_comment_only(body_line):
                continue
            if not GENERIC_EXECUTE_RE.search(body_line):
                continue
            if not _looks_read_only(body_line, lines, body_idx):
                continue
            findings.append(
                _finding(
                    rel_path,
                    body_idx,
                    "READ_ONLY_QUERY_IN_FOR_LOOP",
                    "LOW",
                    _snippet(lines[idx], body_line),
                    "Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration.",
                )
            )

    for idx, line in enumerate(lines):
        if not ITERROWS_RE.search(line) or _is_comment_only(line):
            continue
        indent = _indent_width(line) if not FOR_LOOP_RE.match(line) else _indent_width(FOR_LOOP_RE.match(line).group(1))
        body = _body_window(lines, idx, indent)
        for body_idx, body_line in body:
            if _is_comment_only(body_line):
                continue
            if ITERROWS_IO_RE.search(body_line):
                findings.append(
                    _finding(
                        rel_path,
                        body_idx,
                        "ITERROWS_WITH_IO",
                        "MEDIUM",
                        _snippet(line, body_line),
                        "Replace iterrows with vectorized operations or collect work items and perform SQL/HTTP/file IO in batches.",
                    )
                )
                break

    return sort_findings(_dedupe_findings(findings))


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file, f.line, f.pattern),
    )


def write_json(findings: Sequence[Finding], path: Path = RESULTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_markdown(
    findings: Sequence[Finding],
    path: Path = REPORT_PATH,
    top_n: int = 50,
    *,
    scanned_files: int | None = None,
    scope: str = "production-code (tests excluded)",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {severity: sum(1 for f in findings if f.severity == severity) for severity in ("HIGH", "MEDIUM", "LOW")}
    lines = [
        "# N+1 IO-in-loop Audit Report",
        "",
        f"- Total findings: {len(findings)}",
        f"- HIGH: {counts['HIGH']}",
        f"- MEDIUM: {counts['MEDIUM']}",
        f"- LOW: {counts['LOW']}",
        f"- P-4 baseline: {P4_BASELINE_FINDINGS}",
        f"- Scanned Python files: {scanned_files if scanned_files is not None else 'unknown'}",
        f"- Scope: {scope}",
        "- Mode: WARN-only",
        "",
        f"## Top {min(top_n, len(findings))} Findings",
        "",
        "| Severity | File | Line | Pattern | Suggested fix | Snippet |",
        "|---|---|---:|---|---|---|",
    ]
    for finding in findings[:top_n]:
        lines.append(
            "| {severity} | `{file}` | {line} | {pattern} | {fix} | `{snippet}` |".format(
                severity=finding.severity,
                file=_md_escape(finding.file),
                line=finding.line,
                pattern=_md_escape(finding.pattern),
                fix=_md_escape(finding.suggested_fix),
                snippet=_md_escape(finding.snippet),
            )
        )
    if not findings:
        lines.append("| - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Python loops for likely N+1 IO patterns.")
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        help="Root or Python file to scan. Defaults to backend/ and scripts/.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include backend/tests in the scan. Defaults to production code only.",
    )
    parser.add_argument("--json-out", type=Path, default=RESULTS_PATH)
    parser.add_argument("--md-out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    roots = tuple(args.root) if args.root else DEFAULT_SCAN_ROOTS
    files = iter_python_files(roots, include_tests=args.include_tests)
    findings = scan_files(files, REPO_ROOT)
    write_json(findings, args.json_out)
    write_markdown(
        findings,
        args.md_out,
        scanned_files=len(files),
        scope="production + tests" if args.include_tests else "production-code (tests excluded)",
    )

    counts = {severity: sum(1 for f in findings if f.severity == severity) for severity in ("HIGH", "MEDIUM", "LOW")}
    print(f"audit_n_plus_one: scanned {len(files)} Python files")
    print(
        "audit_n_plus_one: findings total={total} HIGH={high} MEDIUM={medium} LOW={low}".format(
            total=len(findings),
            high=counts["HIGH"],
            medium=counts["MEDIUM"],
            low=counts["LOW"],
        )
    )
    print(f"audit_n_plus_one: wrote {args.json_out}")
    print(f"audit_n_plus_one: wrote {args.md_out}")
    if len(findings) < P4_BASELINE_FINDINGS:
        print(
            f"WARN: total findings {len(findings)} < P-4 baseline {P4_BASELINE_FINDINGS}; baseline may be wrong"
        )
    else:
        print(f"WARN: total findings {len(findings)} >= P-4 baseline {P4_BASELINE_FINDINGS} (OK)")
    return 0


def _body_window(lines: Sequence[str], loop_idx: int, loop_indent: int) -> list[tuple[int, str]]:
    body: list[tuple[int, str]] = []
    for idx in range(loop_idx + 1, min(len(lines), loop_idx + LOOKAHEAD_LINES + 1)):
        line = lines[idx]
        if not line.strip():
            body.append((idx + 1, line))
            continue
        if _indent_width(line) <= loop_indent and not _is_comment_only(line):
            break
        body.append((idx + 1, line))
    return body


def _finding(
    rel_path: str,
    line: int,
    pattern: str,
    severity: str,
    snippet: str,
    suggested_fix: str,
) -> Finding:
    if _is_known_fixed(rel_path, line):
        pattern = f"KNOWN_FIXED: {pattern}"
        suggested_fix = f"KNOWN_FIXED commit {KNOWN_FIXED_COMMIT}: per-date loop already fixed; keep as baseline marker."
    return Finding(
        file=rel_path,
        line=line,
        pattern=pattern,
        severity=severity,
        snippet=snippet,
        suggested_fix=suggested_fix,
    )


def _dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    deduped: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()
    for finding in findings:
        key = (finding.file, finding.line, finding.pattern)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _looks_read_only(body_line: str, lines: Sequence[str], body_line_no: int) -> bool:
    context = body_line
    for idx in range(body_line_no - 1, min(len(lines), body_line_no + 2)):
        context += " " + lines[idx]
    return bool(READ_ONLY_SQL_RE.search(context))


def _relative_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_excluded_path(path: Path, *, include_tests: bool) -> bool:
    excluded = DEFAULT_EXCLUDED_PARTS if not include_tests else DEFAULT_EXCLUDED_PARTS - {"tests"}
    try:
        parts = path.resolve().relative_to(REPO_ROOT).parts
    except ValueError:
        parts = path.parts
    return any(part in excluded for part in parts)


def _is_chunked_batch_loop(loop_line: str, body_line: str) -> bool:
    if not CHUNKED_RANGE_LOOP_RE.search(loop_line):
        return False
    if "executemany" in body_line:
        return True
    return " IN " in body_line.upper() and "batch" in body_line


def _is_schema_statement_loop(loop_line: str, body_line: str) -> bool:
    match = SCHEMA_STATEMENT_LOOP_RE.search(loop_line)
    if not match:
        return False
    loop_var = match.group("var")
    if re.search(rf"\.execute\s*\(\s*{re.escape(loop_var)}\s*\)", body_line):
        return True
    return bool(re.search(r"\.execute\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)", body_line))


def _indent_width(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))


def _snippet(loop_line: str, hit_line: str) -> str:
    return " / ".join(part.strip() for part in (loop_line, hit_line) if part.strip())


def _is_comment_only(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_known_fixed(rel_path: str, line: int) -> bool:
    return (
        rel_path.endswith(KNOWN_FIXED_PATH_SUFFIX)
        and KNOWN_FIXED_LINE_MIN <= line <= KNOWN_FIXED_LINE_MAX
    )


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    sys.exit(main())
