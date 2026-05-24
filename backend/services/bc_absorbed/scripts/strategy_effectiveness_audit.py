#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import numpy as np

from compute import ema_np, get_strategy_profiles, normalize_code, rolling_max_np, sma_np
from settings import MARKET_DB, SMART_DB


OUT_DIR = ROOT / "analysis"
SUMMARY_CSV = OUT_DIR / "strategy_effectiveness_summary.csv"
TRADES_CSV = OUT_DIR / "strategy_effectiveness_trades.csv"
REPORT_MD = OUT_DIR / "strategy_effectiveness_report.md"

MIN_TOTAL_TRADES = 6
MIN_RECENT_TRADES = 3
MAX_RECENT_TRADES = 5


@dataclass
class Trade:
    profile_id: str
    profile_name: str
    code: str
    name: str
    industry: str
    signal_date: str
    buy_date: str
    sell_date: str
    holding_days: int
    ret: float
    max_dd: float
    amt_r20: float
    price60: float


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        n = float(v)
    except Exception:
        return None
    return n if np.isfinite(n) else None


def load_market_data() -> tuple[dict[str, dict[str, np.ndarray]], dict[str, tuple[str, str]]]:
    con = duckdb.connect(str(MARKET_DB), read_only=True)
    try:
        con.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
        raw = con.execute(
            """
            SELECT k.code, k.date, k.low, k.close, k.volume, k.amount
            FROM v_price_kline_qfq k
            INNER JOIN sm.dim_active_a_stock s ON k.code = s.stock_code
            ORDER BY k.code, k.date
            """
        ).fetchnumpy()
        meta_rows = con.execute(
            """
            SELECT s.stock_code, s.stock_name, COALESCE(a.tdx_l1_name, '未知') AS industry
            FROM sm.dim_active_a_stock s
            LEFT JOIN sm.dim_stock_archetype_latest a ON s.stock_code = a.stock_code
            """
        ).fetchall()
    except duckdb.IOException as e:
        print(f"Warning: SMART_DB locked/unavailable, falling back to all market codes: {e}")
        raw = con.execute(
            """
            SELECT code, date, low, close, volume, amount
            FROM v_price_kline_qfq
            ORDER BY code, date
            """
        ).fetchnumpy()
        meta_rows = []
    finally:
        con.close()

    codes = raw["code"]
    unique_codes, counts = np.unique(codes, return_counts=True)
    by_code: dict[str, dict[str, np.ndarray]] = {}
    idx = 0
    for code_raw, cnt in zip(unique_codes, counts):
        code = normalize_code(code_raw)
        sl = slice(idx, idx + cnt)
        by_code[code] = {
            "date": raw["date"][sl],
            "low": raw["low"][sl].astype(np.float64),
            "close": raw["close"][sl].astype(np.float64),
            "volume": raw["volume"][sl].astype(np.float64),
            "amount": raw["amount"][sl].astype(np.float64),
        }
        idx += cnt

    meta = {normalize_code(code): (str(name), str(industry)) for code, name, industry in meta_rows}
    return by_code, meta


