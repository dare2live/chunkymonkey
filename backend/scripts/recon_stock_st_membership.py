#!/usr/bin/env python3
"""stock_st 双水库口径核对 —— 只读诊断脚本 (2026-09-18, ST 契约 v2 刀3, spec 附录
§7.1 / §6 步 4-5)。

**这是什么**: 比较某一天 accepted (``canonical_stock_st_daily``——名称快照路径或
历史 tushare 原行) 与 ``raw_baostock_daily_k`` 水库 (isST) 两条独立路径各自算出
的 ST 成员集合是否一致。这是主循环联网实测阶段 (spec §7.2 U1/U2) 唯一廉价的
交叉核验手段——两条路径互相独立取数 (一个读名称快照/历史供应商行, 一个读
baostock isST), 若长期吻合就是"两个源看到同一件事"的证据; 一旦不吻合, 本脚本
只负责把差异摆出来, **不判断哪边对、不吞任何一只差异码、不做任何"看起来像
误差就忽略"的近似**——沪深段任何差异都必须停下来逐码人工核实 (spec §6 步 4:
"任一沪深差异 -> 停, 逐码查 ifind 戴帽摘帽事件后再决定")。.BJ 只在 accepted 侧
是**预期**差异 (baostock 结构上不覆盖北交所), 不是需要停下来的那种差异。

只读, 不写库、不触网: 两个来源都已经是本地水库/canonical 表。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_sources.stock_st_acquire_rules import (  # noqa: E402
    StockStAcquireRules,
    load_stock_st_acquire_rules,
)


def _exchange(ts_code: str) -> str:
    return ts_code.rsplit(".", 1)[-1].upper() if "." in ts_code else ""


@dataclass(frozen=True)
class MembershipComparison:
    accepted_n: int
    reservoir_n: int
    both_n: int
    only_accepted: tuple[dict[str, str], ...]
    only_reservoir: tuple[dict[str, str], ...]


def compare(
    accepted_codes: Iterable[str], reservoir_codes: Iterable[str]
) -> MembershipComparison:
    """纯函数, 不开库不触网: 两个 ts_code 集合 -> 差异报告。

    不判断哪边"对", 不吞任何一只差异码, 不做近似匹配——把一只码的 isST 从
    ``'1'`` 改成 ``'0'`` 之后, 它必须原样出现在 ``only_accepted`` 里 (accepted
    侧仍标它是成员而 reservoir 侧不再标)。
    """

    accepted_set = {str(c).strip().upper() for c in accepted_codes if str(c).strip()}
    reservoir_set = {str(c).strip().upper() for c in reservoir_codes if str(c).strip()}
    only_accepted = sorted(accepted_set - reservoir_set)
    only_reservoir = sorted(reservoir_set - accepted_set)
    both_n = len(accepted_set & reservoir_set)
    return MembershipComparison(
        accepted_n=len(accepted_set),
        reservoir_n=len(reservoir_set),
        both_n=both_n,
        only_accepted=tuple(
            {"ts_code": c, "exchange": _exchange(c)} for c in only_accepted
        ),
        only_reservoir=tuple(
            {"ts_code": c, "exchange": _exchange(c)} for c in only_reservoir
        ),
    )


_PRE_V2_SCHEMA_ORIGIN_LABEL = "pre_v2_schema_no_st_origin_column"


def _table_has_column(conn: Any, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ? LIMIT 1",  # rule-compliance: ok evidence=probe-column-existence-before-selecting-it-not-a-business-threshold
        [table, column],
    ).fetchone()
    return row is not None


def load_accepted_codes(conn: Any, trade_date: str) -> tuple[set[str], tuple[str, ...]]:
    """只读: ``canonical_stock_st_daily`` 该日全部 ts_code, 及其 distinct
    ``st_origin`` 集合 (排序后返回, 供 CLI 打印 ``accepted_origin``——一个分区
    理论上只有一种 origin, 但本函数不假设, 原样报告实际观测到的集合)。

    B3 修法 (2026-09-19 返修): spec §6 把本脚本 (步 4/5) 排在契约重打 (步 6/7)
    **之前**——那正是它存在的意义 (重打前用两条独立路径交叉核验历史口径)。
    但 ``st_origin`` 是重打才加的列, 重打前 ``canonical_stock_st_daily`` 还是
    v1 形状, 原来的 ``SELECT ts_code, st_origin`` 对任何合法 ``--trade-date``
    都会 ``BinderException: column "st_origin" not found``——这就是"对任何合法
    trade-date 都崩"的根因(实测复现, 见 test_stock_st_backfill_cut.py 的
    B3 用例)。先探列存不存在, 不存在就退化成只报 ts_code 集合 + 一个明确的
    哨兵 origin 标签 (不是伪造一个真实 st_origin 取值, 是诚实地说"这个信息在
    v1 表上不存在")。"""

    day = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))
    if _table_has_column(conn, "canonical_stock_st_daily", "st_origin"):
        rows = conn.execute(
            "SELECT ts_code, st_origin FROM canonical_stock_st_daily WHERE trade_date = ?",
            [day],
        ).fetchall()
        codes = {str(r[0]) for r in rows}
        origins = tuple(sorted({str(r[1]) for r in rows}))
        return codes, origins

    rows = conn.execute(
        "SELECT ts_code FROM canonical_stock_st_daily WHERE trade_date = ?",
        [day],
    ).fetchall()
    codes = {str(r[0]) for r in rows}
    origins = (_PRE_V2_SCHEMA_ORIGIN_LABEL,) if codes else ()
    return codes, origins


