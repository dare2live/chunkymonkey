"""F10 「股东研究」 structured parser.

Turns the raw GBK-decoded text returned by ``get_company_info_content`` into
two record sets:

- ``holders``: one row per holder per share class per period, including
  exit rows from ``较上个报告期退出前十大流通股东`` / ``...退出前十大股东`` tables.
- ``periods``: per-period header metadata (cumulative holdings, A 股户数,
  延前期变化等).

The parser is pure: it only takes the F10 text plus the symbol/name; the
fetch helper (:func:`fetch_holders`) wraps a Quotes client so callers can
get a structured view in a single call.

The schema is intentionally minimal but lossless:

- ``shares_text`` keeps the raw display ("6.8128亿"), ``shares_approx`` is
  the best-effort integer (precision is lossy by design — the F10 text is
  rounded to the displayed unit).
- ``share_class`` is parsed from the inline ``占A股 / 占H股 / 占B股`` suffix
  in 流通股东 rows or from ``无限售A股 / 无限售H股 / 受限售A股`` etc. in
  全量股东 rows.
- A/H dual-listed holders appear as two rows with the same ``holder_name``
  but different ``share_class``; the parser merges the second leg back to
  the prior holder when the name cell is empty but 持股数 is filled.
- Continuation rows (empty name AND empty 持股数) are joined onto the
  prior holder's name.
- Exit rows carry ``is_exit_row=True`` and ``change_status='退出'``;
  ``shares_*`` reflect the LAST KNOWN value before exit.

The module avoids any network or file I/O so it can be tested entirely
against captured fixtures.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from tdxhub.consts import MARKET_BJ, MARKET_SH, MARKET_SZ
from tdxhub.logger import logger
from tdxhub.utils import get_stock_market

PIPE = "｜"
TABLE_TOP = re.compile(r"^[┌┬─]+┐\s*$")
TABLE_MID = re.compile(r"^[├┼─]+┤\s*$")
TABLE_BOTTOM = re.compile(r"^[└┴─]+┘\s*$")

# Format detection: TDX servers serve F10 in two distinct layouts.
FORMAT_A_MARKER = ("灵通V9.0", "港澳资讯")
FORMAT_B_MARKER = "通达信沪深京F10"


def detect_f10_format(text: str) -> str:
    """Return 'a' (灵通V9.0/港澳资讯, fullwidth ｜) or 'b' (通达信沪深京F10, halfwidth │)."""

    if not text:
        return "a"
    if FORMAT_B_MARKER in text:
        return "b"
    return "a"

PAGE_HEAD_RE = re.compile(
    r"(?:☆)?股东研究☆\s*◇(?P<code>\S+)\s+(?P<name>\S+)\s*更新日期：(?P<update_date>\d{4}-\d{2}-\d{2})◇"
)
PERIOD_HEAD_RE = re.compile(
    r"截至日期：(?P<report_date>\d{4}-\d{2}-\d{2})\s+(?P<table_kind>十大流通股东情况|十大股东情况)"
    r"\s*(?:A股户数:(?P<holder_count>[\d.,]+(?:万|亿)?)\s+户均流通股:(?P<avg_shares>[\d.,]+(?:万|亿)?))?"
)
PERIOD_STAT_RE = re.compile(
    r"累计持有:(?P<cumulative>[\d.,]+(?:万|亿)?)股,累计占(?:流通股|总股本)比例:(?P<ratio>[\d.,]+)%"
    r"(?:,较上期变化:(?P<delta>-?[\d.,]+(?:万|亿)?)股)?"
)
EXIT_HEAD_RE = re.compile(
    r"(?P<report_date>\d{4}-\d{2}-\d{2})较上个报告期退出前(?P<table_kind>十大流通股东|十大股东)有"
)
SHARE_CLASS_IN_RATIO_RE = re.compile(r"^(?P<ratio>-?[\d.]+)\s*占(?P<klass>[AHB])股$")
SHARE_NATURE_KIND_RE = re.compile(r"(?P<klass>[AHB])股")

# 增减 cell variants: 未变 / 新进 / 退出 / ↑NNN万 / ↓-NNN万 / -
ARROW_NUM_RE = re.compile(r"^[↑↓]?(?P<num>-?[\d.]+(?:万|亿)?)$")

UNIT_MAP = {"亿": 100_000_000, "万": 10_000, "股": 1, "": 1}
SCALED_UNIT_MAP = {
    "亿元": 100_000_000,
    "万元": 10_000,
    "亿": 100_000_000,
    "万": 10_000,
    "股": 1,
    "元": 1,
    "": 1,
}

ChinesePuncTrans = str.maketrans({"，": ",", "（": "(", "）": ")", "：": ":"})


@dataclass(frozen=True)
class _RawCell:
    text: str

    @property
    def is_blank(self) -> bool:
        return not self.text


def _to_int_shares(text: str) -> tuple[Optional[int], Optional[str]]:
    """Parse strings like ``6.8128亿`` into (681_280_000, '亿')."""

    if not text:
        return None, None
    raw = text.strip().replace(",", "").replace(" ", "")
    if raw in ("", "-"):
        return None, None
    m = re.match(r"^(-?\d+(?:\.\d+)?)(亿|万)?$", raw)
    if not m:
        return None, None
    val = float(m.group(1))
    unit = m.group(2) or ""
    return int(round(val * UNIT_MAP[unit])), (unit or "股")


def _to_scaled_int(text: str) -> tuple[Optional[int], Optional[str]]:
    """Parse shares or money display values into the base unit.

    TDX F10 tables reuse the same ``亿``/``万`` display convention for share
    counts and RMB amounts. The caller decides whether the returned integer
    represents shares or yuan.
    """

    if not text:
        return None, None
    raw = (
        text.strip()
        .replace(",", "")
        .replace("，", "")
        .replace(" ", "")
    )
    if raw in ("", "-", "---", "--", "—"):
        return None, None
    m = re.match(r"^(-?\d+(?:\.\d+)?)(亿元|万元|亿|万|股|元)?$", raw)
    if not m:
        return None, None
    val = float(m.group(1))
    unit = m.group(2) or ""
    return int(round(val * SCALED_UNIT_MAP[unit])), (unit or "base")


def _to_signed_scaled_int(text: str) -> Optional[int]:
    txt = (text or "").strip()
    if txt in ("不变", "未变"):
        return 0
    val, _unit = _to_scaled_int(txt)
    return val


def _to_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group(0)) if m else None


def _strip_cell(cell: str) -> str:
    return cell.strip().replace("　", " ").rstrip()


def _classify_change(raw: str) -> tuple[str, Optional[int]]:
    """Map raw 增减情况 cell text to ``(status, shares_delta)``.

    Returns one of: 新进, 增持, 减持, 不变, 退出, 未知.
    ``shares_delta`` is the parsed integer if present (positive for 增,
    negative for 减, zero for 未变), else None.
    """

    txt = (raw or "").strip()
    if not txt or txt == "-":
        return "未知", None
    if txt == "未变":
        return "不变", 0
    if txt == "新进":
        return "新进", None
    if txt == "退出":
        return "退出", None
    m = ARROW_NUM_RE.match(txt)
    if m:
        n, _unit = _to_int_shares(m.group("num"))
        if n is None:
            return "未知", None
        if n > 0 or txt.startswith("↑"):
            return "增持", abs(n) if txt.startswith("↑") else n
        if n < 0 or txt.startswith("↓"):
            return "减持", -abs(n) if txt.startswith("↓") else n
        return "不变", 0
    return "未知", None


def _split_pipe_row(line: str) -> list[str]:
    """Return cells from a ``｜a｜b｜...｜`` row, trimmed and unpadded."""

    if PIPE not in line:
        return []
    parts = line.split(PIPE)
    # Drop the leading and trailing fragments produced by the surrounding ｜
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [_strip_cell(p) for p in parts]


def _split_box_row(line: str) -> list[str]:
    """Return cells from a Format B ``│a│b│...│`` row."""

    if "│" not in line:
        return []
    parts = line.split("│")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [_strip_cell(p) for p in parts]


def _take_box_table_block(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Read a Format B box table that uses halfwidth ``│`` separators."""

    rows: list[list[str]] = []
    i = start
    n = len(lines)
    if i >= n or not TABLE_TOP.match(lines[i].rstrip()):
        return rows, i
    i += 1
    while i < n:
        line = lines[i].rstrip()
        if TABLE_BOTTOM.match(line):
            return rows, i + 1
        if TABLE_TOP.match(line) or TABLE_MID.match(line):
            i += 1
            continue
        if "│" in line:
            cells = _split_box_row(line)
            if cells:
                rows.append(cells)
            i += 1
            continue
        i += 1
    return rows, i


def _is_table_border(line: str) -> bool:
    s = line.rstrip()
    return bool(TABLE_TOP.match(s) or TABLE_MID.match(s) or TABLE_BOTTOM.match(s))