def completed_strategy_trades(
    profile: dict[str, Any],
    by_code: dict[str, dict[str, np.ndarray]],
    meta: dict[str, tuple[str, str]],
) -> list[Trade]:
    fast = int(profile["macd_fast"])
    slow = int(profile["macd_slow"])
    sig = int(profile["macd_signal"])
    holding_days = int(profile["holding_days"])
    vol_min = float(profile.get("vol_ratio_min", 1.0))
    amt_min = float(profile.get("amt_ratio_min", 1.0))
    price_max = float(profile.get("price_pos_max", 1.0))
    require_dif_positive = bool(profile.get("dif_positive", False))
    warmup = slow + sig + max(60, holding_days) + 2

    trades: list[Trade] = []
    for code, rows in by_code.items():
        dates = rows["date"]
        low = rows["low"]
        close = rows["close"]
        volume = rows["volume"]
        amount = rows["amount"]
        n = len(close)
        if n < warmup:
            continue

        dif = ema_np(close, fast) - ema_np(close, slow)
        dea = ema_np(dif, sig)
        vol_ma20 = sma_np(volume, 20)
        amt_ma20 = sma_np(amount, 20)
        max60 = rolling_max_np(close, 60)
        crosses = np.where((dif[:-1] < dea[:-1]) & (dif[1:] > dea[1:]))[0] + 1
        name, industry = meta.get(code, ("", "未知"))

        for si in crosses:
            buy_i = si + 1
            sell_i = buy_i + holding_days
            if sell_i >= n or close[buy_i] <= 0:
                continue
            if (
                vol_ma20[si] <= 0
                or np.isnan(vol_ma20[si])
                or amt_ma20[si] <= 0
                or np.isnan(amt_ma20[si])
                or max60[si] <= 0
            ):
                continue

            vol_r20 = float(volume[si] / vol_ma20[si])
            amt_r20 = float(amount[si] / amt_ma20[si])
            price60 = float(close[si] / max60[si])
            if (
                vol_r20 < vol_min
                or amt_r20 < amt_min
                or price60 > price_max
                or (require_dif_positive and float(dif[si]) <= 0)
            ):
                continue

            buy_price = float(close[buy_i])
            sell_price = float(close[sell_i])
            hold_low = float(np.min(low[buy_i : sell_i + 1]))
            trades.append(
                Trade(
                    profile_id=str(profile["id"]),
                    profile_name=str(profile["name"]),
                    code=code,
                    name=name,
                    industry=industry,
                    signal_date=str(dates[si]),
                    buy_date=str(dates[buy_i]),
                    sell_date=str(dates[sell_i]),
                    holding_days=holding_days,
                    ret=(sell_price - buy_price) / buy_price,
                    max_dd=min(0.0, (hold_low - buy_price) / buy_price),
                    amt_r20=amt_r20,
                    price60=price60,
                )
            )
    return trades


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def slope(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    x = np.arange(len(values), dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    return float(np.polyfit(x, y, 1)[0])


def stats(rows: list[Trade]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    rets = np.asarray([t.ret for t in rows], dtype=np.float64)
    dds = np.asarray([t.max_dd for t in rows], dtype=np.float64)
    avg_ret = float(np.mean(rets))
    avg_dd = float(np.mean(dds))
    return {
        "n": len(rows),
        "avg_ret": avg_ret,
        "median_ret": float(np.median(rets)),
        "win_rate": float(np.mean(rets > 0)),
        "avg_dd": avg_dd,
        "calmar": avg_ret / max(abs(avg_dd), 0.005),
        "best_ret": float(np.max(rets)),
        "worst_ret": float(np.min(rets)),
    }


def trend_row(profile: dict[str, Any], code: str, rows: list[Trade]) -> dict[str, Any] | None:
    rows = sorted(rows, key=lambda t: t.buy_date)
    total_n = len(rows)
    if total_n < MIN_TOTAL_TRADES:
        return None

    recent_n = min(MAX_RECENT_TRADES, max(MIN_RECENT_TRADES, total_n // 3))
    recent = rows[-recent_n:]
    prior = rows[:-recent_n]
    if len(prior) < MIN_RECENT_TRADES or len(recent) < MIN_RECENT_TRADES:
        return None

    all_s = stats(rows)
    prior_s = stats(prior)
    recent_s = stats(recent)
    recent_rets = [t.ret for t in recent]
    recent_dds = [t.max_dd for t in recent]
    prior_avg_ret = float(prior_s["avg_ret"])
    prior_win_rate = float(prior_s["win_rate"])
    prior_avg_dd = float(prior_s["avg_dd"])
    prior_calmar = float(prior_s["calmar"])
    recent_avg_ret = float(recent_s["avg_ret"])
    recent_win_rate = float(recent_s["win_rate"])
    recent_avg_dd = float(recent_s["avg_dd"])
    recent_calmar = float(recent_s["calmar"])

    trimmed_recent = list(recent_rets)
    trimmed_recent.remove(max(trimmed_recent))
    trimmed_recent_avg = float(np.mean(trimmed_recent)) if trimmed_recent else None
    trimmed_recent_win = float(np.mean(np.asarray(trimmed_recent) > 0)) if trimmed_recent else None
    robust = bool(
        trimmed_recent_avg is not None
        and trimmed_recent_avg > prior_avg_ret
        and (trimmed_recent_win or 0.0) >= max(0.5, prior_win_rate - 0.05)
    )

    positive_sum = sum(v for v in recent_rets if v > 0)
    outlier_share = max(recent_rets) / positive_sum if positive_sum > 0 else 1.0
    ret_delta = recent_avg_ret - prior_avg_ret
    win_delta = recent_win_rate - prior_win_rate
    dd_delta = recent_avg_dd - prior_avg_dd
    calmar_delta = recent_calmar - prior_calmar
    ret_slope = slope([t.ret for t in rows])
    dd_slope = slope([t.max_dd for t in rows])

    score = (
        30 * clamp01((ret_delta + 0.02) / 0.10)
        + 20 * clamp01((win_delta + 0.05) / 0.30)
        + 20 * clamp01((dd_delta + 0.02) / 0.08)
        + 20 * clamp01((calmar_delta + 0.30) / 2.30)
        + 10 * clamp01((ret_slope + 0.005) / 0.025)
    )
    if outlier_share > 0.72:
        score -= 12
    if not robust:
        score -= 10
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 70 and robust and ret_delta > 0 and win_delta >= -0.01 and dd_delta >= -0.01:
        label = "增强中"
    elif score >= 58 and ret_delta > 0 and dd_delta >= -0.02:
        label = "改善观察"
    elif ret_delta < -0.02 or dd_delta < -0.03:
        label = "退化中"
    else:
        label = "稳定/不明显"

    last = rows[-1]
    return {
        "profile_id": profile["id"],
        "profile_name": profile["name"],
        "code": code,
        "name": last.name,
        "industry": last.industry,
        "holding_days": last.holding_days,
        "total_n": total_n,
        "recent_n": recent_n,
        "first_buy_date": rows[0].buy_date,
        "last_buy_date": last.buy_date,
        "all_avg_ret": all_s["avg_ret"],
        "all_win_rate": all_s["win_rate"],
        "all_avg_dd": all_s["avg_dd"],
        "all_calmar": all_s["calmar"],
        "prior_avg_ret": prior_avg_ret,
        "prior_win_rate": prior_win_rate,
        "prior_avg_dd": prior_avg_dd,
        "prior_calmar": prior_calmar,
        "recent_avg_ret": recent_avg_ret,
        "recent_win_rate": recent_win_rate,
        "recent_avg_dd": recent_avg_dd,
        "recent_calmar": recent_calmar,
        "ret_delta": ret_delta,
        "win_delta": win_delta,
        "dd_delta": dd_delta,
        "calmar_delta": calmar_delta,
        "ret_slope": ret_slope,
        "dd_slope": dd_slope,
        "trimmed_recent_avg_ret": trimmed_recent_avg,
        "trimmed_recent_win_rate": trimmed_recent_win,
        "outlier_share": outlier_share,
        "robust_after_drop_best": robust,
        "effectiveness_score": score,
        "effectiveness_label": label,
        "recent_rets": json.dumps([round(v, 4) for v in recent_rets], ensure_ascii=False),
        "recent_dds": json.dumps([round(v, 4) for v in recent_dds], ensure_ascii=False),
    }


def auditable_profiles() -> list[dict[str, Any]]:
    profiles = get_strategy_profiles()
    preferred = {"tdx_12_26_9", "optuna_best", "macd_10_22_8_h15", "macd_12_26_9"}
    return [
        profile
        for pid, profile in profiles.items()
        if pid in preferred and not profile.get("formula_filter_mode")
    ]


def fmt_pct(v: Any) -> str:
    n = _to_float(v)
    return "-" if n is None else f"{n * 100:.2f}%"


def write_outputs(rows: list[dict[str, Any]], trades: list[Trade]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    trade_fields = list(Trade.__dataclass_fields__.keys())
    with TRADES_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=trade_fields)
        writer.writeheader()
        for t in sorted(trades, key=lambda x: (x.profile_id, x.code, x.buy_date)):
            writer.writerow(t.__dict__)

    top = sorted(rows, key=lambda r: (r["effectiveness_score"], r["recent_avg_ret"]), reverse=True)
    label_counts: dict[str, int] = {}
    for row in rows:
        label_counts[row["effectiveness_label"]] = label_counts.get(row["effectiveness_label"], 0) + 1

    lines = [
        "# Strategy Effectiveness Audit",
        "",
        "Purpose: find stocks where the same MACD strategy is improving on that stock: recent trade return and win rate improve while drawdown shrinks.",
        "",
        f"Gate: total completed trades >= {MIN_TOTAL_TRADES}; recent window = 3 to {MAX_RECENT_TRADES} latest completed trades; prior window must contain at least {MIN_RECENT_TRADES} trades.",
        "Anti-outlier: drop the best recent trade and require the trimmed recent average to still beat the prior average before labeling a stock as enhanced.",
        "",
        "## Label Counts",
        "",
    ]
    for label, count in sorted(label_counts.items()):
        lines.append(f"- {label}: {count}")

    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| Rank | Strategy | Code | Name | Score | Label | N | Recent Ret | Prior Ret | Recent Win | Prior Win | Recent DD | Prior DD | Robust | Recent Rets |",
            "|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for i, row in enumerate(top[:40], 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    str(row["profile_id"]),
                    str(row["code"]),
                    str(row["name"]),
                    f"{row['effectiveness_score']:.1f}",
                    str(row["effectiveness_label"]),
                    str(row["total_n"]),
                    fmt_pct(row["recent_avg_ret"]),
                    fmt_pct(row["prior_avg_ret"]),
                    fmt_pct(row["recent_win_rate"]),
                    fmt_pct(row["prior_win_rate"]),
                    fmt_pct(row["recent_avg_dd"]),
                    fmt_pct(row["prior_avg_dd"]),
                    "yes" if row["robust_after_drop_best"] else "no",
                    str(row["recent_rets"]),
                ]
            )
            + " |"
        )

    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    print("Loading market data...")
    by_code, meta = load_market_data()
    print(f"Loaded {len(by_code)} stocks")

    summary_rows: list[dict[str, Any]] = []
    all_trades: list[Trade] = []
    for profile in auditable_profiles():
        print(f"Auditing {profile['id']}...")
        trades = completed_strategy_trades(profile, by_code, meta)
        all_trades.extend(trades)
        grouped: dict[str, list[Trade]] = {}
        for t in trades:
            grouped.setdefault(t.code, []).append(t)
        for code, rows in grouped.items():
            row = trend_row(profile, code, rows)
            if row:
                summary_rows.append(row)

    summary_rows.sort(key=lambda r: (r["effectiveness_score"], r["recent_avg_ret"]), reverse=True)
    write_outputs(summary_rows, all_trades)
    print(json.dumps(summary_rows[:20], ensure_ascii=False, indent=2))
    print(f"Wrote {SUMMARY_CSV}")
    print(f"Wrote {TRADES_CSV}")
    print(f"Wrote {REPORT_MD}")


if __name__ == "__main__":
    main()
