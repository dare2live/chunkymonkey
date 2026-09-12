"""交易所格级裁决登记表: loader + 纯消费函数。

背景见 ``scratchpad/bt_residual_classes_r1.md`` §2-§3 (r1)。核心概念:

- T 格 (``CellKey``): ``(trade_date, venue, code6, price2dp, vol2dp)``。price/vol
  已按 2 位小数字符串归一, 是比较双方 (交易所逐笔证据 / 供应商重落新表) 落到
  同一格的粒度。
- 一格两侧各自的"未匹配行"是多重集 (``Counter[Row]``, ``Row = (buyer, seller)``
  两个席位名字符串对), 不是单行、也不是计数。
- 登记表 (``backend/config/exchange_cell_verdicts.yaml``) 对每个 T 格钉住两侧
  未匹配行的完整多重集 + 可选的逐行配对 (``text_pairs``, 表示"这行缺"与"那行
  多"其实是同一笔只是席位名写法不同) + 谁对 (``truth_side``) + 证据 + 核验日期。

本模块不开数据库、不读环境变量、不做任何 I/O 之外的副作用 (只读登记文件本
身); 所有输入校验失败一律抛 ``ValueError``, 消息里带 ``entries[i]`` 定位与
出错的字段名, 方便人在几十条登记里定位是哪一条、哪个键写错了。

``load_exchange_cell_verdicts`` 与 ``consume`` 是两个独立职责:

- ``load_exchange_cell_verdicts``: 把 YAML 校验成类型化的 ``CellVerdictSet``。
  它只管"这份登记表本身写得对不对", 不看任何观测数据。
- ``consume``: 拿一份已经校验过的 ``CellVerdictSet`` 去对账观测到的残差
  (``Observed``), 判断每个格是被登记消费 (``consumed``)、未登记
  (``unregistered``)、登记已经过时 (``stale``) 还是登记本身自相矛盾
  (``contradiction``)。它不读文件、不做校验, 假设传入的 ``CellVerdictSet``
  已经通过 ``load_exchange_cell_verdicts``。

登记表不能掩盖回归的四个钉子 (§3.2, 由这两个函数共同实现):
1. ``exchange_unmatched`` / ``vendor_unmatched`` 必须与观测逐字节相等 (多重
   集), 一旦任一侧漂移, 该格判 ``stale``——不会拿旧登记继续解释新形状。
2. ``text_pairs`` 指向具体下标, 配对行如果在观测里找不到 (哪怕只差一个字),
   判 ``contradiction``——不会把"配对错了"悄悄吞成"这格没问题"。
3. 剩余行的动作 (missing / extra_duplicate / extra_phantom) 完全由"它在哪一
   侧、交易所是否有同六键行"这两个观测事实决定, 登记本身没有字段可以覆盖它。
4. 每次 ``consume`` 都用分区不变量自检 (Σ 每格消费计数 == Σ 观测行数), 破了
   直接 ``AssertionError``, 不会把记账算错的格悄悄放过去。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from services.data_sources.assignment_gap_recon import normalize_cn_name

CellKey = tuple[str, str, str, str, str]
"""``(trade_date, venue, code6, price2dp, vol2dp)``。全部是归一后的字符串。"""

Row = tuple[str, str]
"""``(buyer_n, seller_n)`` —— 归一后的席位名字符串对。"""

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _REPO_ROOT / "backend" / "config" / "exchange_cell_verdicts.yaml"

_TOP_LEVEL_KEYS = {"version", "entries"}
_ENTRY_KEYS = {
    "domain",
    "cells",
    "exchange_unmatched",
    "vendor_unmatched",
    "text_pairs",
    "truth_side",
    "evidence",
    "checked_at",
}
_TRUTH_SIDES = {"exchange", "vendor", "unknown"}
_VENUES = {"sh", "sz"}

_DATE_RE = re.compile(r"^\d{8}$")
_CODE_RE = re.compile(r"^\d{6}$")
_DECIMAL2_RE = re.compile(r"^\d+\.\d{2}$")
_CHECKED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 只有声明了非空 cell_cols 的域才支持格级登记。这份表是本模块自己的、独立
# 声明 (本模块不连库、不导入脚本层依赖, 不从 backend/scripts/reland_event_
# domain.py 的 CanonSpec 读取——那边未来会各自独立声明同名概念, 两处不共享
# 同一个 dict, 这是有意的解耦, 不是遗漏同步)。``top_inst`` 在这里显式列出
# 空元组, 是为了表达"这个域存在, 但目前没有声明它的格列", 不是没想到它。
_DOMAIN_CELL_COLS: Mapping[str, tuple[str, ...]] = {
    "block_trade": ("price", "vol"),
    "top_inst": (),
}


@dataclass(frozen=True)
class CellEntry:
    domain: str
    cells: tuple[CellKey, ...]
    exchange_unmatched: tuple[tuple[Row, int], ...]
    vendor_unmatched: tuple[tuple[Row, int], ...]
    text_pairs: tuple[tuple[int, int, int], ...]
    truth_side: str
    evidence: str
    checked_at: str


@dataclass(frozen=True)
class CellVerdictSet:
    entries: tuple[CellEntry, ...]
    by_cell: Mapping[CellKey, CellEntry]
    sha256: str


@dataclass(frozen=True)
class Observed:
    """一个 T 格的观测残差。

    ``gap`` = 交易所有、新表没有的行 (E-N); ``extra`` = 新表有、交易所没有的行
    (N-E); ``exchange_all`` = 该格交易所侧的全量行 (E_T, 不是残差), 供
    duplicate/phantom 子类判定 (E[F] >= 1 => duplicate, 否则 phantom)。
    """

    gap: Counter
    extra: Counter
    exchange_all: Counter


@dataclass(frozen=True)
class ConsumptionReport:
    per_cell: Mapping[CellKey, Mapping[str, object]]
    totals: Mapping[str, int]
    consumed_cells: tuple[CellKey, ...]
    stale_cells: tuple[CellKey, ...]


def load_exchange_cell_verdicts(path: Path | None = None) -> CellVerdictSet:
    """读取并校验登记表 YAML, 返回类型化的 ``CellVerdictSet``。

    纯配置校验: 不开数据库、不读环境变量。全部校验失败抛 ``ValueError``,
    消息含 ``entries[i]`` 与出错的键名, 方便定位。
    """
    target = path if path is not None else _DEFAULT_PATH
    raw_bytes = target.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    document = yaml.safe_load(raw_bytes)
    if document is None:
        document = {}

    if not isinstance(document, dict) or set(document.keys()) != _TOP_LEVEL_KEYS:
        got = sorted(document.keys()) if isinstance(document, dict) else type(document).__name__
        raise ValueError(
            f"exchange_cell_verdicts top-level keys must be exactly "
            f"{sorted(_TOP_LEVEL_KEYS)}, got {got}"
        )

    version = document["version"]
    if version != 1:
        raise ValueError(f"exchange_cell_verdicts version must be 1, got {version!r}")

    raw_entries = document["entries"]
    if not isinstance(raw_entries, list):
        raise ValueError("exchange_cell_verdicts entries must be a list")

    entries: list[CellEntry] = []
    seen_cells: dict[CellKey, int] = {}

    for i, raw_entry in enumerate(raw_entries):
        entry = _load_entry(i, raw_entry)
        for cell in entry.cells:
            if cell in seen_cells:
                raise ValueError(
                    f"entries[{i}]: duplicate cell {cell!r} already registered "
                    f"by entries[{seen_cells[cell]}]"
                )
            seen_cells[cell] = i
        entries.append(entry)

    by_cell: dict[CellKey, CellEntry] = {}
    for entry in entries:
        for cell in entry.cells:
            by_cell[cell] = entry

    return CellVerdictSet(entries=tuple(entries), by_cell=by_cell, sha256=sha256)


def _load_entry(i: int, raw_entry: object) -> CellEntry:
    prefix = f"entries[{i}]"

    if not isinstance(raw_entry, dict) or set(raw_entry.keys()) != _ENTRY_KEYS:
        got = sorted(raw_entry.keys()) if isinstance(raw_entry, dict) else type(raw_entry).__name__
        raise ValueError(f"{prefix} keys must be exactly {sorted(_ENTRY_KEYS)}, got {got}")

    domain = raw_entry["domain"]
    if not isinstance(domain, str) or not _DOMAIN_CELL_COLS.get(domain):
        raise ValueError(
            f"{prefix} domain {domain!r} has no declared cell_cols "
            f"(not supported for cell-level verdicts)"
        )

    raw_cells = raw_entry["cells"]
    if not isinstance(raw_cells, list) or len(raw_cells) == 0:
        raise ValueError(f"{prefix} cells must be a non-empty list")
    cells = tuple(_load_cell(i, j, raw_cell) for j, raw_cell in enumerate(raw_cells))

    exchange_unmatched = _load_unmatched(prefix, "exchange_unmatched", raw_entry["exchange_unmatched"])
    vendor_unmatched = _load_unmatched(prefix, "vendor_unmatched", raw_entry["vendor_unmatched"])

    if not exchange_unmatched and not vendor_unmatched:
        raise ValueError(
            f"{prefix} exchange_unmatched and vendor_unmatched explains nothing: both empty"
        )

    text_pairs = _load_text_pairs(prefix, raw_entry["text_pairs"], exchange_unmatched, vendor_unmatched)

    truth_side = raw_entry["truth_side"]
    if truth_side not in _TRUTH_SIDES:
        raise ValueError(f"{prefix} truth_side must be one of {sorted(_TRUTH_SIDES)}, got {truth_side!r}")

    evidence = raw_entry["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError(f"{prefix} evidence must be a non-empty string")

    checked_at = raw_entry["checked_at"]
    if not isinstance(checked_at, str) or not _CHECKED_AT_RE.match(checked_at):
        raise ValueError(f"{prefix} checked_at must match YYYY-MM-DD, got {checked_at!r}")

    return CellEntry(
        domain=domain,
        cells=cells,
        exchange_unmatched=exchange_unmatched,
        vendor_unmatched=vendor_unmatched,
        text_pairs=text_pairs,
        truth_side=truth_side,
        evidence=evidence,
        checked_at=checked_at,
    )


def _load_cell(i: int, j: int, raw_cell: object) -> CellKey:
    prefix = f"entries[{i}] cells[{j}]"
    if not isinstance(raw_cell, (list, tuple)) or len(raw_cell) != 5:
        raise ValueError(
            f"{prefix} must be a 5-element [trade_date, venue, code, price, vol], got {raw_cell!r}"
        )

    trade_date, venue, code, price2, vol2 = raw_cell

    if not isinstance(trade_date, str) or not _DATE_RE.match(trade_date):
        raise ValueError(f"{prefix} trade_date must match YYYYMMDD (8 digits), got {trade_date!r}")
    if venue not in _VENUES:
        raise ValueError(f"{prefix} venue must be one of {sorted(_VENUES)}, got {venue!r}")
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise ValueError(f"{prefix} code must be 6 digits, got {code!r}")
    if not isinstance(price2, str) or not _DECIMAL2_RE.match(price2):
        raise ValueError(f"{prefix} price must match \\d+\\.\\d{{2}}, got {price2!r}")
    if not isinstance(vol2, str) or not _DECIMAL2_RE.match(vol2):
        raise ValueError(f"{prefix} vol must match \\d+\\.\\d{{2}}, got {vol2!r}")

    return (trade_date, venue, code, price2, vol2)


def _load_unmatched(prefix: str, field_name: str, raw_rows: object) -> tuple[tuple[Row, int], ...]:
    if not isinstance(raw_rows, list):
        raise ValueError(f"{prefix} {field_name} must be a list")

    rows: list[tuple[Row, int]] = []
    for k, raw_row in enumerate(raw_rows):
        row_prefix = f"{prefix} {field_name}[{k}]"
        if not isinstance(raw_row, (list, tuple)) or len(raw_row) != 3:
            raise ValueError(f"{row_prefix} must be [buyer, seller, count], got {raw_row!r}")

        buyer, seller, count = raw_row
        if not isinstance(buyer, str) or not isinstance(seller, str):
            raise ValueError(f"{row_prefix} buyer/seller must be strings, got {raw_row!r}")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"{row_prefix} count must be an integer >= 1, got {count!r}")
        if buyer != normalize_cn_name(buyer):
            raise ValueError(
                f"{row_prefix} buyer is not normalize_cn_name-stable (register the normalized "
                f"form): {buyer!r}"
            )
        if seller != normalize_cn_name(seller):
            raise ValueError(
                f"{row_prefix} seller is not normalize_cn_name-stable (register the normalized "
                f"form): {seller!r}"
            )
        rows.append(((buyer, seller), count))

    return tuple(rows)


def _load_text_pairs(
    prefix: str,
    raw_pairs: object,
    exchange_unmatched: tuple[tuple[Row, int], ...],
    vendor_unmatched: tuple[tuple[Row, int], ...],
) -> tuple[tuple[int, int, int], ...]:
    if not isinstance(raw_pairs, list):
        raise ValueError(f"{prefix} text_pairs must be a list")

    ex_remaining = [count for _, count in exchange_unmatched]
    vn_remaining = [count for _, count in vendor_unmatched]

    pairs: list[tuple[int, int, int]] = []
    for k, raw_pair in enumerate(raw_pairs):
        pair_prefix = f"{prefix} text_pairs[{k}]"
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 3:
            raise ValueError(f"{pair_prefix} must be [exchange_idx, vendor_idx, count], got {raw_pair!r}")

        ex_idx, vn_idx, count = raw_pair
        if not isinstance(ex_idx, int) or isinstance(ex_idx, bool) or not (0 <= ex_idx < len(ex_remaining)):
            raise ValueError(
                f"{pair_prefix} exchange index {ex_idx!r} out of range for "
                f"exchange_unmatched (len={len(ex_remaining)})"
            )
        if not isinstance(vn_idx, int) or isinstance(vn_idx, bool) or not (0 <= vn_idx < len(vn_remaining)):
            raise ValueError(
                f"{pair_prefix} vendor index {vn_idx!r} out of range for "
                f"vendor_unmatched (len={len(vn_remaining)})"
            )
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"{pair_prefix} count must be an integer >= 1, got {count!r}")

        ex_remaining[ex_idx] -= count
        vn_remaining[vn_idx] -= count
        if ex_remaining[ex_idx] < 0 or vn_remaining[vn_idx] < 0:
            raise ValueError(
                f"{pair_prefix} consumes more than the declared/remaining share at "
                f"exchange_idx={ex_idx}, vendor_idx={vn_idx}"
            )

        pairs.append((ex_idx, vn_idx, count))

    return tuple(pairs)


def _check_registration_validity(entry: CellEntry, obs: Observed) -> str:
    """判断一条登记 (对某一格) 相对当前观测是 ``consumed``、``stale`` 还是
    ``contradiction``。

    顺序有意义: 若该格观测到的两侧残差都已清空 (格已经 matched, 登记彻底没
    有对象可解释), 直接判 ``stale``, 不去看 ``text_pairs`` 引用了什么——"格
    已 matched"本身就足以让登记失效, 与配对内容是否巧合"看似合法"无关。只有
    格上还有残差时, 才先查 ``text_pairs`` 引用的具体行是否真的存在于观测里
    (哪怕只差一个字, 也会在这里被抓到, 判 ``contradiction``); 配对本身站得
    住脚, 才去比两侧声明的完整多重集是否与观测逐字节相等 (不等 => ``stale``)。
    这样"配对指错行"与"登记跟不上事实"两种失效原因不会被混成一个状态。
    """
    if not obs.gap and not obs.extra:
        return "stale"

    for ex_idx, vn_idx, count in entry.text_pairs:
        ex_row, _ = entry.exchange_unmatched[ex_idx]
        vn_row, _ = entry.vendor_unmatched[vn_idx]
        if obs.gap.get(ex_row, 0) <= 0 or obs.extra.get(vn_row, 0) <= 0:
            return "contradiction"

    declared_gap: Counter = Counter()
    for row, count in entry.exchange_unmatched:
        declared_gap[row] += count
    declared_extra: Counter = Counter()
    for row, count in entry.vendor_unmatched:
        declared_extra[row] += count

    if declared_gap != obs.gap or declared_extra != obs.extra:
        return "stale"

    return "consumed"


def _consume_cell(entry: CellEntry, obs: Observed) -> dict[str, object]:
    """已确认有效 (``consumed``) 的一格: 按 text_pairs 消费, 剩余 exchange 行
    记 ``missing``, 剩余 vendor 行按 ``exchange_all`` 是否含同 Row 分
    ``extra_duplicate`` / ``extra_phantom``。"""
    ex_consumed = [0] * len(entry.exchange_unmatched)
    vn_consumed = [0] * len(entry.vendor_unmatched)
    text_total = 0
    for ex_idx, vn_idx, count in entry.text_pairs:
        ex_consumed[ex_idx] += count
        vn_consumed[vn_idx] += count
        text_total += count

    missing_total = sum(
        count - consumed for (_, count), consumed in zip(entry.exchange_unmatched, ex_consumed)
    )

    extra_duplicate = 0
    extra_phantom = 0
    for (row, count), consumed in zip(entry.vendor_unmatched, vn_consumed):
        remaining = count - consumed
        if remaining <= 0:
            continue
        if obs.exchange_all.get(row, 0) >= 1:
            extra_duplicate += remaining
        else:
            extra_phantom += remaining

    return {
        "status": "consumed",
        "text": text_total,
        "missing": missing_total,
        "extra_duplicate": extra_duplicate,
        "extra_phantom": extra_phantom,
        "text_candidate": 0,
        "missing_unregistered": 0,
        "extra_unregistered": 0,
    }


def _classify_unregistered(obs: Observed) -> dict[str, object]:
    """未被任何登记覆盖的格: §2.2 的临时分类, 只用来定退出码, 不是人工裁决的
    替代品。"""
    gap_total = sum(obs.gap.values())
    extra_total = sum(obs.extra.values())
    text_candidate = min(gap_total, extra_total)
    missing_unregistered = gap_total - text_candidate
    extra_unregistered = extra_total - text_candidate
    return {
        "status": "unregistered",
        "text": 0,
        "missing": 0,
        "extra_duplicate": 0,
        "extra_phantom": 0,
        "text_candidate": text_candidate,
        "missing_unregistered": missing_unregistered,
        "extra_unregistered": extra_unregistered,
    }


_STALE_STATUSES = ("stale", "contradiction")
_COUNT_FIELDS = (
    "text",
    "missing",
    "extra_duplicate",
    "extra_phantom",
    "text_candidate",
    "missing_unregistered",
    "extra_unregistered",
)


def consume(observed: Mapping[CellKey, Observed], verdicts: CellVerdictSet) -> ConsumptionReport:
    """用登记表对观测残差做逐格消费, 返回 ``ConsumptionReport``。

    每格独立处理 (同一条 entry 覆盖多个 cells 时, 每个 cell 各自对着自己的
    ``observed`` 条目判断, §6 M12): 不在 ``verdicts.by_cell`` 里的格走
    ``unregistered`` 分类; 在的格先判有效性 (``stale``/``contradiction``),
    有效才真正消费。

    分区不变量: 对每个 ``consumed``/``unregistered`` 格, 消费计数之和必须等
    于该格观测到的 (gap+extra) 行数之和; 全局汇总后若对不上, 说明消费逻辑本
    身算错了 (不是登记表或观测数据的问题), 抛 ``AssertionError``。``stale``/
    ``contradiction`` 格不计入这个不变量 (它们的定义就是"登记跟观测对不上",
    没有什么可以拿来配平)。
    """
    per_cell: dict[CellKey, dict[str, object]] = {}
    consumed_cells: list[CellKey] = []
    stale_cells: list[CellKey] = []
    totals: dict[str, int] = {field: 0 for field in _COUNT_FIELDS}

    invariant_lhs = 0
    invariant_rhs = 0

    all_cells = set(observed.keys()) | set(verdicts.by_cell.keys())
    for cell in all_cells:
        entry = verdicts.by_cell.get(cell)
        obs = observed.get(cell)
        if obs is None:
            obs = Observed(gap=Counter(), extra=Counter(), exchange_all=Counter())

        if entry is None:
            result = _classify_unregistered(obs)
            per_cell[cell] = result
            for field in _COUNT_FIELDS:
                totals[field] += result[field]
            invariant_lhs += (
                result["text_candidate"] * 2
                + result["missing_unregistered"]
                + result["extra_unregistered"]
            )
            invariant_rhs += sum(obs.gap.values()) + sum(obs.extra.values())
            continue

        status = _check_registration_validity(entry, obs)
        if status in _STALE_STATUSES:
            per_cell[cell] = {
                "status": status,
                "text": 0,
                "missing": 0,
                "extra_duplicate": 0,
                "extra_phantom": 0,
                "text_candidate": 0,
                "missing_unregistered": 0,
                "extra_unregistered": 0,
            }
            stale_cells.append(cell)
            continue

        result = _consume_cell(entry, obs)
        per_cell[cell] = result
        consumed_cells.append(cell)
        for field in _COUNT_FIELDS:
            totals[field] += result[field]
        invariant_lhs += (
            result["text"] * 2 + result["missing"] + result["extra_duplicate"] + result["extra_phantom"]
        )
        invariant_rhs += sum(obs.gap.values()) + sum(obs.extra.values())

    if invariant_lhs != invariant_rhs:
        raise AssertionError(
            f"exchange_cell_verdicts partition invariant broken: consumed/unregistered "
            f"accounting sums to {invariant_lhs}, observed gap+extra sums to {invariant_rhs}"
        )

    return ConsumptionReport(
        per_cell=per_cell,
        totals=totals,
        consumed_cells=tuple(consumed_cells),
        stale_cells=tuple(stale_cells),
    )