def load_reservoir_member_codes(
    conn: Any, trade_date: str, rules: StockStAcquireRules
) -> set[str]:
    """只读: ``raw_baostock_daily_k`` 该日每码最新一版里 isST ==
    ``isst_true_value`` 的 ts_code 集合。``fields_csv`` 不含 isST 的行不计入
    (与派生器 ``fetch_raw`` 的水库路径同一判据, 不重新发明第二套过滤逻辑)。"""

    from services.data_sources.baostock_daily_k_reservoir import latest_rows_for_date

    isst_field = rules.reservoir_isst_field
    true_value = rules.reservoir_isst_true_value
    rows = latest_rows_for_date(conn, trade_date)
    return {
        str(r.ts_code)
        for r in rows
        if isst_field in str(r.fields_csv).split(",")
        and str(r.payload.get(isst_field)) == true_value
    }


def probe_isst(
    conn: Any, probes: Sequence[tuple[str, str]], rules: StockStAcquireRules
) -> list[dict[str, Any]]:
    """``--probe`` 逐个 (ts_code, trade_date) 查水库最新一版的 isST **原始值**
    (不做真假判断; ``None`` 表示水库该日没有这只码的行, 与"isST 为假"是两件
    不同的事, 不得混淆)。字段名从 ``rules.reservoir_isst_field`` 现读 (同
    ``load_reservoir_member_codes``), 不留字面量副本——这里曾经是唯一一处退回
    硬编码 ``"isST"`` 的地方 (B4 返修点名), 不得重新长出来。"""

    from services.data_sources.baostock_daily_k_reservoir import latest_rows_for_date

    isst_field = rules.reservoir_isst_field
    result: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    for ts_code, trade_date in probes:
        if trade_date not in cache:
            cache[trade_date] = {
                str(r.ts_code): r for r in latest_rows_for_date(conn, trade_date)
            }
        row = cache[trade_date].get(ts_code)
        result.append(
            {
                "ts_code": ts_code,
                "date": trade_date,
                isst_field: None if row is None else row.payload.get(isst_field),
            }
        )
    return result


def _parse_probe(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        ts_code, _, trade_date = item.partition(":")
        if not ts_code or not trade_date:
            raise ValueError(
                "recon_stock_st_membership: --probe 项形状须为 TS_CODE:YYYYMMDD, "
                f"got {item!r}"
            )
        out.append((ts_code.strip(), trade_date.strip()))
    return out


def run(*, trade_date: str, db_override: str | None, probe: str | None) -> dict[str, Any]:
    from services.data_access.resolver import db_path
    from services.duck_adapter import connect

    rules = load_stock_st_acquire_rules()
    target = db_override or str(db_path("tushare_raw"))
    conn = connect(target, read_only=True)
    try:
        accepted_codes, accepted_origin = load_accepted_codes(conn, trade_date)
        reservoir_codes = load_reservoir_member_codes(conn, trade_date, rules)
        comparison = compare(accepted_codes, reservoir_codes)
        probes = _parse_probe(probe) if probe else []
        isst_probe = probe_isst(conn, probes, rules) if probes else []
    finally:
        conn.close()

    return {
        "trade_date": trade_date,
        "accepted_origin": list(accepted_origin),
        "accepted_n": comparison.accepted_n,
        "reservoir_n": comparison.reservoir_n,
        "both_n": comparison.both_n,
        "only_accepted": list(comparison.only_accepted),
        "only_reservoir": list(comparison.only_reservoir),
        "isst_probe": isst_probe,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trade-date", required=True, help="YYYYMMDD")
    parser.add_argument("--db-override", default=None, help="改读目标库路径")
    parser.add_argument(
        "--probe", default=None,
        help=(
            "TS_CODE:YYYYMMDD[,TS_CODE:YYYYMMDD...] —— 逐个探查水库该 (码,日) 的 "
            "reservoir.isst_field 原始值"
        ),
    )
    args = parser.parse_args(argv)
    if not (len(args.trade_date) == 8 and args.trade_date.isdigit()):
        print(
            f"recon_stock_st_membership: --trade-date 必须是 YYYYMMDD, got {args.trade_date!r}",
            file=sys.stderr,
        )
        return 2
    try:
        result = run(
            trade_date=args.trade_date, db_override=args.db_override, probe=args.probe
        )
    except ValueError as exc:
        print(f"recon_stock_st_membership: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