def _take_table_block(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Read a ┌...└ block starting at ``start`` (the ┌ row).

    Returns ``(rows, end_index)`` where ``rows`` is every pipe-row inside
    the block (title rows, column-header rows, data rows — caller decides
    how to classify). ``end_index`` points to the line AFTER the closing
    └. Inner ├ separators are skipped without resetting state.
    """

    rows: list[list[str]] = []
    i = start
    n = len(lines)
    if i >= n or not TABLE_TOP.match(lines[i].rstrip()):
        return rows, i
    i += 1
    while i < n:
        line = lines[i].rstrip()
        if TABLE_BOTTOM.match(line):
            return rows, i + 1
        if TABLE_TOP.match(line) or TABLE_MID.match(line):
            i += 1
            continue
        if PIPE in line:
            cells = _split_pipe_row(line)
            if cells:
                rows.append(cells)
            i += 1
            continue
        # Non-pipe text inside a block (rare); ignore.
        i += 1
    return rows, i


def _normalise_row_count(rows: list[list[str]]) -> list[list[str]]:
    """Pad each row to the max width seen so cell indices are stable."""

    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _is_header_row(cells: list[str]) -> bool:
    """Detect column-header rows of holder tables."""

    return bool(cells) and (
        cells[0].startswith("股东名称")
        or any("持股数" in c for c in cells[:3])
    )


def _parse_holder_table(rows: list[list[str]], *, is_exit: bool) -> list[dict[str, Any]]:
    """Parse a normalised list of pipe-cell rows into holder records.

    Skips column-header rows (``股东名称`` / ``持股数(股)`` etc.).
    Continuation rows (empty name + empty shares) are joined onto the
    prior record's name. A/H second-class rows (empty name + non-empty
    shares) become a new record that re-uses the prior name.
    """

    out: list[dict[str, Any]] = []
    if not rows:
        return out

    rows = [r for r in rows if not _is_header_row(r)]
    if not rows:
        return out

    width = max((len(r) for r in rows), default=0)
    if width < 5:
        return out

    rows = _normalise_row_count(rows)

    last_full_name: Optional[str] = None
    rank = 0
    row_seq = 0
    for cells in rows:
        name = cells[0]
        shares_cell = cells[1]
        ratio_cell = cells[2]
        type_cell = cells[3]
        change_cell = cells[4]

        # Continuation row: name empty, shares empty -> append leading name
        # fragment to the prior record's name. The fragment lives in the
        # NEXT row's name cell when wrapping; here we are dealing with the
        # NEXT row's continuation: cells[0] is the wrap chunk.
        if not name and not shares_cell:
            # Pure spacer row (no fragment); ignore.
            continue

        # A/H second class row: name empty, shares non-empty -> reuse name
        if not name and shares_cell:
            holder_name = last_full_name or ""
            row_seq += 1
            out.append(
                _build_holder_record(
                    rank=rank,
                    row_seq=row_seq,
                    name=holder_name,
                    shares_cell=shares_cell,
                    ratio_cell=ratio_cell,
                    type_cell=type_cell,
                    change_cell=change_cell,
                    is_exit_row=is_exit,
                    is_secondary_class=True,
                )
            )
            continue

        # Wrap continuation row: name non-empty but shares empty -> append
        # to the previous record's name and DO NOT bump rank.
        if name and not shares_cell and out:
            out[-1]["holder_name"] = (out[-1]["holder_name"] + name).strip()
            last_full_name = out[-1]["holder_name"]
            continue

        # Fresh holder row
        rank += 1
        row_seq += 1
        rec = _build_holder_record(
            rank=rank,
            row_seq=row_seq,
            name=name,
            shares_cell=shares_cell,
            ratio_cell=ratio_cell,
            type_cell=type_cell,
            change_cell=change_cell,
            is_exit_row=is_exit,
            is_secondary_class=False,
        )
        out.append(rec)
        last_full_name = rec["holder_name"]

    return out


def _build_holder_record(
    *,
    rank: int,
    row_seq: int,
    name: str,
    shares_cell: str,
    ratio_cell: str,
    type_cell: str,
    change_cell: str,
    is_exit_row: bool,
    is_secondary_class: bool,
) -> dict[str, Any]:
    shares_approx, shares_precision = _to_int_shares(shares_cell)
    klass = None
    ratio = None
    m = SHARE_CLASS_IN_RATIO_RE.match(ratio_cell.strip())
    if m:
        klass = m.group("klass")
        ratio = float(m.group("ratio"))
    else:
        # 全量股东 table: ratio is plain number, share class is in 股份性质 cell
        ratio = _to_float(ratio_cell)
        n = SHARE_NATURE_KIND_RE.search(type_cell)
        if n:
            klass = n.group("klass")
    status, change_shares = _classify_change(change_cell)
    return {
        "holder_rank": rank,
        "row_seq": row_seq,
        "holder_name": name,
        "share_class": klass,
        "shares_text": shares_cell,
        "shares_approx": shares_approx,
        "shares_precision": shares_precision,
        "hold_ratio": ratio,
        "holder_type_or_nature": type_cell,
        "change_status": status,
        "change_shares_text": change_cell,
        "change_shares_approx": change_shares,
        "is_exit_row": is_exit_row,
        "is_secondary_class": is_secondary_class,
    }


def _section_lines(text: str, section_num: int) -> list[str]:
    """Slice ``text`` to the lines belonging to a ``【<n>.…】`` section.

    The page header repeats the section title list (``★本栏包括…★``) so the
    first occurrence of ``【<n>.…】`` is part of the TOC line. Anchor on the
    heading at line start to avoid that.
    """

    pat = rf"^【{section_num}\.[^】]*】.*$"
    m = re.search(pat, text or "", re.MULTILINE)
    if m is None:
        return []
    rest = text[m.end():]
    nxt = re.search(r"^【\d+\.", rest, re.MULTILINE)
    if nxt is not None:
        rest = rest[: nxt.start()]
    return rest.splitlines()


def _section_heading(text: str, section_num: int) -> str:
    pat = rf"^【{section_num}\.[^】]*】.*$"
    m = re.search(pat, text or "", re.MULTILINE)
    return m.group(0) if m else ""


def _section_report_date_text(text: str, section_num: int) -> Optional[str]:
    heading = _section_heading(text, section_num)
    m = re.search(r"(?:截止日期|截至日期)[:：]\s*(\d{4}[.-]\d{1,2}[.-]\d{1,2})", heading)
    return m.group(1) if m else None


def _section_4_lines(text: str) -> list[str]:
    """Backwards-compatible alias for ``_section_lines(text, 4)``."""

    return _section_lines(text, 4)


def _market_str(symbol: str) -> str:
    market = int(get_stock_market(symbol, string=False))
    return {MARKET_SH: "SH", MARKET_SZ: "SZ", MARKET_BJ: "BJ"}.get(market, "")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def _records_with_columns(records: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [{column: record.get(column) for column in columns} for record in records]


def _classify_table(rows: list[list[str]]) -> tuple[str, list[list[str]]]:
    """Classify a ┌...└ block by its content.

    Returns one of:
    - ``("main", rows)`` — main 十大流通股东 / 十大股东 holder table
    - ``("exit", data_rows)`` — exit table; the title row is stripped
    - ``("unknown", [])`` — other layouts (controlling shareholder, plan,
      unknown title)
    """

    if not rows:
        return "unknown", []

    # Look for an exit-title row: a row with exactly one non-empty cell
    # whose text matches the EXIT_HEAD pattern.
    for idx, row in enumerate(rows):
        non_empty = [c for c in row if c]
        if len(non_empty) == 1 and EXIT_HEAD_RE.search(non_empty[0]):
            return "exit", rows[idx + 1 :]

    # Main holder table: contains a 股东名称 / 持股数 column-header row.
    if any(_is_header_row(r) for r in rows):
        return "main", rows

    return "unknown", []


def parse_holders(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse F10 「股东研究」 text into ``(holders, periods)`` records.

    Both record lists are empty if the section 4 block is missing or
    unparsable. The function never raises on malformed input.
    """

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    holder_records: list[dict[str, Any]] = []
    period_records: list[dict[str, Any]] = []

    lines = _section_4_lines(text or "")
    n = len(lines)
    i = 0
    current_period: Optional[dict[str, Any]] = None
    while i < n:
        line = lines[i].rstrip()

        head_m = PERIOD_HEAD_RE.search(line)
        if head_m:
            holder_count_text = head_m.group("holder_count")
            holder_count, _ = _to_int_shares(holder_count_text or "")
            avg_shares_text = head_m.group("avg_shares")
            avg_shares, _ = _to_int_shares(avg_shares_text or "")
            kind = head_m.group("table_kind")
            holder_set = "free" if kind == "十大流通股东情况" else "all"
            current_period = {
                "stock_code": page_code,
                "stock_name": page_name,
                "market": market,
                "report_date": _normalise_date(head_m.group("report_date")),
                "holder_set": holder_set,
                "a_share_holder_count_text": holder_count_text,
                "a_share_holder_count": holder_count,
                "avg_float_shares_text": avg_shares_text,
                "avg_float_shares": avg_shares,
                "cumulative_text": None,
                "cumulative_shares": None,
                "cumulative_ratio": None,
                "delta_vs_prev_text": None,
                "delta_vs_prev_shares": None,
                "page_update_date": page_update_date,
                "source": "tdx_f10",
                "raw_hash": raw_hash,
                "fetched_at": fetched_at,
            }
            # Attempt to consume the stat line on the very next non-blank line
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                stat_m = PERIOD_STAT_RE.search(lines[j])
                if stat_m:
                    cum_text = stat_m.group("cumulative")
                    cum_shares, _ = _to_int_shares(cum_text)
                    delta_text = stat_m.group("delta")
                    delta_shares, _ = _to_int_shares(delta_text or "")
                    current_period.update(
                        {
                            "cumulative_text": cum_text,
                            "cumulative_shares": cum_shares,
                            "cumulative_ratio": _to_float(stat_m.group("ratio")),
                            "delta_vs_prev_text": delta_text,
                            "delta_vs_prev_shares": delta_shares,
                        }
                    )
                    i = j + 1
                else:
                    i = j
            else:
                i = j
            period_records.append(dict(current_period))
            continue

        if TABLE_TOP.match(line):
            rows, end = _take_table_block(lines, i)
            kind, data = _classify_table(rows)
            if kind in ("main", "exit") and current_period is not None:
                is_exit = kind == "exit"
                for rec in _parse_holder_table(data, is_exit=is_exit):
                    rec.update(_period_keys(current_period))
                    holder_records.append(rec)
            i = end
            continue

        i += 1

    return (
        _records_with_columns(holder_records, _HOLDER_COLUMNS),
        _records_with_columns(period_records, _PERIOD_COLUMNS),
    )


def _period_keys(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": p.get("stock_code"),
        "stock_name": p.get("stock_name"),
        "market": p.get("market"),
        "report_date": p.get("report_date"),
        "holder_set": p.get("holder_set"),
        "page_update_date": p.get("page_update_date"),
        "source": p.get("source", "tdx_f10"),
        "raw_hash": p.get("raw_hash"),
        "fetched_at": p.get("fetched_at"),
    }


def _find_period(records: Iterable[dict[str, Any]], report_date: str, holder_set: str) -> Optional[dict[str, Any]]:
    for r in records:
        if r.get("report_date") == report_date and r.get("holder_set") == holder_set:
            return r
    return None


_HOLDER_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "report_date",
    "holder_set",
    "holder_rank",
    "row_seq",
    "holder_name",
    "share_class",
    "shares_text",
    "shares_approx",
    "shares_precision",
    "hold_ratio",
    "holder_type_or_nature",
    "change_status",
    "change_shares_text",
    "change_shares_approx",
    "is_exit_row",
    "is_secondary_class",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]

