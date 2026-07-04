"""continuity_guard — 数据连续性消费侧硬门 (2026-07-03, 用户定调"像交易日历一样强制")。

分层执法设计 (与 universe 硬门同构):
  - 采集侧: check_continuity_integrity.py 每日跑批尾全库扫 → ALERT flag (告警级, 不阻断采集 —
    中断 daily_update 比缺口更糟);
  - **消费侧 (本模块, 硬门)**: 策略/GT/消融读某域数据前 assert_domains_continuous —
    带缺口的数据进研究 = 错误结论, 违规即 raise, 如同非交易日不能下单。
实时单域 SQL 检查 (不依赖 stale 审查报告); known_empty_days 墓碑与 gap_tolerance: annotate 域放行。
接线: rally_gt.rebuild 入口 (与 holdout/universe 门并列第三道)。D2 消融 builder 未来必接。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REG_PATH = Path(__file__).resolve().parent.parent / "config" / "sync_registry.yaml"


class ContinuityGapError(RuntimeError):
    """消费的数据域存在未豁免的日历缺口 — 拒绝把缺口数据喂进研究/策略。"""


def assert_domains_continuous(domains: list[str], conn, *, end_date: str | None = None) -> dict:
    """指定域对交易日历零中间缺口断言 (conn 须可解析 tr.* 与 ref.dim_trading_calendar)。

    end_date (compact): 检查上界, 默认交易日历内最新已闭合交易日; 尾部滞后不算缺口
    (由采集侧 SLA 告警管), 只抓**中间空洞** — 研究致命的是历史断层非最新一天。
    返回 {domain: {checked_days, gaps}}; 任何未豁免 gap → ContinuityGapError。
    """
    reg = yaml.safe_load(_REG_PATH.read_text(encoding="utf-8"))["domains"]
    out: dict = {}
    problems: list[str] = []
    for d in domains:
        spec = reg.get(d)
        if spec is None:
            raise ContinuityGapError(f"域 {d!r} 不在 sync_registry — 消费未注册域违反宪法第 7 条")
        if spec.get("batch_mode") not in ("by_trade_date", "by_date_range"):
            out[d] = {"checked_days": 0, "gaps": [], "note": "非日频域, 连续性语义不适用"}
            continue
        if str(spec.get("gap_tolerance", "none")) == "annotate":
            out[d] = {"checked_days": 0, "gaps": [], "note": "gap_tolerance=annotate 豁免域"}
            continue
        table = spec["target_table"]
        start = str(spec["data_start"])
        tombstones = {str(x).replace("-", "") for x in (spec.get("known_empty_days") or [])}
        rows = conn.execute(f"""
            SELECT replace(c.trade_date, '-', '') AS d
            FROM ref.dim_trading_calendar c
            WHERE c.is_trading = 1
              AND replace(c.trade_date, '-', '') >= ?
              AND replace(c.trade_date, '-', '') <= COALESCE(?, (
                    SELECT MAX(trade_date) FROM tr.{table}))
              AND replace(c.trade_date, '-', '') NOT IN (
                    SELECT DISTINCT trade_date FROM tr.{table})
            ORDER BY 1""", [start, end_date]).fetchall()
        gaps = [r[0] for r in rows if r[0] not in tombstones]
        n = conn.execute("""
            SELECT COUNT(*) FROM ref.dim_trading_calendar
            WHERE is_trading = 1 AND replace(trade_date,'-','') >= ?""", [start]).fetchone()[0]
        out[d] = {"checked_days": n, "gaps": gaps[:20]}
        if gaps:
            problems.append(f"{d}: {len(gaps)} 个未豁免中间缺口 (样例 {gaps[:5]})")
    if problems:
        raise ContinuityGapError(
            "数据连续性硬门: 消费域存在中间缺口, 拒绝喂进研究/策略 (缺口=错误结论) — "
            + "; ".join(problems)
            + "。修法: drain 重放补缺 / 实弹核证真空日进 known_empty_days 墓碑。")
    return out
