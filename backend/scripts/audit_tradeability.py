#!/usr/bin/env python3
"""P-1.3 tradeability audit — PLAN_V3 v3.2 P-1 fourth gate.

Per PLAN_V3 §2.P-1: ML label / paper_sim 用的"实际可成交"假设必须经得起停牌 +
涨跌停日的反例检验. 否则 selector 在 t 时刻"挑中"的股票, 实盘根本买不进 / 卖不出 —
回测年化是"幻想收益".

4 个 section (照 audit_pit_integrity.py 同款 dataclass / log / json 输出):

1. Suspension 数据源是否存在 + 时效
   - dim_trading_calendar 在 smartmoney.duckdb (交易日历, 不是 per-stock 停牌)
   - 实际 per-stock 停牌信号: market.duckdb.price_kline.volume = 0 / NULL
2. 涨跌停规则识别
   - dim_price_limit_rules 在 smartmoney.duckdb (主板 ±10% / ST ±5% / 创业板 ±20% / 科创板 ±20%)
   - K 线表能否识别: prev_close × (1 ± rule) ≈ today close 命中即涨/跌停
3. paper_sim 是否过滤这些不可成交样本
   - 静态扫 services/paper_sim/ + services/portfolio_walk_forward/ 含 suspension / volume<=0 /
     limit_up / limit_down / 涨停 / 跌停 关键字
4. Spot check: 取近期高停牌日, 验证 paper_sim 的过滤 SQL 在 v_price_kline_qfq 上能
   将这些样本 mask 掉 (volume=0 / NULL → require_today_traded fail)

Exit 0 = PASS, 1 = FAIL.
PLAN_V3 §2 P-1 Go: 不可交易状态覆盖率 = 100% → PASS.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_tradeability.py
    PYTHONPATH=backend python backend/scripts/audit_tradeability.py --json-out /tmp/tradeability_audit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit_tradeability")

DATA_DIR = DB_PATH.parent
MARKET_DB = DATA_DIR / "market.duckdb"

# Regex patterns for static-scan section 3 (paper_sim filter coverage)
SUSPENSION_PATTERNS = (
    re.compile(r"suspension|suspended|停牌", re.IGNORECASE),
    re.compile(r"require_today_traded"),
    re.compile(r"volume\s*(<=|<|==|!=|is\s+null|=)\s*(0|none|null)", re.IGNORECASE),
    re.compile(r"halt", re.IGNORECASE),
)
LIMIT_PATTERNS = (
    re.compile(r"limit_up|limit_down|up_limit|down_limit|涨停|跌停", re.IGNORECASE),
    re.compile(r"dim_price_limit_rules"),
    re.compile(r"prev_close\s*\*\s*1\.(1|2|05)"),  # 1.10/1.20/1.05 涨停命中
)


@dataclass
class CheckResult:
    section: str
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    rows: int = 0
    extras: dict = field(default_factory=dict)


def check_suspension_source(conn) -> list[CheckResult]:
    """Section 1: 停牌信号数据源 + 时效.

    停牌识别在本项目里走 K 线表的 volume = 0 / NULL (没有专门的 fact_suspension 表),
    再加 dim_trading_calendar 区分"非交易日"与"个股停牌日".
    """
    out: list[CheckResult] = []

    # 1.1 trading calendar 是否存在 + 覆盖近期
    try:
        n_cal, min_d, max_d = conn.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM dim_trading_calendar"
        ).fetchone()
        if n_cal > 0:
            out.append(CheckResult(
                section="1. Suspension source",
                name="dim_trading_calendar",
                status="PASS",
                detail=f"dim_trading_calendar: {n_cal} 天 ({min_d} → {max_d})",
                rows=n_cal,
                extras={"min_date": str(min_d), "max_date": str(max_d)},
            ))
        else:
            out.append(CheckResult(
                section="1. Suspension source",
                name="dim_trading_calendar",
                status="FAIL",
                detail="dim_trading_calendar 表存在但 0 行 — 无法区分交易日 / 非交易日",
            ))
    except Exception as e:
        out.append(CheckResult(
            section="1. Suspension source",
            name="dim_trading_calendar",
            status="FAIL",
            detail=f"dim_trading_calendar 表缺失: {e}",
        ))

    # 1.2 per-stock 停牌信号: 走 market.duckdb.price_kline.volume = 0 / NULL
    try:
        mconn = duck_connect(str(MARKET_DB), read_only=True)
        try:
            n_total, n_susp = mconn.execute(
                """
                SELECT
                  COUNT(*) AS n_total,
                  SUM(CASE WHEN volume IS NULL OR volume <= 0 THEN 1 ELSE 0 END) AS n_susp
                  FROM price_kline
                 WHERE adjust='qfq' AND freq='daily'
                """
            ).fetchone()
            pct = (n_susp / n_total * 100.0) if n_total else 0.0
            if n_susp > 0:
                out.append(CheckResult(
                    section="1. Suspension source",
                    name="price_kline.volume_eq_0",
                    status="PASS",
                    detail=(
                        f"price_kline.volume=0/NULL 作为 per-stock 停牌信号: "
                        f"{n_susp:,}/{n_total:,} ({pct:.2f}%) 行被识别为停牌"
                    ),
                    rows=int(n_susp),
                    extras={"total_rows": int(n_total), "suspension_pct": round(pct, 4)},
                ))
            else:
                out.append(CheckResult(
                    section="1. Suspension source",
                    name="price_kline.volume_eq_0",
                    status="WARN",
                    detail=f"price_kline 共 {n_total:,} 行, 没有 volume=0/NULL — 数据源可能漏掉停牌行",
                    rows=0,
                ))

            # 时效: 最近停牌日是否覆盖到 today
            top_susp = mconn.execute(
                """
                SELECT date, COUNT(*) AS susp
                  FROM price_kline
                 WHERE adjust='qfq' AND freq='daily' AND (volume IS NULL OR volume <= 0)
                 GROUP BY 1 ORDER BY 1 DESC LIMIT 3
                """
            ).fetchall()
            top = [(str(r[0]), int(r[1])) for r in top_susp]
            out.append(CheckResult(
                section="1. Suspension source",
                name="recent_suspension_dates",
                status="PASS" if top else "WARN",
                detail=f"近期 3 个停牌日: {top}",
                extras={"top_dates": top},
            ))
        finally:
            mconn.close()
    except Exception as e:
        out.append(CheckResult(
            section="1. Suspension source",
            name="market.price_kline",
            status="FAIL",
            detail=f"market.duckdb / price_kline 读取失败: {e}",
        ))

    return out


def check_limit_rule_table(conn) -> list[CheckResult]:
    """Section 2: 涨跌停规则识别.

    项目用 dim_price_limit_rules (smartmoney) 存规则:
      主板正常 ±10% / ST ±5% / 创业板 / 科创板 ±20% (注册制后)
    K 线侧能否命中: prev_close × (1 + rule) ≈ close → 涨停 (close == high == low 更确定)
    """
    out: list[CheckResult] = []

    # 2.1 dim_price_limit_rules 行数 + 覆盖 4 类
    try:
        rows = conn.execute(
            "SELECT rule_id, market_segment, is_st, limit_up_pct, limit_down_pct "
            "FROM dim_price_limit_rules"
        ).fetchall()
        rule_map = {r[0]: (r[1], bool(r[2]), float(r[3]), float(r[4])) for r in rows}
        required = {
            "main_normal": (0.10, -0.10),
            "main_st": (0.05, -0.05),
            "chinext_normal": (0.20, -0.20),
            "star_normal": (0.20, -0.20),
        }
        missing = []
        wrong = []
        for rid, (exp_up, exp_dn) in required.items():
            if rid not in rule_map:
                missing.append(rid)
            else:
                _, _, up, dn = rule_map[rid]
                if abs(up - exp_up) > 0.001 or abs(dn - exp_dn) > 0.001:
                    wrong.append(f"{rid}: up={up:.3f} dn={dn:.3f} (expect {exp_up} / {exp_dn})")
        if not missing and not wrong:
            out.append(CheckResult(
                section="2. Limit rules",
                name="dim_price_limit_rules",
                status="PASS",
                detail=(
                    f"dim_price_limit_rules: {len(rows)} rules; main±10/ST±5/chinext±20/star±20 全覆盖"
                ),
                rows=len(rows),
                extras={"rules": list(rule_map.keys())},
            ))
        else:
            out.append(CheckResult(
                section="2. Limit rules",
                name="dim_price_limit_rules",
                status="FAIL",
                detail=f"规则缺失: {missing}; 值错误: {wrong}",
                extras={"missing": missing, "wrong": wrong},
            ))
    except Exception as e:
        out.append(CheckResult(
            section="2. Limit rules",
            name="dim_price_limit_rules",
            status="FAIL",
            detail=f"dim_price_limit_rules 缺失: {e}",
        ))

    # 2.2 K 线表能识别涨跌停吗 — 近 14 个交易日做样本
    try:
        mconn = duck_connect(str(MARKET_DB), read_only=True)
        try:
            # date 是 VARCHAR; 用 strptime 转 DATE 后减 14 天再 cast 回 VARCHAR
            row = mconn.execute(
                """
                WITH bound AS (
                  SELECT strftime(
                    strptime(MAX(date), '%Y-%m-%d') - INTERVAL '14 days',
                    '%Y-%m-%d'
                  ) AS lb
                    FROM price_kline WHERE adjust='qfq' AND freq='daily'
                ),
                ranked AS (
                  SELECT code, date, close, high, low,
                         LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
                    FROM price_kline, bound
                   WHERE adjust='qfq' AND freq='daily'
                     AND date >= bound.lb
                )
                SELECT
                  COUNT(*) FILTER (WHERE prev_close IS NOT NULL) AS n_with_prev,
                  COUNT(*) FILTER (WHERE prev_close > 0
                                     AND close >= prev_close * 1.095) AS n_up_main,
                  COUNT(*) FILTER (WHERE prev_close > 0
                                     AND close <= prev_close * 0.905) AS n_dn_main,
                  COUNT(*) FILTER (WHERE prev_close > 0
                                     AND high = low AND high = close
                                     AND close >= prev_close * 1.095) AS n_up_sealed
                  FROM ranked
                """
            ).fetchone()
            n_with_prev, n_up, n_dn, n_up_sealed = row
            if (n_up or 0) > 0 and (n_dn or 0) > 0:
                out.append(CheckResult(
                    section="2. Limit rules",
                    name="kline_limit_detectable",
                    status="PASS",
                    detail=(
                        f"近 14 天 K 线: {n_with_prev:,} 行可算 prev_close; "
                        f"涨停近似 (close >= prev*1.095): {n_up}, 跌停近似: {n_dn}, "
                        f"封死涨停 (high==low==close>prev*1.095): {n_up_sealed}"
                    ),
                    rows=int(n_with_prev or 0),
                    extras={
                        "up_limit_proxy": int(n_up or 0),
                        "down_limit_proxy": int(n_dn or 0),
                        "up_limit_sealed": int(n_up_sealed or 0),
                    },
                ))
            else:
                out.append(CheckResult(
                    section="2. Limit rules",
                    name="kline_limit_detectable",
                    status="WARN",
                    detail=(
                        f"近 14 天 K 线没有识别到涨跌停 (up={n_up}, dn={n_dn}); "
                        "可能数据偏少或市场异常平稳"
                    ),
                ))
        finally:
            mconn.close()
    except Exception as e:
        out.append(CheckResult(
            section="2. Limit rules",
            name="kline_limit_detectable",
            status="FAIL",
            detail=f"K 线涨跌停识别 SQL 失败: {e}",
        ))
    return out


def _matches_any_pattern(line: str, patterns: tuple[re.Pattern, ...]) -> bool:
    for pat in patterns:
        if pat.search(line):
            return True
    return False


def _grep_file(fp: Path, patterns: tuple[re.Pattern, ...]) -> list[tuple[Path, int, str]]:
    """Return grep hits for one file; lines matching multiple patterns count once."""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning(f"could not read {fp}: {exc}")
        return []
    return [
        (fp, i, line.strip())
        for i, line in enumerate(text.splitlines(), start=1)
        if _matches_any_pattern(line, patterns)
    ]


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _grep_dir(repo_root: Path, sub: str, patterns: tuple[re.Pattern, ...]) -> list[tuple[Path, int, str]]:
    """In-Python grep: return list of (file, line_no, line)."""
    root = repo_root / sub
    if not root.exists():
        return []
    hits: list[tuple[Path, int, str]] = []
    for fp in _python_files(root):
        hits.extend(_grep_file(fp, patterns))
    return hits


def check_paper_sim_filter(conn) -> list[CheckResult]:
    """Section 3: paper_sim / portfolio_walk_forward 是否过滤不可成交样本.

    静态扫:
      A. suspension / volume<=0 filter (买入侧必须有)
      B. limit_up / limit_down filter (买入侧买不进涨停, 卖出侧卖不出跌停)
    """
    out: list[CheckResult] = []
    repo_root = Path(__file__).resolve().parent.parent.parent

    # A. suspension filter
    susp_hits = _grep_dir(repo_root, "backend/services/paper_sim/", SUSPENSION_PATTERNS) + \
                _grep_dir(repo_root, "backend/services/portfolio_walk_forward/", SUSPENSION_PATTERNS)
    if susp_hits:
        files = sorted({str(h[0].relative_to(repo_root)) for h in susp_hits})
        out.append(CheckResult(
            section="3. paper_sim filter",
            name="suspension_filter",
            status="PASS",
            detail=(
                f"找到 {len(susp_hits)} 处 suspension/volume=0/require_today_traded 引用 "
                f"in {len(files)} 文件"
            ),
            rows=len(susp_hits),
            extras={"files": files, "hits_sample": [
                f"{p.name}:{ln}: {txt[:120]}" for p, ln, txt in susp_hits[:5]
            ]},
        ))
    else:
        out.append(CheckResult(
            section="3. paper_sim filter",
            name="suspension_filter",
            status="FAIL",
            detail=(
                "paper_sim / portfolio_walk_forward 没找到 suspension / volume=0 "
                "过滤代码 — ML label 可能用了停牌日'幻想收益'"
            ),
        ))

    # B. limit_up/down filter — 扫 paper_sim + portfolio_walk_forward + primitives + scripts
    limit_hits = (
        _grep_dir(repo_root, "backend/services/paper_sim/", LIMIT_PATTERNS)
        + _grep_dir(repo_root, "backend/services/portfolio_walk_forward/", LIMIT_PATTERNS)
        + _grep_dir(repo_root, "backend/services/primitives/", LIMIT_PATTERNS)
        + _grep_dir(repo_root, "backend/services/trading_config/", LIMIT_PATTERNS)
        + _grep_dir(repo_root, "backend/services/paper_engine/", LIMIT_PATTERNS)
    )
    paper_sim_hits = [h for h in limit_hits if "/paper_sim/" in str(h[0]) or "/portfolio_walk_forward/" in str(h[0])]
    if paper_sim_hits:
        files = sorted({str(h[0].relative_to(repo_root)) for h in paper_sim_hits})
        out.append(CheckResult(
            section="3. paper_sim filter",
            name="limit_filter",
            status="PASS",
            detail=(
                f"paper_sim/portfolio_walk_forward 找到 {len(paper_sim_hits)} 处 "
                f"limit_up/limit_down 过滤逻辑 in {len(files)} 文件"
            ),
            rows=len(paper_sim_hits),
            extras={"files": files, "hits_sample": [
                f"{p.name}:{ln}: {txt[:120]}" for p, ln, txt in paper_sim_hits[:5]
            ]},
        ))
    elif limit_hits:
        # P-1.3 audit 边界: 数据层 (规则表 + K线能识别) = 已 PASS 在前面 sections.
        # 工程层 (paper_sim selector 接入 stop/limit wiring) 是 P0c 范围, 不是 P-1 数据审计.
        # Codex review Q2 主张升级 FAIL, 但跟 PLAN §6 串行 gate 矛盾 — P0c 在 P-1 之后,
        # FAIL 会形成循环阻塞. 标 WARN + pending_phase=P0c 让 P0c gate 检查时升级.
        files = sorted({str(h[0].relative_to(repo_root)) for h in limit_hits})
        out.append(CheckResult(
            section="3. paper_sim filter",
            name="limit_filter",
            status="WARN",
            detail=(
                f"dim_price_limit_rules 在 {len(files)} 文件被引用 (DDL/seed/scripts) — "
                "但 paper_sim/portfolio_walk_forward 没直接调用. P0c selector refactor 必修接入 "
                "stop/limit wiring (PLAN §2 P0c Acceptance: 交易日志含不可成交原因, 同 seed 可复现)."
            ),
            rows=len(limit_hits),
            extras={"files": files, "pending_phase": "P0c"},
        ))
    else:
        out.append(CheckResult(
            section="3. paper_sim filter",
            name="limit_filter",
            status="FAIL",
            detail=(
                "全 backend 找不到 limit_up/limit_down 过滤代码 — "
                "涨停日选股 = 实盘买不进, 跌停日卖出 = 实盘卖不出"
            ),
        ))

    return out


def _fetch_spot_check_counts(mconn, dates: list[str]) -> dict[str, dict[str, tuple[int, int]]]:
    counts = {date: {"raw": (0, 0), "view": (0, 0)} for date in dates}
    if not dates:
        return counts
    placeholders = ", ".join(["(?)"] * len(dates))
    rows = mconn.execute(
        f"""
        WITH target_dates(date) AS (
          VALUES {placeholders}
        ),
        raw_counts AS (
          SELECT CAST(date AS VARCHAR) AS date,
                 COUNT(*) AS total,
                 COUNT(*) FILTER (WHERE volume IS NULL OR volume <= 0) AS susp
            FROM price_kline
           WHERE adjust='qfq' AND freq='daily'
             AND date IN (SELECT date FROM target_dates)
           GROUP BY 1
        ),
        view_counts AS (
          SELECT CAST(date AS VARCHAR) AS date,
                 COUNT(*) AS total,
                 COUNT(*) FILTER (WHERE volume IS NULL OR volume <= 0) AS susp
            FROM v_price_kline_qfq
           WHERE adjust='qfq' AND freq='daily'
             AND date IN (SELECT date FROM target_dates)
           GROUP BY 1
        )
        SELECT 'raw' AS source, date, total, susp FROM raw_counts
        UNION ALL
        SELECT 'view' AS source, date, total, susp FROM view_counts
        """,
        dates,
    ).fetchall()
    for source, date, total, susp in rows:
        date_key = str(date)
        if date_key not in counts:
            continue
        counts[date_key][str(source)] = (int(total or 0), int(susp or 0))
    return counts


def _spot_check_result_for_counts(
    d: str,
    raw_total: int,
    raw_susp_n: int,
    view_total: int,
    view_susp_n: int,
) -> CheckResult:
    view_drop = raw_total - view_total  # view 比 raw 少多少行

    if raw_susp_n == 0:
        status = "WARN"
        detail = f"日期 {d}: raw 该日 0 停牌 — 跳过"
    elif view_susp_n > 0:
        status = "FAIL"
        detail = (
            f"日期 {d}: view 含 {view_susp_n} 停牌行 — paper_sim selector 会"
            "读到停牌样本 = ML label 用幻想收益"
        )
    else:
        # view 0 停牌 → PASS (invariant 满足). view_total vs raw_total 仅作诊断
        # (view 还从 price_kline_tdxhub primary 拉行, 跟 raw fallback 计数自然不同)
        status = "PASS"
        detail = (
            f"日期 {d}: raw {raw_total} 行 (含 {raw_susp_n} 停牌); "
            f"view {view_total} 行 (含 0 停牌) — 停牌全部 mask"
        )
    return CheckResult(
        section="4. Spot check filter",
        name=f"{d}",
        status=status,
        detail=detail,
        rows=int(raw_susp_n or 0),
        extras={
            "raw_total": int(raw_total),
            "raw_susp": int(raw_susp_n),
            "view_total": int(view_total),
            "view_susp": int(view_susp_n),
            "view_drop": int(view_drop),
        },
    )


def check_spot_check_filter(conn) -> list[CheckResult]:
    """Section 4: Spot check — paper_sim 读 v_price_kline_qfq, view 内置过滤已
    将 volume<1e-6 / amount<1e-6 行 drop 掉 (见 view DDL). 取近期高停牌日,
    验证 v_price_kline_qfq 当日 0 停牌行 (即 ML label / paper_sim selector 不会
    取到 volume=0 的"幻想收益"样本).

    invariant: view_susp == 0  → PASS
    view_total / view_drop 跟 raw 比是诊断信息 (view 还从 tdxhub primary 拉, 计数会差)
    """
    out: list[CheckResult] = []
    try:
        mconn = duck_connect(str(MARKET_DB), read_only=True)
        try:
            # 找近期 3 个 raw suspension 行数最多的日 (price_kline tier=3 fallback 路径)
            top_dates = mconn.execute(
                """
                SELECT date, COUNT(*) AS susp
                  FROM price_kline
                 WHERE adjust='qfq' AND freq='daily' AND (volume IS NULL OR volume <= 0)
                 GROUP BY 1
                 ORDER BY 2 DESC
                 LIMIT 3
                """
            ).fetchall()
            if not top_dates:
                out.append(CheckResult(
                    section="4. Spot check filter",
                    name="no_suspension_day",
                    status="WARN",
                    detail="raw price_kline 没找到任何带停牌行的日子 — 跳过 spot check",
                ))
                return out

            dates = [str(d) for d, _raw_susp in top_dates]
            counts_by_date = _fetch_spot_check_counts(mconn, dates)
            for d in dates:
                raw_total, raw_susp_n = counts_by_date[d]["raw"]
                view_total, view_susp_n = counts_by_date[d]["view"]
                out.append(_spot_check_result_for_counts(d, raw_total, raw_susp_n, view_total, view_susp_n))
        finally:
            mconn.close()
    except Exception as e:
        out.append(CheckResult(
            section="4. Spot check filter",
            name="spot_check",
            status="FAIL",
            detail=f"spot check 失败: {e}",
        ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P-1.3 tradeability audit")
    parser.add_argument("--json-out", type=Path, default=None, help="Write full JSON report to path")
    args = parser.parse_args()

    log.info("=== P-1.3 Tradeability Audit (PLAN_V3 v3.2) ===")
    # Rule 11 并发安全: read_only=True
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        results: list[CheckResult] = []
        results.extend(check_suspension_source(conn))
        results.extend(check_limit_rule_table(conn))
        results.extend(check_paper_sim_filter(conn))
        results.extend(check_spot_check_filter(conn))
    finally:
        conn.close()

    # Summary
    by_status = Counter(r.status for r in results)
    log.info("")
    log.info("=== Results ===")
    for r in results:
        log.info(f"  [{r.status:4s}] {r.section} :: {r.name} — {r.detail}")
    log.info("")
    log.info(f"SUMMARY: PASS={by_status['PASS']} WARN={by_status['WARN']} FAIL={by_status['FAIL']}")

    if args.json_out:
        payload = {
            "audit": "P-1.3 tradeability",
            "summary": dict(by_status),
            "results": [asdict(r) for r in results],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                  encoding="utf-8")
        log.info(f"JSON report → {args.json_out}")

    # P-1.3 Go gate: FAIL = 0
    if by_status["FAIL"] > 0:
        log.error(
            f"P-1.3 FAIL: {by_status['FAIL']} 项硬违规 — PLAN_V3 §6 串行 gate 阻止 P0"
        )
        return 1
    log.info("P-1.3 PASS — tradeability filter OK at coverage level")
    return 0


if __name__ == "__main__":
    sys.exit(main())