_PERIOD_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "report_date",
    "holder_set",
    "a_share_holder_count_text",
    "a_share_holder_count",
    "avg_float_shares_text",
    "avg_float_shares",
    "cumulative_text",
    "cumulative_shares",
    "cumulative_ratio",
    "delta_vs_prev_text",
    "delta_vs_prev_shares",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]


# ---------------------------------------------------------------------------
# Section 1 — 控股股东与实际控制人
# ---------------------------------------------------------------------------

# Some companies render this as "控股股东"; others (e.g. 601398 ICBC) use
# "第一大股东" when there is no formal controlling shareholder. Both values
# carry the same downstream semantics for our use case.
PRIMARY_LABELS = ("控股股东", "第一大股东")
ACTUAL_CONTROLLER_LABEL = "实际控制人"

# Auxiliary annotation prefixes seen inside parentheticals (...) or
# brackets 【...】 attached to a shareholder name. When a trailing paren
# block matches one of these prefixes it is stripped from the name.
_AUX_PREFIXES = (
    "联席股东",
    "控股比例",
    "持股比例",
    "上市公司",
    "一致行动",
)


def _walk_left_strip_ratio_paren(value: str) -> tuple[str, Optional[float]]:
    """Strip the parenthetical that wraps the LAST ``XX.XX%`` and return it.

    Handles two layouts simultaneously:
    - ``<name>(<ratio>%)`` — the trailing ``(`` immediately precedes the percentage.
    - ``<name>(<aux>:<ratio>%)`` — the percentage lives inside a larger paren
      block. Walking left while counting parens locates the outermost ``(``
      that wraps the percentage.
    """

    if not value:
        return "", None
    pct_matches = list(re.finditer(r"(-?\d+(?:\.\d+)?)%", value))
    if not pct_matches:
        return value.strip(), None
    last = pct_matches[-1]
    ratio = float(last.group(1))
    pos = last.start()
    depth = 1
    open_pos = -1
    j = pos - 1
    while j >= 0:
        c = value[j]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                open_pos = j
                break
        j -= 1
    if open_pos == -1:
        return value[:pos].rstrip(":：(（ "), ratio
    return value[:open_pos].rstrip(), ratio


_TRAILING_PAREN_RE = re.compile(r"[\(（](?P<inner>[^()（）]*)[\)）]\s*$")
_TRAILING_BRACKET_RE = re.compile(r"【(?P<inner>[^【】]*)】\s*$")


def _strip_aux_annotations(name: str) -> str:
    """Iteratively strip trailing ``(aux)`` / ``【aux】`` annotations."""

    s = name
    while True:
        s = s.strip()
        m = _TRAILING_PAREN_RE.search(s) or _TRAILING_BRACKET_RE.search(s)
        if m is None:
            return s
        inner = m.group("inner")
        if not any(inner.startswith(p) for p in _AUX_PREFIXES):
            return s
        s = s[: m.start()]


def _split_name_ratio(value: str) -> tuple[str, Optional[float]]:
    """Return ``(clean_name, ratio_pct)`` from an F10 段-1 value cell."""

    name, ratio = _walk_left_strip_ratio_paren(value)
    name = _strip_aux_annotations(name)
    return name, ratio


def parse_controlling_shareholder(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> Optional[dict[str, Any]]:
    """Parse F10 段 1 (控股股东与实际控制人).

    Returns ``None`` if the section is missing or shows ``暂无数据``.
    """

    if detect_f10_format(text) == "b":
        return parse_controlling_shareholder_format_b(
            text, symbol=symbol, stock_name=stock_name
        )

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 1)
    if not lines or any("暂无数据" in ln for ln in lines):
        return None

    rec: dict[str, Any] = {
        "stock_code": page_code,
        "stock_name": page_name,
        "market": market,
        "primary_shareholder_label": None,
        "primary_shareholder_name": None,
        "primary_shareholder_ratio": None,
        "primary_shareholder_raw": None,
        "actual_controller_name": None,
        "actual_controller_ratio": None,
        "actual_controller_raw": None,
        "page_update_date": page_update_date,
        "source": "tdx_f10",
        "raw_hash": raw_hash,
        "fetched_at": fetched_at,
    }

    i = 0
    n = len(lines)
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_table_block(lines, i)
            for row in rows:
                if len(row) < 2:
                    continue
                label = row[0].strip()
                value = row[1].strip()
                if not value:
                    continue
                name, ratio = _split_name_ratio(value)
                if label in PRIMARY_LABELS:
                    rec["primary_shareholder_label"] = label
                    rec["primary_shareholder_name"] = name
                    rec["primary_shareholder_ratio"] = ratio
                    rec["primary_shareholder_raw"] = value
                elif label == ACTUAL_CONTROLLER_LABEL:
                    rec["actual_controller_name"] = name
                    rec["actual_controller_ratio"] = ratio
                    rec["actual_controller_raw"] = value
            i = end
            continue
        i += 1

    if rec["primary_shareholder_name"] is None:
        return None
    return rec


def parse_controlling_shareholder_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> Optional[dict[str, Any]]:
    """Parse Format B 段 1, including the 控股关系 chain text."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 1)
    if not lines or any("暂无数据" in ln for ln in lines):
        return None

    rec: dict[str, Any] = {
        "stock_code": page_code,
        "stock_name": page_name,
        "market": market,
        "primary_shareholder_label": None,
        "primary_shareholder_name": None,
        "primary_shareholder_ratio": None,
        "primary_shareholder_raw": None,
        "actual_controller_name": None,
        "actual_controller_ratio": None,
        "actual_controller_raw": None,
        "control_chain_text": None,
        "page_update_date": page_update_date,
        "source": "tdx_f10",
        "raw_hash": raw_hash,
        "fetched_at": fetched_at,
    }

    i = 0
    n = len(lines)
    chain_parts: list[str] = []
    current_label: Optional[str] = None
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_box_table_block(lines, i)
            for row in rows:
                if len(row) < 2:
                    continue
                label = row[0].strip()
                value = row[1].strip()
                if label:
                    current_label = label
                if not value:
                    continue
                if (label or current_label) == "控股关系":
                    chain_parts.append(value)
                    continue
                name, ratio = _split_name_ratio(value)
                if label in PRIMARY_LABELS:
                    rec["primary_shareholder_label"] = label
                    rec["primary_shareholder_name"] = name
                    rec["primary_shareholder_ratio"] = ratio
                    rec["primary_shareholder_raw"] = value
                elif label == ACTUAL_CONTROLLER_LABEL:
                    rec["actual_controller_name"] = name
                    rec["actual_controller_ratio"] = ratio
                    rec["actual_controller_raw"] = value
            i = end
            continue
        i += 1

    if chain_parts:
        rec["control_chain_text"] = "\n".join(chain_parts)
    if rec["primary_shareholder_name"] is None and not rec.get("control_chain_text"):
        return None
    return rec


# ---------------------------------------------------------------------------
# Section 2 — 股东增减持计划
# ---------------------------------------------------------------------------

KNOWN_PLAN_LABELS = (
    "公告披露日",
    "行为主体",
    "变动方向",
    "进度",
    "起始日期",
    "截止日期",
    "预计增减持数量(股)",
    "占总股本比(%)",
    "增减持原因",
    "进展说明",
)


def _split_label_value(cell: str) -> tuple[Optional[str], str]:
    """Split ``label:value`` cell. Returns ``(label, value)`` or ``(None, cell)``.

    Recognises only the fixed labels in :data:`KNOWN_PLAN_LABELS` to avoid
    accidentally splitting on colons inside dates or proper nouns.
    """

    if not cell:
        return None, ""
    for lbl in KNOWN_PLAN_LABELS:
        prefix = lbl + ":"
        if cell.startswith(prefix):
            return lbl, cell[len(prefix):].strip()
    return None, cell


def _parse_one_plan(rows: list[list[str]]) -> dict[str, str]:
    """Parse one ┌-└ block into a label-keyed dict."""

    plan: dict[str, str] = {lbl: "" for lbl in KNOWN_PLAN_LABELS}
    current_label: Optional[str] = None
    for row in rows:
        for cell in row:
            text = cell.strip()
            if not text:
                continue
            label, value = _split_label_value(text)
            if label is not None:
                current_label = label
                plan[label] = value
            elif current_label is not None:
                # Continuation of the previous label's value
                plan[current_label] = (plan[current_label] + text).strip()
    return plan


def parse_shareholder_plans(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse F10 段 2 (股东增减持计划) into one record per plan."""

    if detect_f10_format(text) == "b":
        return parse_shareholder_plans_format_b(
            text, symbol=symbol, stock_name=stock_name
        )

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 2)
    plans: list[dict[str, Any]] = []
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    i = 0
    n = len(lines)
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_table_block(lines, i)
            plan = _parse_one_plan(rows)
            target_shares_raw = plan.get("预计增减持数量(股)", "")
            target_shares, _ = _to_int_shares(target_shares_raw)
            target_ratio_raw = plan.get("占总股本比(%)", "")
            target_ratio = _to_float(target_ratio_raw) if target_ratio_raw not in ("-", "") else None
            plans.append(
                {
                    "stock_code": page_code,
                    "stock_name": page_name,
                    "market": market,
                    "announce_date": _normalise_date(plan.get("公告披露日", "")),
                    "subject": plan.get("行为主体", ""),
                    "direction": plan.get("变动方向", ""),
                    "progress": plan.get("进度", ""),
                    "start_date": _normalise_date(plan.get("起始日期", "")),
                    "end_date": _normalise_date(plan.get("截止日期", "")),
                    "target_shares_text": target_shares_raw,
                    "target_shares": target_shares,
                    "target_ratio_text": target_ratio_raw,
                    "target_ratio": target_ratio,
                    "reason": plan.get("增减持原因", ""),
                    "narrative": plan.get("进展说明", ""),
                    "page_update_date": page_update_date,
                    "source": "tdx_f10",
                    "raw_hash": raw_hash,
                    "fetched_at": fetched_at,
                }
            )
            i = end
            continue
        i += 1

    return _records_with_columns(plans, _PLAN_COLUMNS)


_PLAN_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "announce_date",
    "latest_announce_date",
    "first_announce_date",
    "subject",
    "direction",
    "progress",
    "start_date",
    "end_date",
    "target_shares_min_text",
    "target_shares_min",
    "target_shares_text",
    "target_shares",
    "target_ratio_text",
    "target_ratio",
    "target_amount_min_text",
    "target_amount_min",
    "target_amount_max_text",
    "target_amount_max",
    "trade_method",
    "reason",
    "narrative",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]


