"""Phase γ D3 — 全市场单日股票画像生成 (mart_stock_picture_daily fan-out)。

输入 (来自 D1 audit, 全部已存在):
  - dim_active_a_stock         (5,512 股, 股票池)
  - dim_stock_archetype_latest (5,503 行, archetype + pe_ttm + eps_ttm + revenue/profit_yoy)
  - dim_stock_stage_latest     (3,355 行, stage_reason + stage_score_v1 + return_3m + stock_gate)
  - fact_stock_technical_stage (2.18M 行, 算 technical_stage_days)
  - fact_technical_trigger     (750K 行, 算 formula_hits_last_5d)
  - raw_aif10_valuation_quantile (PE 分位 P30/P50/P70)
  - raw_aif10_peer_valuation   (peer_pe_median, eps_ttm)
  - fact_top10_holder_period   (机构持仓, 取最新 report_date)
  - mart_institution_profile   (机构 win_rate_60d)
  - inst_institutions          (240 跟踪机构)
  - market.duckdb v_price_kline_qfq (最新 2 天 close)

输出 (DELETE+INSERT 全量替换当日, 显式事务):
  - fact_stock_fundamental_stage_daily  (~5,500 行)
  - fact_stock_type_daily               (~5,500 行)
  - dim_stock_stage_days                (~5,500 行)
  - mart_stock_picture_daily            (~5,500 行)

用法:
  PYTHONPATH=backend python backend/scripts/build_picture_daily.py [--date 2026-05-12]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np

from services.db import get_conn
from services.market_db import get_market_conn
from services.universe import get_active_universe
from services.picture.ddl import ensure_picture_tables
from services.picture.fundamental_stage import classify_fundamental_stage
from services.picture.institution_signal import aggregate_institution_signal
from services.picture.kline_latest import derive_kline_latest
from services.picture.stage_days import latest_stage_days
from services.picture.stock_type import classify_stock_type
from services.picture.valuation import derive_valuation


log = logging.getLogger("build_picture_daily")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _today_iso() -> str:
    """Phase ψ.5 根因修复: picture snapshot 默认对齐"最近已收盘交易日", 不是 wall-clock today.
    盘中调时 snapshot_date 标昨日, 跟 K 线 / 财务等底料日期一致, 避免"今天的画像用昨天数据"错配.
    """
    from services.db import get_conn
    from services.utils import latest_completed_trade_date
    _c = get_conn()
    try:
        d = latest_completed_trade_date(_c)
    finally:
        _c.close()
    if not d:
        raise RuntimeError(
            "latest_completed_trade_date 返 None — dim_trading_calendar 未 seed"
        )
    return d


def _load_archetypes(conn) -> dict[str, dict]:
    """dim_stock_archetype_latest → {code: {pe_ttm, archetype, profit_yoy, revenue_yoy, ...}}。"""
    rows = conn.execute(
        """
        SELECT stock_code, stock_archetype, pe_ttm,
               latest_revenue_yoy, latest_profit_yoy
          FROM dim_stock_archetype_latest
        """
    ).fetchall()
    out = {}
    for r in rows:
        out[r[0]] = {
            "stock_archetype":     r[1],
            "pe_ttm":              r[2],
            "latest_revenue_yoy":  r[3],
            "latest_profit_yoy":   r[4],
        }
    log.info(f"  archetypes: {len(out):,} 股")
    return out


def _load_stages(conn) -> dict[str, dict]:
    """dim_stock_stage_latest → {code: {stage_reason, stage_score_v1, stock_gate, return_3m}}。"""
    rows = conn.execute(
        """
        SELECT stock_code, stage_reason, stage_score_v1, stock_gate, return_3m
          FROM dim_stock_stage_latest
        """
    ).fetchall()
    out = {}
    for r in rows:
        out[r[0]] = {
            "stage_reason":    r[1],
            "stage_score_v1":  r[2],
            "stock_gate":      r[3],
            "return_3m":       r[4],
        }
    log.info(f"  stage 资料: {len(out):,} 股")
    return out


def _stage_days_from_sorted_rows(rows: list[tuple]) -> dict[str, tuple[str, int]]:
    """通用辅助: 按 (stock_code, date) 升序的 (code, date, stage) 序列 → {code: (latest_stage, days)}。

    Run-length: 累计同 stage 连续天数, 切换时重置。
    """
    out: dict[str, tuple[str, int]] = {}
    cur_code = None
    cur_stage = None
    cur_days = 0
    for code, _date, stage in rows:
        if code != cur_code:
            if cur_code is not None:
                out[cur_code] = (cur_stage, cur_days)
            cur_code, cur_stage, cur_days = code, stage, 1
        else:
            if stage == cur_stage:
                cur_days += 1
            else:
                cur_stage, cur_days = stage, 1
    if cur_code is not None:
        out[cur_code] = (cur_stage, cur_days)
    return out


def _load_technical_stage_days(conn, target_date: str) -> dict[str, tuple[str, int]]:
    """对每只股票算 (latest_technical_stage, technical_stage_days at target_date)。

    数据从 fact_stock_technical_stage 拉, 按 (code, date) 排序后单趟扫描算 run-length。
    """
    t0 = time.time()
    rows = conn.execute(
        """
        SELECT stock_code, date, stage
          FROM fact_stock_technical_stage
         WHERE date <= ?
         ORDER BY stock_code, date
        """,
        [target_date],
    ).fetchall()
    out = _stage_days_from_sorted_rows(rows)
    log.info(f"  technical_stage_days: {len(out):,} 股 ({time.time()-t0:.1f}s)")
    return out


def _load_fundamental_stage_days(conn, target_date: str) -> dict[str, int]:
    """从 fact_stock_fundamental_stage_daily 历史算 days。

    首次跑时表是空的, 返回空 dict, 每股的 days 默认 1。
    """
    try:
        rows = conn.execute(
            """
            SELECT stock_code, date, fundamental_stage
              FROM fact_stock_fundamental_stage_daily
             WHERE date <= ?
             ORDER BY stock_code, date
            """,
            [target_date],
        ).fetchall()
    except Exception:
        return {}
    parsed = _stage_days_from_sorted_rows(rows)
    return {code: days for code, (_, days) in parsed.items()}


def _load_formula_hits(conn, target_date: str, days_back: int = 5) -> dict[str, int]:
    """fact_technical_trigger 最近 days_back 日 hit 数 by stock_code。"""
    # 用 Python 算下限, 避免 DuckDB INTERVAL 方言差异
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(target_date)
    lower = (d - timedelta(days=days_back)).isoformat()
    rows = conn.execute(
        """
        SELECT stock_code, COUNT(*)
          FROM fact_technical_trigger
         WHERE date <= ? AND date >= ?
         GROUP BY stock_code
        """,
        [target_date, lower],
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _load_valuation_quantiles(conn) -> dict[str, dict]:
    """raw_aif10_valuation_quantile (PE only, index_type='1') 每股最新 P30/P50/P70。

    注: 表用 security_code (不是 stock_code), 我们 strip 后 6 位返回。
    """
    rows = conn.execute(
        """
        SELECT security_code, percentile_thirty, percentile_fifty, percentile_seventy
          FROM (
            SELECT security_code, percentile_thirty, percentile_fifty, percentile_seventy,
                   ROW_NUMBER() OVER (PARTITION BY security_code ORDER BY statistics_cycle DESC) AS rn
              FROM raw_aif10_valuation_quantile
             WHERE CAST(index_type AS TEXT) = '1'
          )
         WHERE rn = 1
        """
    ).fetchall()
    return {r[0]: {"p30": r[1], "p50": r[2], "p70": r[3]} for r in rows}


def _load_peer_valuation(conn) -> dict[str, dict]:
    """raw_aif10_peer_valuation 最新 industry_pe_median + stock_pe 等。

    注: 表用 security_code (不是 stock_code)。
    """
    rows = conn.execute(
        """
        SELECT security_code, industry_pe_median, stock_pe
          FROM (
            SELECT security_code, industry_pe_median, stock_pe,
                   ROW_NUMBER() OVER (PARTITION BY security_code ORDER BY report_date DESC) AS rn
              FROM raw_aif10_peer_valuation
             WHERE security_code IS NOT NULL
          )
         WHERE rn = 1
        """
    ).fetchall()
    return {r[0]: {"industry_pe_median": r[1], "stock_pe": r[2]} for r in rows}


def _load_kline_latest(mkt_conn, target_date: str) -> dict[str, tuple[float, float]]:
    """每股最新 close + 前一日 close, 用于 chg_pct。"""
    rows = mkt_conn.execute(
        """
        SELECT code, date, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily' AND date <= ?
         ORDER BY code, date DESC
        """,
        [target_date],
    ).fetchall()
    out: dict[str, tuple[float, float]] = {}
    # 每股取前 2 行 (date DESC, 第一行 = today, 第二行 = prev)
    seen: dict[str, list[tuple[str, float]]] = {}
    for code, dt, cl in rows:
        seen.setdefault(code, [])
        if len(seen[code]) < 2:
            seen[code].append((dt, cl))
    for code, lst in seen.items():
        today_close = lst[0][1] if len(lst) >= 1 else None
        prev_close = lst[1][1] if len(lst) >= 2 else None
        out[code] = (today_close, prev_close)
    return out


def _load_tracked_inst_names(conn) -> set[str]:
    """inst_institutions.name + display_name → set 用于 holder_name_norm 匹配。"""
    rows = conn.execute(
        "SELECT name, display_name FROM inst_institutions WHERE COALESCE(enabled, 1) = 1"
    ).fetchall()
    out = set()
    for r in rows:
        if r[0]:
            out.add(r[0])
        if r[1]:
            out.add(r[1])
    return out


def _load_inst_win_rates(conn) -> dict[str, float]:
    """mart_institution_profile → {institution_name: win_rate_60d}."""
    rows = conn.execute(
        "SELECT institution_name, win_rate_60d FROM mart_institution_profile"
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[0] and r[1] is not None}


def _load_holders(conn) -> dict[str, list[dict]]:
    """fact_top10_holder_period 每股最新 report_date 的 holders list。

    每股 report_date 不一致 (季报发布日期不同), 用 ROW_NUMBER 找每股最新一期。
    """
    rows = conn.execute(
        """
        WITH latest_per_stock AS (
            SELECT stock_code, MAX(report_date) AS latest_rpt
              FROM fact_top10_holder_period
             GROUP BY stock_code
        )
        SELECT h.stock_code, h.holder_name, h.holder_name_norm,
               h.hold_ratio_total, h.hold_change_num
          FROM fact_top10_holder_period h
          JOIN latest_per_stock lps
            ON h.stock_code = lps.stock_code AND h.report_date = lps.latest_rpt
         WHERE COALESCE(h.is_exit_row, FALSE) = FALSE
        """
    ).fetchall()
    by_stock: dict[str, list[dict]] = {}
    for r in rows:
        by_stock.setdefault(r[0], []).append({
            "name": r[1],
            "holder_name_norm": r[2],
            "share_pct": r[3],
            "share_change_qoq": r[4],
        })
    return by_stock


def _enrich_holders(
    holders: list[dict],
    tracked_names: set[str],
    win_rates: dict[str, float],
) -> list[dict]:
    """给 holders 加 is_tracked + inst_win_rate_60d 字段。"""
    out = []
    for h in holders:
        nm = h.get("holder_name_norm") or h.get("name") or ""
        is_tracked = nm in tracked_names
        wr = win_rates.get(nm)
        out.append({
            **h,
            "institution_name": nm,
            "is_tracked": is_tracked,
            "inst_win_rate_60d": wr,
        })
    return out


def _write_atomic(
    conn,
    target_date: str,
    fund_stage_rows: list[tuple],
    type_rows: list[tuple],
    days_rows: list[tuple],
    picture_rows: list[tuple],
) -> None:
    """4 张表全在一个事务里 DELETE+INSERT, 任何中断自动 ROLLBACK。"""
    conn.execute("BEGIN TRANSACTION")
    try:
        # 1. fact_stock_fundamental_stage_daily
        conn.execute("DELETE FROM fact_stock_fundamental_stage_daily WHERE date = ?", [target_date])
        conn.executemany(
            """
            INSERT INTO fact_stock_fundamental_stage_daily
              (stock_code, date, fundamental_stage, stage_score_v1, stage_reason, stock_gate)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            fund_stage_rows,
        )
        # 2. fact_stock_type_daily
        conn.execute("DELETE FROM fact_stock_type_daily WHERE date = ?", [target_date])
        conn.executemany(
            """
            INSERT INTO fact_stock_type_daily
              (stock_code, date, primary_type, secondary_types_json, type_score, reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            type_rows,
        )
        # 3. dim_stock_stage_days
        conn.execute("DELETE FROM dim_stock_stage_days WHERE snapshot_date = ?", [target_date])
        conn.executemany(
            """
            INSERT INTO dim_stock_stage_days
              (stock_code, snapshot_date, fundamental_stage, fundamental_stage_days,
               technical_stage, technical_stage_days)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            days_rows,
        )
        # 4. mart_stock_picture_daily
        conn.execute("DELETE FROM mart_stock_picture_daily WHERE snapshot_date = ?", [target_date])
        conn.executemany(
            """
            INSERT INTO mart_stock_picture_daily
              (stock_code, snapshot_date, latest_close, chg_pct,
               fundamental_stage, fundamental_stage_days,
               technical_stage, technical_stage_days,
               primary_type, secondary_types_json,
               valuation_pe, valuation_pe_pctile, valuation_upside_pct,
               institution_score, institution_n_insts, institution_top_json,
               formulas_hit_json, stock_archetype)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            picture_rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def build_picture_daily(
    target_date: str | None = None,
    conn=None,
    mkt_conn=None,
) -> dict[str, int]:
    """主 entry。返回 {表名: 行数} dict 用于审计。

    Args:
        target_date: 默认今日
        conn:        测试可注入 in-memory conn (不会自动 close)
        mkt_conn:    测试可注入 in-memory mkt_conn
    """
    if not target_date:
        target_date = _today_iso()
    log.info(f"target_date = {target_date}")

    t_total = time.time()
    owns_conn = conn is None
    owns_mkt  = mkt_conn is None
    if conn is None:
        conn = get_conn()
    if mkt_conn is None:
        mkt_conn = get_market_conn()
    try:
        ensure_picture_tables(conn)

        # 一次性加载所有原料 (~10 秒)
        log.info("加载原料...")
        archetypes  = _load_archetypes(conn)
        stages_meta = _load_stages(conn)
        tech_days   = _load_technical_stage_days(conn, target_date)
        fund_days_history = _load_fundamental_stage_days(conn, target_date)
        formula_hits = _load_formula_hits(conn, target_date)
        val_qts     = _load_valuation_quantiles(conn)
        peer_vals   = _load_peer_valuation(conn)
        kline_latest = _load_kline_latest(mkt_conn, target_date)
        tracked_inst = _load_tracked_inst_names(conn)
        inst_wins   = _load_inst_win_rates(conn)
        holders_by_stock = _load_holders(conn)
        log.info(f"  机构跟踪: {len(tracked_inst):,} 个名称 / 持仓股票 {len(holders_by_stock):,}")

        # 全量股票池 — K 线真相源
        codes = sorted(get_active_universe(conn, include_st=True, market_conn=mkt_conn))
        log.info(f"股票池 {len(codes):,} 股, 开始组装...")

        fund_stage_rows = []
        type_rows = []
        days_rows = []
        picture_rows = []
        t1 = time.time()

        def _f(x):
            """DB 可能返回 Decimal, 统一转 float; None 保留。"""
            if x is None:
                return None
            return float(x)

        for i, code in enumerate(codes):
            arch = archetypes.get(code, {})
            stg = stages_meta.get(code, {})
            tech_stage, tech_stage_days = tech_days.get(code, (None, 0))
            n_formula_hits = formula_hits.get(code, 0)
            today_close, prev_close = kline_latest.get(code, (None, None))
            today_close, prev_close = _f(today_close), _f(prev_close)
            kl = derive_kline_latest(today_close=today_close, prev_close=prev_close)

            # 估值
            vq = val_qts.get(code, {})
            pv = peer_vals.get(code, {})
            pe_ttm = _f(arch.get("pe_ttm"))
            stock_pe = _f(pv.get("stock_pe"))
            peer_med = _f(pv.get("industry_pe_median"))
            eps_ttm = (today_close / stock_pe) if (stock_pe and stock_pe > 0 and today_close) else None
            val = derive_valuation(
                pe_ttm=pe_ttm,
                pe_p30=_f(vq.get("p30")), pe_p50=_f(vq.get("p50")), pe_p70=_f(vq.get("p70")),
                close=today_close,
                peer_pe_median=peer_med,
                eps_ttm=eps_ttm,
            )

            # fundamental_stage
            fund_stage = classify_fundamental_stage(stg.get("stage_reason"))
            # fundamental_stage_days: 历史有则 +1, 否则 1
            prev_days = fund_days_history.get(code, 0)
            fund_stage_days = prev_days + 1 if prev_days > 0 else 1

            # stock_type
            type_features = {
                "event_count_30d": 0,  # TODO: 后续 sprint 接 mart_event_*
                "stock_archetype": arch.get("stock_archetype"),
                "fundamental_stage": fund_stage,
                "latest_profit_yoy": arch.get("latest_profit_yoy"),
                "latest_revenue_yoy": arch.get("latest_revenue_yoy"),
                "valuation_pe_pctile": val.get("valuation_pe_pctile"),
                "return_3m": stg.get("return_3m"),
                "vol_ratio": None,  # TODO: 后续 sprint 接 amount_ratio_20_120
                "formula_hits_last_5d": n_formula_hits,
            }
            type_out = classify_stock_type(type_features)

            # institution_signal
            h_raw = holders_by_stock.get(code, [])
            h_enriched = _enrich_holders(h_raw, tracked_inst, inst_wins)
            inst_sig = aggregate_institution_signal(h_enriched)

            # 拼 4 张表的行
            fund_stage_rows.append((
                code, target_date, fund_stage,
                stg.get("stage_score_v1"), stg.get("stage_reason"), stg.get("stock_gate"),
            ))
            type_rows.append((
                code, target_date, type_out["primary_type"],
                json.dumps(type_out["secondary_types"], ensure_ascii=False) if type_out["secondary_types"] else None,
                None,  # type_score 暂留空
                json.dumps(type_out["reason_codes"], ensure_ascii=False) if type_out["reason_codes"] else None,
            ))
            days_rows.append((
                code, target_date, fund_stage, fund_stage_days,
                tech_stage, tech_stage_days,
            ))
            picture_rows.append((
                code, target_date,
                kl["latest_close"], kl["chg_pct"],
                fund_stage, fund_stage_days,
                tech_stage, tech_stage_days,
                type_out["primary_type"],
                json.dumps(type_out["secondary_types"], ensure_ascii=False) if type_out["secondary_types"] else None,
                val["valuation_pe"], val["valuation_pe_pctile"], val["valuation_upside_pct"],
                inst_sig["institution_score"], inst_sig["institution_n_insts"],
                json.dumps(inst_sig["institution_top"], ensure_ascii=False) if inst_sig["institution_top"] else None,
                None,  # formulas_hit_json 暂留空 (D4 接入)
                arch.get("stock_archetype"),
            ))

            if (i + 1) % 1000 == 0:
                log.info(f"  组装 {i+1:,}/{len(codes):,} ({(i+1)/(time.time()-t1):.0f} 股/s)")

        log.info(f"组装完成 ({time.time()-t1:.1f}s); 写库 (4 表事务原子)...")
        t2 = time.time()
        _write_atomic(conn, target_date, fund_stage_rows, type_rows, days_rows, picture_rows)
        log.info(f"写库完成 ({time.time()-t2:.1f}s)")

        log.info(f"完成: {len(codes):,} 股 × 4 表 = {len(codes)*4:,} 行 (总耗时 {time.time()-t_total:.1f}s)")
        return {
            "fact_stock_fundamental_stage_daily": len(fund_stage_rows),
            "fact_stock_type_daily": len(type_rows),
            "dim_stock_stage_days": len(days_rows),
            "mart_stock_picture_daily": len(picture_rows),
        }
    finally:
        if owns_conn:
            conn.close()
        if owns_mkt:
            mkt_conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="目标 snapshot_date, 默认今日")
    args = parser.parse_args()
    build_picture_daily(args.date)


if __name__ == "__main__":
    main()