FORMAT_B_PLAN_META_RE = re.compile(
    r"最新公告日期[:：](?P<announce>\d{4}[.-]\d{1,2}[.-]\d{1,2})[，,]\s*"
    r"变动方向[:：](?P<direction>[^，,]+)[，,]\s*进度[:：](?P<progress>\S+)"
)


def _normalise_plan_direction(raw: str) -> str:
    txt = (raw or "").strip()
    if "增持" in txt:
        return "增持计划"
    if "减持" in txt:
        return "减持计划"
    return txt


def _extract_format_b_plan_notes(notes: list[str]) -> dict[str, str]:
    joined = "".join(n.strip() for n in notes if n.strip())
    out = {
        "first_announce_date": "",
        "start_date": "",
        "end_date": "",
        "reason": "",
        "trade_method": "",
        "narrative": joined,
    }
    patterns = {
        "first_announce_date": r"首次公告披露日[:：]([^，,；;]*)",
        "start_date": r"起始变动日期[:：]([^，,；;]*)",
        "end_date": r"截止变动日期[:：]([^，,；;]*)",
        "reason": r"变动目的[:：]([^；;]*)",
        "trade_method": r"交易方式[:：]([^；;]*)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, joined)
        if m:
            out[key] = m.group(1).strip()
    return out


def _parse_format_b_plan_rows(rows: list[list[str]]) -> tuple[list[list[str]], list[str]]:
    """Return data rows and note lines from one Format B plan box table."""

    rows = _normalise_row_count(rows)
    data_rows: list[list[str]] = []
    notes: list[str] = []
    current: Optional[list[str]] = None
    in_notes = False
    for raw_cells in rows:
        cells = (raw_cells + [""] * 6)[:6]
        blob = "".join(c.strip() for c in raw_cells)
        if not blob:
            continue
        if "股东名称" in blob or "拟变动" in blob or blob in ("(股)股本比(%)(元)(元)",):
            continue
        if blob.startswith(("说明：", "变动目的：", "变动股份来源：", "交易方式：")):
            in_notes = True
            notes.append(blob)
            continue
        if in_notes:
            notes.append(blob)
            continue

        has_measure = any(c.strip() for c in cells[2:6])
        if has_measure:
            current = [c.strip() for c in cells]
            data_rows.append(current)
            continue
        if current is not None:
            if cells[0].strip():
                current[0] = (current[0] + cells[0].strip()).strip()
            if cells[1].strip():
                current[1] = (current[1] + cells[1].strip()).strip()
    return data_rows, notes


def parse_shareholder_plans_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse Format B 段 2 股东增减持计划.

    The Format B plan table is not label/value based: each plan starts with a
    metadata line (latest announcement, direction, progress), followed by a
    box table whose data row can wrap across several visual rows. Empty-shell
    rows are filtered out.
    """

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 2)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    plans: list[dict[str, Any]] = []
    meta: dict[str, str] = {"announce": "", "direction": "", "progress": ""}
    i = 0
    n = len(lines)
    while i < n:
        meta_m = FORMAT_B_PLAN_META_RE.search(lines[i])
        if meta_m:
            meta = meta_m.groupdict()
            i += 1
            continue
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_box_table_block(lines, i)
            data_rows, notes = _parse_format_b_plan_rows(rows)
            note_values = _extract_format_b_plan_notes(notes)
            for data in data_rows:
                subject = data[0].strip()
                direction = _normalise_plan_direction(meta.get("direction", ""))
                progress = meta.get("progress", "").strip()
                if not any((subject, direction, progress)):
                    continue
                target_shares_text = data[2].strip()
                target_shares, _ = _to_scaled_int(target_shares_text)
                target_ratio_text = data[3].strip()
                target_amount_min_text = data[4].strip()
                target_amount_max_text = data[5].strip()
                target_amount_min, _ = _to_scaled_int(target_amount_min_text)
                target_amount_max, _ = _to_scaled_int(target_amount_max_text)
                plans.append(
                    {
                        "stock_code": page_code,
                        "stock_name": page_name,
                        "market": market,
                        "announce_date": _normalise_date(meta.get("announce", "")),
                        "latest_announce_date": _normalise_date(meta.get("announce", "")),
                        "first_announce_date": _normalise_date(
                            note_values.get("first_announce_date", "")
                        ),
                        "subject": subject,
                        "direction": direction,
                        "progress": progress,
                        "start_date": _normalise_date(note_values.get("start_date", "")),
                        "end_date": _normalise_date(note_values.get("end_date", "")),
                        "target_shares_min_text": "",
                        "target_shares_min": None,
                        "target_shares_text": target_shares_text,
                        "target_shares": target_shares,
                        "target_ratio_text": target_ratio_text,
                        "target_ratio": _to_float(target_ratio_text),
                        "target_amount_min_text": target_amount_min_text,
                        "target_amount_min": target_amount_min,
                        "target_amount_max_text": target_amount_max_text,
                        "target_amount_max": target_amount_max,
                        "trade_method": note_values.get("trade_method", ""),
                        "reason": note_values.get("reason", ""),
                        "narrative": note_values.get("narrative", ""),
                        "page_update_date": page_update_date,
                        "source": "tdx_f10",
                        "raw_hash": raw_hash,
                        "fetched_at": fetched_at,
                    }
                )
            i = end
            continue
        i += 1

    return _records_with_columns(plans, _PLAN_COLUMNS)


_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _normalise_date(s: str) -> Optional[str]:
    if not s:
        return None
    m = _DATE_RE.search(s.replace(".", "-"))
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        parsed = datetime(int(y), int(mo), int(d))
    except ValueError:
        return None
    # F10 occasionally contains OCR/alignment artifacts such as 2121/2232.
    # Treat those as invalid structured dates while preserving the raw text
    # in the caller's narrative/date_text fields.
    if parsed.year < 1990 or parsed.year > datetime.now().year + 1:
        return None
    return parsed.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Section 3 — 股东持股变动
# ---------------------------------------------------------------------------

def _is_trade_header_row(cells: list[str]) -> bool:
    """The trades table header spans two visual rows.

    Row 1: ['变动日期', '股东名称', '持股数', '变动股数', '变动后', '变动后占', '变动类型']
    Row 2: ['', '', '(股)', '(股)', '持股数(股)', '总股本比例(%)', '']

    Both must be skipped before parsing data rows.
    """

    blob = "".join(cells)
    if "变动日期" in blob and "股东名称" in blob:
        return True
    if "总股本比例" in blob or "持股数(股)" in blob:
        return True
    # Pure unit-only continuation: every non-empty cell is a unit annotation
    non_empty = [c for c in cells if c]
    if non_empty and all(
        c in ("(股)", "持股数(股)", "总股本比例(%)") for c in non_empty
    ):
        return True
    return False


def parse_shareholder_trades(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse F10 段 3 (股东持股变动) into one record per trade."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 3)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    trades: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_table_block(lines, i)
            # Skip header rows (the title spans 2 visual lines, so 2 header rows
            # may be present)
            data_rows = [r for r in rows if not _is_trade_header_row(r)]
            data_rows = _normalise_row_count(data_rows)
            last_record: Optional[dict[str, Any]] = None
            for cells in data_rows:
                if len(cells) < 7:
                    continue
                date_cell = cells[0].strip()
                name_cell = cells[1].strip()
                shares_before_cell = cells[2].strip()
                shares_change_cell = cells[3].strip()
                shares_after_cell = cells[4].strip()
                ratio_after_cell = cells[5].strip()
                change_type_cell = cells[6].strip()
                if not date_cell and not shares_before_cell and not shares_after_cell:
                    # Continuation row for a long holder name
                    if last_record is not None and name_cell:
                        last_record["holder_name"] = (
                            last_record["holder_name"] + name_cell
                        ).strip()
                    continue
                shares_before, _ = _to_int_shares(shares_before_cell)
                shares_change, _ = _to_int_shares(shares_change_cell)
                shares_after, _ = _to_int_shares(shares_after_cell)
                ratio_after = (
                    _to_float(ratio_after_cell) if ratio_after_cell not in ("-", "") else None
                )
                rec = {
                    "stock_code": page_code,
                    "stock_name": page_name,
                    "market": market,
                    "change_date": _normalise_date(date_cell),
                    "holder_name": name_cell,
                    "shares_before_text": shares_before_cell,
                    "shares_before": shares_before,
                    "shares_change_text": shares_change_cell,
                    "shares_change": shares_change,
                    "shares_after_text": shares_after_cell,
                    "shares_after": shares_after,
                    "ratio_after": ratio_after,
                    "change_type": change_type_cell,
                    "page_update_date": page_update_date,
                    "source": "tdx_f10",
                    "raw_hash": raw_hash,
                    "fetched_at": fetched_at,
                }
                trades.append(rec)
                last_record = rec
            i = end
            continue
        i += 1

    return _records_with_columns(trades, _TRADE_COLUMNS)


_TRADE_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "change_date",
    "holder_name",
    "shares_before_text",
    "shares_before",
    "shares_change_text",
    "shares_change",
    "shares_after_text",
    "shares_after",
    "ratio_after",
    "change_type",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]


# ---------------------------------------------------------------------------
# Format B Section 3 — 重要股东持股变动
# ---------------------------------------------------------------------------


def _split_format_b_period(value: str) -> tuple[Optional[str], Optional[str]]:
    dates = re.findall(r"\d{4}[.-]\d{1,2}[.-]\d{1,2}", value or "")
    if not dates:
        return None, None
    start = _normalise_date(dates[0])
    end = _normalise_date(dates[-1])
    return start, end


def _shares_from_wan_text(value: str) -> tuple[Optional[int], str]:
    text = (value or "").strip()
    if not text or text == "---":
        return None, text
    num = _to_float(text)
    if num is None:
        return None, text
    return int(round(num * 10_000)), f"{text}万"


def parse_shareholder_trades_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse Format B 段 3 重要股东持股变动.

    This richer table has period, actor, changed shares, average price,
    remaining shares and transaction method. It is intentionally separate
    from :func:`parse_shareholder_trades`, whose schema mirrors Format A.
    """

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 3)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    records: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_box_table_block(lines, i)
            rows = _normalise_row_count(rows)
            last_record: Optional[dict[str, Any]] = None
            for cells in rows:
                if len(cells) < 6:
                    continue
                blob = "".join(cells)
                if "变动期间" in blob and "变动人" in blob:
                    continue
                period_text = cells[0].strip()
                holder_name = cells[1].strip()
                shares_change_cell = cells[2].strip()
                avg_price_cell = cells[3].strip()
                shares_after_cell = cells[4].strip()
                method_cell = cells[5].strip()

                if (
                    not period_text
                    and not shares_change_cell
                    and not avg_price_cell
                    and not shares_after_cell
                    and not method_cell
                ):
                    if last_record is not None and holder_name:
                        last_record["holder_name"] = (
                            last_record["holder_name"] + holder_name
                        ).strip()
                    continue

                start_date, end_date = _split_format_b_period(period_text)
                shares_change, shares_change_text = _shares_from_wan_text(shares_change_cell)
                shares_after, shares_after_text = _shares_from_wan_text(shares_after_cell)
                rec = {
                    "stock_code": page_code,
                    "stock_name": page_name,
                    "market": market,
                    "change_period_text": period_text,
                    "change_start_date": start_date,
                    "change_end_date": end_date,
                    "change_date": end_date or start_date,
                    "holder_name": holder_name,
                    "shares_change_text": shares_change_text,
                    "shares_change": shares_change,
                    "average_price_text": avg_price_cell,
                    "average_price": _to_float(avg_price_cell) if avg_price_cell != "---" else None,
                    "shares_after_text": shares_after_text,
                    "shares_after": shares_after,
                    "change_method": method_cell,
                    "page_update_date": page_update_date,
                    "source": "tdx_f10",
                    "raw_hash": raw_hash,
                    "fetched_at": fetched_at,
                }
                records.append(rec)
                last_record = rec
            i = end
            continue
        i += 1

    return _records_with_columns(records, _TRADE_B_COLUMNS)


_TRADE_B_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "change_period_text",
    "change_start_date",
    "change_end_date",
    "change_date",
    "holder_name",
    "shares_change_text",
    "shares_change",
    "average_price_text",
    "average_price",
    "shares_after_text",
    "shares_after",
    "change_method",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]


# ---------------------------------------------------------------------------
# Format B Section 5 — 股东人数变化
# ---------------------------------------------------------------------------


def parse_holder_count_history_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse Format B 段 5 股东人数变化."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    lines = _section_lines(text or "", 5)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    records: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        if TABLE_TOP.match(lines[i].rstrip()):
            rows, end = _take_box_table_block(lines, i)
            rows = _normalise_row_count(rows)
            for cells in rows:
                if len(cells) < 7:
                    continue
                if "截止日期" in "".join(cells):
                    continue
                date_text = cells[0].strip()
                if not date_text:
                    continue
                holder_count_text = cells[1].strip()
                change_text = cells[2].strip()
                change_pct_text = cells[3].strip()
                avg_float_text = cells[4].strip()
                avg_float_change_pct_text = cells[5].strip()
                close_price_text = cells[6].strip()
                holder_count, _ = _to_int_shares(holder_count_text)
                holder_count_change, _ = _to_int_shares(change_text)
                avg_float, _ = _to_int_shares(avg_float_text)
                records.append({
                    "stock_code": page_code,
                    "stock_name": page_name,
                    "market": market,
                    "report_date": _normalise_date(date_text),
                    "report_date_text": date_text,
                    "holder_count_text": holder_count_text,
                    "holder_count": holder_count,
                    "holder_count_change_text": change_text,
                    "holder_count_change": holder_count_change,
                    "holder_count_change_pct_text": change_pct_text,
                    "holder_count_change_pct": _to_float(change_pct_text),
                    "avg_float_shares_text": avg_float_text,
                    "avg_float_shares": avg_float,
                    "avg_float_shares_change_pct_text": avg_float_change_pct_text,
                    "avg_float_shares_change_pct": _to_float(avg_float_change_pct_text),
                    "close_price_text": close_price_text,
                    "close_price": _to_float(close_price_text),
                    "page_update_date": page_update_date,
                    "source": "tdx_f10",
                    "raw_hash": raw_hash,
                    "fetched_at": fetched_at,
                })
            i = end
            continue
        i += 1

    return _records_with_columns(records, _HOLDER_COUNT_COLUMNS)


_HOLDER_COUNT_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "report_date",
    "report_date_text",
    "holder_count_text",
    "holder_count",
    "holder_count_change_text",
    "holder_count_change",
    "holder_count_change_pct_text",
    "holder_count_change_pct",
    "avg_float_shares_text",
    "avg_float_shares",
    "avg_float_shares_change_pct_text",
    "avg_float_shares_change_pct",
    "close_price_text",
    "close_price",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
]


# ---------------------------------------------------------------------------
# Format B Section 6 — 同大股东个股
# ---------------------------------------------------------------------------

COMMON_MAJOR_HOLDER_RE = re.compile(r"^(?P<name>.+?)[（(]\s*共\s*\d+\s*家\s*[）)]\s*$")
COMMON_MAJOR_ROW_RE = re.compile(
    r"^\s*(?P<rank>\d+)\s+"
    r"(?P<code>\d{6})\s+"
    r"(?P<name>.+?)\s+"
    r"(?P<shares>-?[\d.,]+(?:亿|万|股)?)\s+"
    r"(?P<ratio>-?[\d.,]+|---)\s+"
    r"(?P<change>不变|未变|新进|退出|---|-?[\d.,]+(?:亿|万|股)?)\s+"
    r"(?P<parent>-?[\d.,]+(?:亿元|万元|亿|万|元)?)\s+"
    r"(?P<deduct>-?[\d.,]+(?:亿元|万元|亿|万|元)?)\s*$"
)


def _clean_major_holder_name(line: str) -> str:
    m = COMMON_MAJOR_HOLDER_RE.match(line.strip())
    if m:
        return m.group("name").strip()
    return line.strip()


def parse_common_major_holder_stocks_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse Format B 段 6 同大股东个股."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)
    report_date_text = _section_report_date_text(text, 6)
    report_date = _normalise_date(report_date_text or "")

    lines = _section_lines(text or "", 6)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    records: list[dict[str, Any]] = []
    major_holder_name: Optional[str] = None
    row_seq = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or FORMAT_B_DASH_RE.match(stripped):
            continue
        if stripped.startswith("【"):
            break
        if stripped.startswith("排名") and "证券代码" in stripped:
            continue
        m = COMMON_MAJOR_ROW_RE.match(stripped)
        if not m:
            major_holder_name = _clean_major_holder_name(stripped)
            continue
        if not major_holder_name:
            continue
        row_seq += 1
        shares_text = m.group("shares")
        shares, _ = _to_scaled_int(shares_text)
        change_text = m.group("change")
        net_profit_parent_text = m.group("parent")
        net_profit_deducted_text = m.group("deduct")
        net_profit_parent, _ = _to_scaled_int(net_profit_parent_text)
        net_profit_deducted, _ = _to_scaled_int(net_profit_deducted_text)
        records.append(
            {
                "stock_code": page_code,
                "stock_name": page_name,
                "market": market,
                "report_date": report_date,
                "report_date_text": report_date_text,
                "major_holder_name": major_holder_name,
                "peer_stock_code": m.group("code"),
                "peer_stock_name": m.group("name").strip(),
                "shares_text": shares_text,
                "shares": shares,
                "hold_ratio_text": m.group("ratio"),
                "hold_ratio": _to_float(m.group("ratio")),
                "change_text": change_text,
                "change_shares": _to_signed_scaled_int(change_text),
                "net_profit_parent_text": net_profit_parent_text,
                "net_profit_parent": net_profit_parent,
                "net_profit_deducted_text": net_profit_deducted_text,
                "net_profit_deducted": net_profit_deducted,
                "page_update_date": page_update_date,
                "source": "tdx_f10",
                "raw_hash": raw_hash,
                "fetched_at": fetched_at,
                "row_seq": row_seq,
            }
        )

    return _records_with_columns(records, _COMMON_MAJOR_HOLDER_COLUMNS)


_COMMON_MAJOR_HOLDER_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "report_date",
    "report_date_text",
    "major_holder_name",
    "peer_stock_code",
    "peer_stock_name",
    "shares_text",
    "shares",
    "hold_ratio_text",
    "hold_ratio",
    "change_text",
    "change_shares",
    "net_profit_parent_text",
    "net_profit_parent",
    "net_profit_deducted_text",
    "net_profit_deducted",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
    "row_seq",
]


# ---------------------------------------------------------------------------
# Format B Section 7 — 基金持股
# ---------------------------------------------------------------------------

F10_FOOTER_KEYWORDS = (
    "本公司力求但不保证",
    "真实性、准确性",
    "投资者使用前请自行予以核实",
    "不作为投资决策的依据",
    "投资有风险",
    "中国证监会指定上市公司信息披露媒体",
    "不对因上述信息",
    "服务不会受中断",
)

_SCALED_UNIT_RE = re.compile(r"(亿元|万元|亿股|万股|亿|万|股|元)")
_NUMERIC_WITH_OPTIONAL_UNIT_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:亿元|万元|亿股|万股|亿|万|股|元)?$")


def _is_f10_footer_text(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return any(keyword in compact for keyword in F10_FOOTER_KEYWORDS)


def _scaled_header_multiplier(header: str) -> int:
    compact = (header or "").replace(" ", "")
    m = re.search(r"[（(](?P<unit>亿元|万元|亿股|万股|股|元|亿|万)[）)]", compact)
    if not m:
        return 1
    unit = m.group("unit")
    if unit == "亿股":
        unit = "亿"
    elif unit == "万股":
        unit = "万"
    return SCALED_UNIT_MAP.get(unit, 1)


def _to_scaled_int_with_header_unit(text: str, header: str) -> tuple[Optional[int], Optional[str]]:
    raw = (text or "").strip().replace(",", "").replace("，", "").replace(" ", "")
    if not raw or raw in ("-", "---", "--", "—"):
        return None, None
    # Explicit cell units win over the table header unit.
    if _SCALED_UNIT_RE.search(raw):
        normalized = raw.replace("亿股", "亿").replace("万股", "万")
        return _to_scaled_int(normalized)
    if not _NUMERIC_WITH_OPTIONAL_UNIT_RE.match(raw):
        return None, None
    val = float(raw)
    multiplier = _scaled_header_multiplier(header)
    return int(round(val * multiplier)), ("header" if multiplier != 1 else "base")


def _is_fund_name_continuation(text: str) -> bool:
    compact = (text or "").strip()
    if not compact or _is_f10_footer_text(compact):
        return False
    if any(token in compact for token in ("基金名称", "持股数", "持股市值", "真实性", "投资者")):
        return False
    if re.match(r"^\d+[、.．]", compact):
        return False
    return any(
        token in compact
        for token in (
            "基金",
            "证券",
            "指数",
            "交易",
            "开放式",
            "联接",
            "股票",
            "混合",
            "债券",
            "ETF",
            "LOF",
        )
    )


def _find_header_index(headers: list[str], needles: tuple[str, ...], fallback: int) -> int:
    for idx, header in enumerate(headers):
        if any(needle in header for needle in needles):
            return idx
    return fallback


def _parse_fund_holding_cells(
    cells: list[str], headers: list[str], last_record: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    if not cells:
        return None
    if _is_f10_footer_text("".join(cells)):
        return None
    name_idx = _find_header_index(headers, ("基金名称",), 0)
    shares_idx = _find_header_index(headers, ("持股数", "持仓数量", "持股数量"), 1)
    ratio_idx = _find_header_index(headers, ("流通", "比例"), 2)
    value_idx = _find_header_index(headers, ("市值",), 3)
    padded = cells + [""] * (max(name_idx, shares_idx, ratio_idx, value_idx) + 1 - len(cells))
    fund_name = padded[name_idx].strip()
    shares_text = padded[shares_idx].strip()
    ratio_text = padded[ratio_idx].strip()
    value_text = padded[value_idx].strip()
    if (
        fund_name
        and not any((shares_text, ratio_text, value_text))
        and last_record is not None
        and _is_fund_name_continuation(fund_name)
    ):
        last_record["fund_name"] = (last_record["fund_name"] + fund_name).strip()
        return None
    if not fund_name or not any((shares_text, ratio_text, value_text)):
        return None
    shares, _ = _to_scaled_int_with_header_unit(shares_text, headers[shares_idx] if shares_idx < len(headers) else "")
    market_value, _ = _to_scaled_int_with_header_unit(
        value_text,
        headers[value_idx] if value_idx < len(headers) else "",
    )
    if shares is None or market_value is None:
        return None
    return {
        "fund_name": fund_name,
        "shares_text": shares_text,
        "shares": shares,
        "float_a_ratio_text": ratio_text,
        "float_a_ratio": _to_float(ratio_text),
        "market_value_text": value_text,
        "market_value": market_value,
    }


def parse_fund_holdings_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> list[dict[str, Any]]:
    """Parse Format B 段 7 基金持股."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)
    report_date_text = _section_report_date_text(text, 7)
    report_date = _normalise_date(report_date_text or "")

    lines = _section_lines(text or "", 7)
    if not lines or any("暂无数据" in ln for ln in lines):
        return []

    records: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    row_seq = 0
    while i < n:
        line = lines[i].rstrip()
        if TABLE_TOP.match(line):
            rows, end = _take_box_table_block(lines, i)
            rows = _normalise_row_count(rows)
            headers: list[str] = []
            last_record: Optional[dict[str, Any]] = None
            for cells in rows:
                blob = "".join(cells)
                if _is_f10_footer_text(blob):
                    break
                if "基金名称" in blob and ("持股" in blob or "持仓" in blob):
                    headers = [c.strip() for c in cells]
                    continue
                if not headers:
                    continue
                rec = _parse_fund_holding_cells(cells, headers, last_record)
                if rec is None:
                    continue
                row_seq += 1
                rec.update(
                    {
                        "stock_code": page_code,
                        "stock_name": page_name,
                        "market": market,
                        "report_date": report_date,
                        "report_date_text": report_date_text,
                        "page_update_date": page_update_date,
                        "source": "tdx_f10",
                        "raw_hash": raw_hash,
                        "fetched_at": fetched_at,
                        "row_seq": row_seq,
                    }
                )
                records.append(rec)
                last_record = rec
            i = end
            continue

        if "基金名称" in line and ("持股" in line or "持仓" in line):
            col_starts = _column_visual_starts(line)
            headers = _slice_by_visual_cols(line, col_starts)
            last_record = records[-1] if records else None
            i += 1
            while i < n:
                data_line = lines[i].rstrip()
                if not data_line.strip() or FORMAT_B_DASH_RE.match(data_line.strip()):
                    i += 1
                    continue
                if _is_f10_footer_text(data_line):
                    break
                if data_line.startswith("【"):
                    break
                cells = _slice_by_visual_cols(data_line, col_starts)
                rec = _parse_fund_holding_cells(cells, headers, last_record)
                if rec is not None:
                    row_seq += 1
                    rec.update(
                        {
                            "stock_code": page_code,
                            "stock_name": page_name,
                            "market": market,
                            "report_date": report_date,
                            "report_date_text": report_date_text,
                            "page_update_date": page_update_date,
                            "source": "tdx_f10",
                            "raw_hash": raw_hash,
                            "fetched_at": fetched_at,
                            "row_seq": row_seq,
                        }
                    )
                    records.append(rec)
                    last_record = rec
                i += 1
            continue
        i += 1

    return _records_with_columns(records, _FUND_HOLDING_COLUMNS)


_FUND_HOLDING_COLUMNS = [
    "stock_code",
    "stock_name",
    "market",
    "report_date",
    "report_date_text",
    "fund_name",
    "shares_text",
    "shares",
    "float_a_ratio_text",
    "float_a_ratio",
    "market_value_text",
    "market_value",
    "page_update_date",
    "source",
    "raw_hash",
    "fetched_at",
    "row_seq",
]


# ---------------------------------------------------------------------------
# Combined view — parse_research
# ---------------------------------------------------------------------------


def parse_research(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> dict[str, Any]:
    """Parse all supported 「股东研究」 sections in one call.

    ``trades`` preserves the legacy Format A section-3 schema. Format B-only
    sections are exposed through separate keys so existing callers do not
    accidentally write richer tables into the legacy schema.
    """

    fmt = detect_f10_format(text)
    holders, periods = parse_holders_auto(text, symbol=symbol, stock_name=stock_name)
    head = PAGE_HEAD_RE.search(text or "")
    page = {
        "stock_code": head.group("code") if head else symbol,
        "stock_name": head.group("name") if head else stock_name,
        "market": _market_str(symbol or (head.group("code") if head else "")),
        "page_update_date": head.group("update_date") if head else None,
        "raw_hash": _hash(text),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {
        "page": page,
        "controlling": parse_controlling_shareholder(
            text, symbol=symbol, stock_name=stock_name
        ),
        "plans": parse_shareholder_plans(text, symbol=symbol, stock_name=stock_name),
        "trades": parse_shareholder_trades(text, symbol=symbol, stock_name=stock_name),
        "trades_b": (
            parse_shareholder_trades_format_b(text, symbol=symbol, stock_name=stock_name)
            if fmt == "b"
            else []
        ),
        "holders": holders,
        "periods": periods,
        "holder_count_history": (
            parse_holder_count_history_format_b(text, symbol=symbol, stock_name=stock_name)
            if fmt == "b"
            else []
        ),
        "common_major_holder_stocks": (
            parse_common_major_holder_stocks_format_b(
                text, symbol=symbol, stock_name=stock_name
            )
            if fmt == "b"
            else []
        ),
        "fund_holdings": (
            parse_fund_holdings_format_b(text, symbol=symbol, stock_name=stock_name)
            if fmt == "b"
            else []
        ),
    }


def _normalise_record_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalise_record_value(value) for key, value in record.items()}


def _normalise_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return []
    return [_normalise_record(record) for record in records]


def parse_holders_records(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> dict[str, list[dict[str, Any]]]:
    """Parse section-4 holder tables into records."""

    holders, periods = parse_holders_auto(text, symbol=symbol, stock_name=stock_name)
    return {
        "holders": _normalise_records(holders),
        "periods": _normalise_records(periods),
    }


def parse_research_records(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> dict[str, Any]:
    """Parse all supported 「股东研究」 sections into plain Python records."""

    parsed = parse_research(text, symbol=symbol, stock_name=stock_name)
    records: dict[str, Any] = {}
    for key, value in parsed.items():
        if isinstance(value, list):
            records[key] = _normalise_records(value)
        elif isinstance(value, dict):
            records[key] = _normalise_record(value)
        else:
            records[key] = value
    return records


# ---------------------------------------------------------------------------
# Format B parser (通达信沪深京F10)
# ---------------------------------------------------------------------------

FORMAT_B_PERIOD_HEAD_RE = re.compile(
    r"^●(?P<kind>十大流通股东|十大股东)\s*截止日期:(?P<report_date>\d{4}-\d{2}-\d{2})\s*$"
)
FORMAT_B_STAT_RE = re.compile(
    r"前十大(?:流通)?股东累计持有[:：](?P<cum>[\d.,]+(?:亿|万)?)股[,，]"
    r"累计占(?:流通股|总股本)比[:：](?P<ratio>[\d.,]+)%[,，]"
    r"较上期变化[:：](?P<delta>-?[\d.,]+(?:亿|万)?)股"
)
FORMAT_B_EXIT_HEAD_RE = re.compile(r"较上个报告期退出前十大(?:流通)?股东")
FORMAT_B_DASH_RE = re.compile(r"^[─\-]+\s*$")
FORMAT_B_FIELD_HEADER_RE = re.compile(r"^股东名称\s+(股份性质|股东类别)\s")
FORMAT_B_SHARE_CLASS_INLINE_RE = re.compile(r"(?P<ratio>-?\d+(?:\.\d+)?)\s+(?P<klass>[AHB])股\s*$")


def _visual_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _find_visual_col(line: str, target_visual: int) -> int:
    """Return the char index whose cumulative visual width first reaches ``target_visual``."""

    w = 0
    for i, c in enumerate(line):
        if w >= target_visual:
            return i
        w += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return len(line)


def _column_visual_starts(header: str) -> list[int]:
    """Return visual positions where each header column begins.

    A column boundary is a transition from spaces to non-space characters.
    """

    starts: list[int] = []
    in_col = False
    pos = 0
    for c in header:
        if c == " " or c == "\t":
            in_col = False
        else:
            if not in_col:
                starts.append(pos)
                in_col = True
        pos += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return starts


def _slice_by_visual_cols(line: str, visual_starts: list[int]) -> list[str]:
    """Slice ``line`` into stripped column substrings at the given visual positions."""

    if not visual_starts:
        return []
    char_positions = [_find_visual_col(line, v) for v in visual_starts] + [len(line)]
    return [line[char_positions[i]:char_positions[i + 1]].strip() for i in range(len(visual_starts))]


def _parse_format_b_period_block(
    lines: list[str], start: int
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], int]:
    """Parse one ●十大... period (main + exit table). Returns (period_record, holder_records, end_idx)."""

    n = len(lines)
    head_m = FORMAT_B_PERIOD_HEAD_RE.match(lines[start].strip())
    if not head_m:
        return None, [], start + 1
    kind = head_m.group("kind")
    holder_set = "free" if kind == "十大流通股东" else "all"
    period: dict[str, Any] = {
        "report_date": _normalise_date(head_m.group("report_date")),
        "holder_set": holder_set,
        "a_share_holder_count_text": None,
        "a_share_holder_count": None,
        "avg_float_shares_text": None,
        "avg_float_shares": None,
        "cumulative_text": None,
        "cumulative_shares": None,
        "cumulative_ratio": None,
        "delta_vs_prev_text": None,
        "delta_vs_prev_shares": None,
    }
    i = start + 1
    # Optional stat line ("前十大[流通]股东累计持有:...,累计占...,较上期变化:...")
    # Some 1H stocks (e.g. 601398) skip this line and jump straight to a
    # "主要股东持股变动" subtitle — we tolerate either by scanning ahead until
    # we find the column header. Anything before the header is informational.
    while i < n and not FORMAT_B_FIELD_HEADER_RE.match(lines[i]):
        if FORMAT_B_PERIOD_HEAD_RE.match(lines[i].strip()):
            return period, [], i
        sm = FORMAT_B_STAT_RE.search(lines[i])
        if sm:
            cum_text = sm.group("cum")
            cum_shares, _ = _to_int_shares(cum_text)
            delta_text = sm.group("delta")
            delta_shares, _ = _to_int_shares(delta_text or "")
            period.update(
                {
                    "cumulative_text": cum_text,
                    "cumulative_shares": cum_shares,
                    "cumulative_ratio": _to_float(sm.group("ratio")),
                    "delta_vs_prev_text": delta_text,
                    "delta_vs_prev_shares": delta_shares,
                }
            )
        i += 1
    if i >= n:
        return period, [], i
    header_line = lines[i]
    col_starts = _column_visual_starts(header_line)
    headers = _slice_by_visual_cols(header_line, col_starts)
    i += 1
    # opening dash line
    if i < n and FORMAT_B_DASH_RE.match(lines[i]):
        i += 1

    holders: list[dict[str, Any]] = []
    rank = 0
    row_seq = 0
    last_record: Optional[dict[str, Any]] = None
    is_exit = False

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if FORMAT_B_DASH_RE.match(line):
            i += 1
            # Lookahead: 一致行动人 paragraph or exit table or next period
            while i < n and not FORMAT_B_DASH_RE.match(lines[i]) and not lines[i].startswith("●"):
                if FORMAT_B_EXIT_HEAD_RE.search(lines[i]):
                    # Move past the exit title + the dash line below it
                    i += 1
                    while i < n and not FORMAT_B_DASH_RE.match(lines[i]):
                        i += 1
                    if i < n and FORMAT_B_DASH_RE.match(lines[i]):
                        i += 1
                    is_exit = True
                    rank = 0
                    last_record = None
                    break
                # Skip any 一致行动人 / informational paragraph
                i += 1
            if i < n and FORMAT_B_DASH_RE.match(lines[i]) and not is_exit:
                i += 1
                continue
            if not is_exit and (i >= n or lines[i].startswith("●")):
                break
            continue
        if line.startswith("●"):
            break

        cells = _slice_by_visual_cols(line, col_starts)
        if not cells:
            i += 1
            continue
        name = cells[0].strip()
        rest = [c.strip() for c in cells[1:]]
        nature_frag = rest[0] if len(rest) > 0 else ""
        shares_cell = rest[1] if len(rest) > 1 else ""
        ratio_cell = rest[2] if len(rest) > 2 else ""
        change_cell = rest[3] if len(rest) > 3 else ""

        # Continuation logic: a row is a wrap continuation of the prior
        # holder when ratio AND change are both blank, regardless of name
        # / nature / shares fragment present. Two real-world cases:
        # 1. Long names wrap (name fragment + everything else blank).
        # 2. Big shares values overflow the column width (ICBC's
        #    12400466.09 → "12400466.0\n9"); shares cell has a tiny digit
        #    fragment and ratio/change are blank.
        shares_is_overflow_only = bool(shares_cell) and shares_cell.replace(".", "").replace("-", "").isdigit()
        is_continuation = (
            (not ratio_cell)
            and (not change_cell)
            and (not shares_cell or shares_is_overflow_only)
        )
        if is_continuation:
            if last_record is not None:
                if name:
                    last_record["holder_name"] = (last_record["holder_name"] + name).strip()
                if nature_frag:
                    last_record["holder_type_or_nature"] = (
                        last_record["holder_type_or_nature"] + nature_frag
                    ).strip()
                # Numeric overflow for shares: append the trailing digit(s).
                if shares_cell and shares_cell.replace(".", "").isdigit():
                    base = (last_record.get("shares_text") or "").rstrip("万")
                    last_record["shares_text"] = base + shares_cell + "万"
                    full_num = _to_float(base + shares_cell)
                    if full_num is not None:
                        last_record["shares_approx"] = int(round(full_num * 10_000))
            i += 1
            continue
        # Real data row
        nature_or_type = nature_frag
        shares_text = shares_cell
        # tail: 一致行动人关系组 (only in 十大股东 block)
        # share class detection
        klass = None
        ratio_val = None
        m_inline = FORMAT_B_SHARE_CLASS_INLINE_RE.search(ratio_cell)
        if m_inline:
            klass = m_inline.group("klass")
            ratio_val = float(m_inline.group("ratio"))
        else:
            ratio_val = _to_float(ratio_cell)
            if "A股" in nature_or_type and "H股" not in nature_or_type:
                klass = "A"
            elif "H股" in nature_or_type:
                klass = "H"
            elif "B股" in nature_or_type:
                klass = "B"
        # shares: in 万 unit
        shares_approx = None
        shares_precision = None
        shares_text_normalised = shares_text
        if shares_text:
            num = _to_float(shares_text)
            if num is not None:
                shares_approx = int(round(num * 10_000))
                shares_precision = "万"
                shares_text_normalised = f"{shares_text}万"
        # change status
        change_text = change_cell.strip()
        change_status, change_shares = _classify_format_b_change(change_text)
        rank += 1
        row_seq += 1
        rec = {
            "holder_rank": rank,
            "row_seq": row_seq,
            "holder_name": name,
            "share_class": klass,
            "shares_text": shares_text_normalised,
            "shares_approx": shares_approx,
            "shares_precision": shares_precision,
            "hold_ratio": ratio_val,
            "holder_type_or_nature": nature_or_type,
            "change_status": change_status,
            "change_shares_text": change_text,
            "change_shares_approx": change_shares,
            "is_exit_row": is_exit,
            "is_secondary_class": False,
        }
        holders.append(rec)
        last_record = rec
        i += 1

    return period, holders, i


def _classify_format_b_change(raw: str) -> tuple[str, Optional[int]]:
    """Map Format B 增减情况(万) cell text to ``(status, signed_shares)``.

    Format B values are stem numbers in 万 unit (e.g. ``368.42`` for +368.42万),
    with sentinel words ``未变`` / ``新进`` / ``退出`` / ``---``.
    """

    txt = (raw or "").strip()
    if not txt or txt == "---":
        return "未知", None
    if txt == "未变":
        return "不变", 0
    if txt == "新进":
        return "新进", None
    if txt == "退出":
        return "退出", None
    val = _to_float(txt)
    if val is None:
        return "未知", None
    shares = int(round(val * 10_000))
    if shares > 0:
        return "增持", shares
    if shares < 0:
        return "减持", shares
    return "不变", 0


def parse_holders_format_b(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse F10 段 4 from the 通达信沪深京F10 (Format B) layout."""

    raw_hash = _hash(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = PAGE_HEAD_RE.search(text or "")
    page_code = head.group("code") if head else symbol
    page_name = head.group("name") if head else stock_name
    page_update_date = head.group("update_date") if head else None
    market = _market_str(symbol or page_code)

    holder_records: list[dict[str, Any]] = []
    period_records: list[dict[str, Any]] = []

    lines = _section_lines(text or "", 4)
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if FORMAT_B_PERIOD_HEAD_RE.match(line.strip()):
            period, holders, end = _parse_format_b_period_block(lines, i)
            if period is not None:
                base = {
                    "stock_code": page_code,
                    "stock_name": page_name,
                    "market": market,
                    "report_date": period["report_date"],
                    "holder_set": period["holder_set"],
                    "page_update_date": page_update_date,
                    "source": "tdx_f10",
                    "raw_hash": raw_hash,
                    "fetched_at": fetched_at,
                }
                period_records.append({**period, **base})
                for h in holders:
                    holder_records.append({**h, **base})
            i = end
            continue
        i += 1

    return (
        _records_with_columns(holder_records, _HOLDER_COLUMNS),
        _records_with_columns(period_records, _PERIOD_COLUMNS),
    )


# ---------------------------------------------------------------------------
# Dispatch wrapper
# ---------------------------------------------------------------------------


def parse_holders_auto(
    text: str, *, symbol: str = "", stock_name: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Auto-detect F10 format and dispatch to the right parser.

    Format A (灵通V9.0 / 港澳资讯) and Format B (通达信沪深京F10) coexist in
    the live HQ host pool — your call may land on either depending on
    which server answered. This wrapper returns identical column schemas.
    """

    fmt = detect_f10_format(text)
    if fmt == "b":
        return parse_holders_format_b(text, symbol=symbol, stock_name=stock_name)
    return parse_holders(text, symbol=symbol, stock_name=stock_name)


# ---------------------------------------------------------------------------
# Fetcher (single client)
# ---------------------------------------------------------------------------


def fetch_holders_text(client: Any, symbol: str) -> Optional[str]:
    """Fetch the raw F10 「股东研究」 text via a tdxhub Quotes client.

    Returns ``None`` if the page is unavailable (e.g. 北交所 / no F10 entry).
    """

    market = int(get_stock_market(symbol, string=False))
    if market == MARKET_BJ:
        return None
    cats = client.client.get_company_info_category(market, symbol)
    if not cats:
        return None
    target = next((c for c in cats if c["name"] == "股东研究"), None)
    if target is None:
        return None
    return client.client.get_company_info_content(
        market, symbol, target["filename"], target["start"], target["length"]
    )


def fetch_holders(
    client: Any, symbol: str, *, stock_name: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convenience wrapper: fetch + parse holders only (sections 4).

    Uses :func:`parse_holders_auto` to handle both Format A and Format B
    transparently — the live HQ pool serves both layouts.
    """

    text = fetch_holders_text(client, symbol)
    if not text:
        return ([], [])
    return parse_holders_auto(text, symbol=symbol, stock_name=stock_name)


def fetch_research(
    client: Any, symbol: str, *, stock_name: str = ""
) -> Optional[dict[str, Any]]:
    """Convenience wrapper: fetch + parse all four sections in one call."""

    text = fetch_holders_text(client, symbol)
    if not text:
        return None
    return parse_research(text, symbol=symbol, stock_name=stock_name)


# ---------------------------------------------------------------------------
# Server-rotating fetcher (production stability)
# ---------------------------------------------------------------------------


class HolderFetcher:
    """Resilient F10 「股东研究」 fetcher with automatic server rotation.

    The TDX HQ host pool (~117 servers) is unstable: a non-trivial fraction
    are unreachable or return empty headers at any given moment, but the
    set of "good" servers shifts over time — yesterday's bad server is
    often today's good one. This class therefore avoids permanent
    blacklists. Failed servers are *suspended* for a cooldown window and
    re-tried automatically once it expires; the candidate pool is also
    re-synced from :data:`tdxhub.consts.HQ_HOSTS` when it grows stale or
    when callers explicitly request it via :meth:`refresh_pool`.

    The full HQ pool drives availability — calls land on whatever server
    succeeds, not on a fixed cached one. ``stats()`` exposes attempts,
    rotations and current pool state so monitoring is straightforward.
    """

    DEFAULT_SUSPEND_SECONDS = 600  # 10 min cooldown for a failed server
    DEFAULT_REFRESH_SECONDS = 1800  # re-sync HQ_HOSTS every 30 min

    def __init__(
        self,
        *,
        candidates: Optional[list[tuple[str, int]]] = None,
        timeout: int = 15,
        probe_timeout: float = 2.0,
        max_attempts_per_call: int = 6,
        prescreen_limit: int = 0,
        suspend_seconds: int = DEFAULT_SUSPEND_SECONDS,
        refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
    ) -> None:
        self._timeout = timeout
        self._probe_timeout = probe_timeout
        self._max_attempts = max_attempts_per_call
        self._prescreen_limit = prescreen_limit
        self._suspend_seconds = suspend_seconds
        self._refresh_seconds = refresh_seconds
        self._candidates: list[tuple[str, int]] = []
        self._reachable: list[tuple[str, int]] = []
        # server -> earliest unix-ts at which it can be retried again
        self._suspended_until: dict[tuple[str, int], float] = {}
        self._client: Any = None
        self._client_server: Optional[tuple[str, int]] = None
        self._last_pool_refresh: float = 0.0
        self._stats: dict[str, Any] = {
            "calls": 0,
            "successes": 0,
            "rotations": 0,
            "probe_calls": 0,
            "pool_refreshes": 0,
        }
        self.refresh_pool(initial=True, override_candidates=candidates)

    # -- internal helpers -----------------------------------------------------

    def refresh_pool(
        self,
        *,
        initial: bool = False,
        override_candidates: Optional[list[tuple[str, int]]] = None,
    ) -> None:
        """Re-sync the candidate pool from :data:`tdxhub.consts.HQ_HOSTS`.

        Drops nothing from ``_suspended_until`` so cooldowns continue to
        apply, but adds any server that has appeared in HQ_HOSTS since the
        last refresh.
        """

        from tdxhub.consts import HQ_HOSTS

        import time

        if override_candidates is not None:
            new_pool = list(override_candidates)
        else:
            new_pool = [(ip, port) for _name, ip, port in HQ_HOSTS]
        self._candidates = new_pool
        self._last_pool_refresh = time.time()
        self._stats["pool_refreshes"] += 1
        if initial:
            self._reachable = []

    def _maybe_refresh_pool(self) -> None:
        import time

        if time.time() - self._last_pool_refresh > self._refresh_seconds:
            self.refresh_pool()

    def _is_suspended(self, server: tuple[str, int]) -> bool:
        import time

        until = self._suspended_until.get(server)
        if until is None:
            return False
        if time.time() >= until:
            del self._suspended_until[server]
            return False
        return True

    def _suspend(self, server: tuple[str, int]) -> None:
        import time

        self._suspended_until[server] = time.time() + self._suspend_seconds

    def _probe_reachable(self) -> None:
        import socket
        import random

        self._stats["probe_calls"] += 1
        self._maybe_refresh_pool()
        active = [s for s in self._candidates if not self._is_suspended(s)]
        if not active:
            # All candidates are in cooldown — clear cooldowns and try fresh
            self._suspended_until.clear()
            active = list(self._candidates)
        random.shuffle(active)
        self._reachable = []
        limit = self._prescreen_limit or len(active)
        for ip, port in active:
            try:
                s = socket.create_connection((ip, port), timeout=self._probe_timeout)
                s.close()
                self._reachable.append((ip, port))
                if len(self._reachable) >= limit:
                    break
            except Exception:
                self._suspend((ip, port))

    def _ensure_client(self) -> Any:
        if self._client is not None and not self._is_suspended(self._client_server or ("", 0)):
            return self._client
        if self._client is not None:
            self._drop_client(suspend=False)
        from tdxhub.quotes import Quotes

        if not self._reachable:
            self._probe_reachable()
        last_err: Optional[Exception] = None
        for ip, port in list(self._reachable):
            try:
                client = Quotes.factory(
                    market="std", server=f"{ip}:{port}", timeout=self._timeout
                )
                self._client = client
                self._client_server = (ip, port)
                return client
            except Exception as e:
                last_err = e
                self._suspend((ip, port))
                if (ip, port) in self._reachable:
                    self._reachable.remove((ip, port))
        # Pool exhausted — re-probe and try again once
        self._probe_reachable()
        for ip, port in list(self._reachable):
            try:
                client = Quotes.factory(
                    market="std", server=f"{ip}:{port}", timeout=self._timeout
                )
                self._client = client
                self._client_server = (ip, port)
                return client
            except Exception as e:
                last_err = e
                self._suspend((ip, port))
                if (ip, port) in self._reachable:
                    self._reachable.remove((ip, port))
        if last_err is not None:
            raise last_err
        raise RuntimeError("no reachable TDX HQ servers in pool")

    def _drop_client(self, *, suspend: bool = True) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 — 关连接失败不该阻断轮换/收尾
                # 并入本仓时改: 原处是 pass。语义(关不掉也要继续)保持不变,
                # 只是从"无痕"改成留一行 —— 连接关不掉往往是对端已经断了,
                # 但也可能是句柄泄漏, 不记就永远看不见。
                logger.debug('关闭 TDX 连接失败, 继续', exc_info=True)
            if suspend and self._client_server is not None:
                self._suspend(self._client_server)
                if self._client_server in self._reachable:
                    self._reachable.remove(self._client_server)
        self._client = None
        self._client_server = None
        if suspend:
            self._stats["rotations"] += 1

    # -- public api -----------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "active_server": self._client_server,
            "candidate_count": len(self._candidates),
            "reachable_count": len(self._reachable),
            "suspended_count": len(self._suspended_until),
        }

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 — 关连接失败不该阻断轮换/收尾
                # 并入本仓时改: 原处是 pass。语义(关不掉也要继续)保持不变,
                # 只是从"无痕"改成留一行 —— 连接关不掉往往是对端已经断了,
                # 但也可能是句柄泄漏, 不记就永远看不见。
                logger.debug('关闭 TDX 连接失败, 继续', exc_info=True)
        self._client = None
        self._client_server = None

    def fetch_text(self, symbol: str) -> Optional[str]:
        self._stats["calls"] += 1
        market = int(get_stock_market(symbol, string=False))
        if market == MARKET_BJ:
            return None
        last_err: Optional[Exception] = None
        for _ in range(self._max_attempts):
            try:
                client = self._ensure_client()
                text = fetch_holders_text(client, symbol)
                self._stats["successes"] += 1
                return text
            except Exception as e:
                last_err = e
                self._drop_client()
                continue
        if last_err is not None:
            raise last_err
        return None

    def fetch_holders(
        self, symbol: str, *, stock_name: str = ""
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        text = self.fetch_text(symbol)
        if not text:
            return ([], [])
        return parse_holders_auto(text, symbol=symbol, stock_name=stock_name)

    def fetch_research(
        self, symbol: str, *, stock_name: str = ""
    ) -> Optional[dict[str, Any]]:
        text = self.fetch_text(symbol)
        if not text:
            return None
        return parse_research(text, symbol=symbol, stock_name=stock_name)

    def fetch_research_records(
        self, symbol: str, *, stock_name: str = ""
    ) -> Optional[dict[str, Any]]:
        text = self.fetch_text(symbol)
        if not text:
            return None
        return parse_research_records(text, symbol=symbol, stock_name=stock_name)


__all__ = [
    "parse_holders",
    "parse_holders_records",
    "parse_holders_format_b",
    "parse_holders_auto",
    "parse_controlling_shareholder",
    "parse_controlling_shareholder_format_b",
    "parse_shareholder_plans",
    "parse_shareholder_plans_format_b",
    "parse_shareholder_trades",
    "parse_shareholder_trades_format_b",
    "parse_holder_count_history_format_b",
    "parse_common_major_holder_stocks_format_b",
    "parse_fund_holdings_format_b",
    "parse_research",
    "parse_research_records",
    "detect_f10_format",
    "fetch_holders",
    "fetch_holders_text",
    "fetch_research",
    "HolderFetcher",
]
