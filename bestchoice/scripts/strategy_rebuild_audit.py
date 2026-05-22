from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute import ComputeEngine, _cache_fresh, _load_cache, get_data_freshness, get_strategy_profiles
from execution_model import EXECUTION_MODEL_VERSION


ANALYSIS_DIR = ROOT / "analysis"
FORMULA_SUMMARY = ANALYSIS_DIR / "formula_parameter_search_summary.csv"
FORMULA_STOCK_BEST = ANALYSIS_DIR / "formula_stock_best_params.csv"
FORMULA_VARIANT_METRICS = ANALYSIS_DIR / "formula_variant_metrics.csv"
STOCK_FORMULA_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
EXECUTION_AUDIT = ANALYSIS_DIR / "execution_model_audit.csv"
SELL_RULE_AUDIT = ANALYSIS_DIR / "formula_sell_rule_audit.csv"
REPORT = ANALYSIS_DIR / "strategy_rebuild_report.md"
KEY_SAMPLE_CODES = ["301511", "301658", "688700", "002718"]
RECOMMENDATION_GUARD_CODES = ["000571", "002501", "002691", "600273"]


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def _fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def _load_ready_profiles() -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    profiles = get_strategy_profiles()
    ready: dict[str, dict[str, dict]] = {}
    for pid, profile in profiles.items():
        if profile.get("signal_source") != "formula":
            continue
        try:
            if _cache_fresh(pid, profile):
                ready[pid] = _load_cache(pid, include_trade_series=False)
        except Exception:
            pass
    return profiles, ready


def _load_best_sell_rules() -> dict[str, dict[str, object]]:
    if not SELL_RULE_AUDIT.exists():
        return {}
    out: dict[str, dict[str, object]] = {}
    with SELL_RULE_AUDIT.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            formula_id = row.get("formula_id")
            if not formula_id:
                continue
            try:
                score = float(row.get("score") or 0)
            except Exception:
                score = 0.0
            if formula_id not in out or score > float(out[formula_id].get("score") or 0):
                out[formula_id] = {
                    "sell_rule": row.get("sell_rule"),
                    "score": score,
                    "sell_rule_score": score,
                    "sell_rule_win_rate": row.get("win_rate"),
                    "sell_rule_avg_ret": row.get("avg_ret"),
                    "sell_rule_avg_dd": row.get("avg_dd"),
                    "sell_rule_trade_count": row.get("trade_count"),
                }
    return out


def _load_stock_formula_best() -> dict[tuple[str, str], dict[str, object]]:
    if not STOCK_FORMULA_BEST.exists():
        return {}
    out: dict[tuple[str, str], dict[str, object]] = {}
    with STOCK_FORMULA_BEST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = row.get("stock_code")
            formula_id = row.get("formula_id")
            if not code or not formula_id:
                continue
            out[(code, formula_id)] = row
    return out


def write_formula_summary(profiles: dict[str, dict], ready: dict[str, dict[str, dict]]) -> None:
    rows = []
    formula_profiles = [p for p in profiles.values() if p.get("signal_source") == "formula"]
    for p in formula_profiles:
        pid = p["id"]
        data = ready.get(pid) or {}
        metric_rows = [r for r in data.values() if int(r.get("signal_count") or 0) > 0]
        rows.append(
            {
                "strategy_id": pid,
                "formula_id": p.get("formula_id"),
                "display_name": p.get("name"),
                "cache_ready": bool(data),
                "stock_count": len(data),
                "stocks_with_signal": len(metric_rows),
                "avg_signal_count": _avg([float(r.get("signal_count") or 0) for r in metric_rows]),
                "avg_win_rate": _avg([float(r["win_rate"]) for r in metric_rows if r.get("win_rate") is not None]),
                "avg_ret": _avg([float(r["avg_ret"]) for r in metric_rows if r.get("avg_ret") is not None]),
                "avg_dd": _avg([float(r["avg_dd"]) for r in metric_rows if r.get("avg_dd") is not None]),
                "avg_calmar": _avg([float(r["calmar"]) for r in metric_rows if r.get("calmar") is not None]),
                "avg_untradable_rate": _avg([
                    float((r.get("execution") or {}).get("untradable_rate"))
                    for r in metric_rows
                    if (r.get("execution") or {}).get("untradable_rate") is not None
                ]),
                "execution_model": EXECUTION_MODEL_VERSION,
                "params": json.dumps(p.get("formula_params") or {}, ensure_ascii=False, sort_keys=True),
            }
        )

    with FORMULA_SUMMARY.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _fmt(v) for k, v in r.items()})


def write_stock_best(profiles: dict[str, dict], ready: dict[str, dict[str, dict]]) -> None:
    rows = []
    stock_best = _load_stock_formula_best()
    for pid, data in ready.items():
        p = profiles[pid]
        formula_id = str(p.get("formula_id") or "")
        for code, r in data.items():
            if int(r.get("signal_count") or 0) <= 0:
                continue
            opt = stock_best.get((code, formula_id), {})
            best_holding_days = r.get("best_holding_days")
            sell_rule = opt.get("best_sell_rule") or opt.get("sell_rule") or (
                f"fixed_{int(best_holding_days)}" if best_holding_days is not None else None
            )
            rows.append(
                {
                    "stock_code": code,
                    "strategy_id": pid,
                    "formula_id": formula_id,
                    "display_name": p.get("name"),
                    "best_holding_days": best_holding_days,
                    "signal_count": r.get("signal_count"),
                    "win_rate": r.get("win_rate"),
                    "avg_ret": r.get("avg_ret"),
                    "avg_dd": r.get("avg_dd"),
                    "calmar": r.get("calmar"),
                    "effectiveness_score": (r.get("effectiveness") or {}).get("score"),
                    "effectiveness_label": (r.get("effectiveness") or {}).get("label"),
                    "untradable_rate": (r.get("execution") or {}).get("untradable_rate"),
                    "completion_rate": (r.get("execution") or {}).get("completion_rate"),
                    "best_sell_rule": sell_rule,
                    "best_sell_rule_score": opt.get("best_sell_rule_score") or opt.get("sell_rule_score") or opt.get("score"),
                    "params": opt.get("params") or json.dumps(p.get("formula_params") or {}, ensure_ascii=False, sort_keys=True),
                }
            )
    rows.sort(key=lambda r: (r["stock_code"], r["strategy_id"]))
    with FORMULA_STOCK_BEST.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "stock_code",
            "strategy_id",
            "formula_id",
            "display_name",
            "best_holding_days",
            "signal_count",
            "win_rate",
            "avg_ret",
            "avg_dd",
            "calmar",
            "effectiveness_score",
            "effectiveness_label",
            "untradable_rate",
            "completion_rate",
            "best_sell_rule",
            "best_sell_rule_score",
            "params",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _fmt(v) for k, v in r.items()})


def write_execution_audit(ready: dict[str, dict[str, dict]]) -> None:
    counters: dict[tuple[str, str], int] = {}
    for pid, data in ready.items():
        for r in data.values():
            execution = r.get("execution") or {}
            for key in (
                "total_signals",
                "completed_trades",
                "skipped_buys",
                "pending_buys",
                "delayed_buys",
                "delayed_sells",
                "untradable_events",
            ):
                val = execution.get(key)
                if val:
                    counters[(pid, key)] = counters.get((pid, key), 0) + int(val)

    with EXECUTION_AUDIT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["strategy_id", "metric", "count"])
        writer.writeheader()
        for (pid, metric), count in sorted(counters.items()):
            writer.writerow({"strategy_id": pid, "metric": metric, "count": count})


def write_report(profiles: dict[str, dict], ready: dict[str, dict[str, dict]]) -> None:
    freshness = get_data_freshness(force=True)
    formula_profiles = [p for p in profiles.values() if p.get("signal_source") == "formula"]
    missing = [p["id"] for p in formula_profiles if p["id"] not in ready]
    variant_metric_rows = _csv_row_count(FORMULA_VARIANT_METRICS)
    stock_formula_best_rows = _csv_row_count(STOCK_FORMULA_BEST)
    parameter_search_ready = variant_metric_rows > 0 and stock_formula_best_rows > 0
    ready_lines = []
    for p in formula_profiles:
        data = ready.get(p["id"]) or {}
        with_signal = sum(1 for r in data.values() if int(r.get("signal_count") or 0) > 0)
        ready_lines.append(f"- `{p['name']}` `{p['id']}`: cache={'yes' if data else 'no'}, stocks_with_signal={with_signal}")
    sample_lines, guard_lines, unified_summary = _unified_sample_lines()

    REPORT.write_text(
        "\n".join(
            [
                "# Strategy Rebuild Audit",
                "",
                f"- data_latest_date: `{freshness.get('latest_data_date')}`",
                f"- global_latest_data_date: `{freshness.get('global_latest_data_date')}`",
                f"- execution_model: `{EXECUTION_MODEL_VERSION}`",
                f"- formula_profiles: `{len(formula_profiles)}`",
                f"- ready_formula_caches: `{len(ready)}`",
                f"- missing_formula_caches: `{', '.join(missing) if missing else 'none'}`",
                f"- parameter_search_ready: `{'yes' if parameter_search_ready else 'no'}`",
                f"- formula_variant_metric_rows: `{variant_metric_rows}`",
                f"- stock_formula_best_rows: `{stock_formula_best_rows}`",
                "",
                "## Formula Cache Status",
                "",
                *ready_lines,
                "",
                "## Unified Pool Summary",
                "",
                unified_summary,
                "",
                "## Key Sample Verification",
                "",
                *sample_lines,
                "",
                "## Recommendation Guard Samples",
                "",
                *guard_lines,
                "",
                "## Generated Artifacts",
                "",
                f"- `{FORMULA_SUMMARY.relative_to(ROOT)}`",
                f"- `{FORMULA_STOCK_BEST.relative_to(ROOT)}`",
                f"- `{FORMULA_VARIANT_METRICS.relative_to(ROOT)}`",
                f"- `{STOCK_FORMULA_BEST.relative_to(ROOT)}`",
                f"- `{EXECUTION_AUDIT.relative_to(ROOT)}`",
                "",
                "## Notes",
                "",
                "- This audit summarizes caches that already exist and are fresh.",
                "- Missing formula caches must be computed before final completion.",
                "- Parameter search is considered ready only when both variant metrics and per-stock best outputs contain rows.",
                "- Key sample lines are generated from `/api/unified`-equivalent engine data, not hand-written observations.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _unified_sample_lines() -> tuple[list[str], list[str], str]:
    try:
        data = ComputeEngine().unified_data()
    except Exception as exc:
        msg = f"- unified sample verification unavailable: `{type(exc).__name__}: {exc}`"
        return [msg], [msg], "- unavailable"
    if not data or not data.get("ready"):
        msg = "- unified sample verification unavailable: unified pool not ready"
        return [msg], [msg], "- unavailable"

    by_code = {str(r.get("code")): r for r in data.get("stocks", [])}
    summary = data.get("summary") or {}
    summary_line = (
        f"- total `{summary.get('total')}`, today_recommended `{summary.get('today_recommended')}`, "
        f"buy_window `{summary.get('buy_window')}`, multi_family `{summary.get('multi_family')}`, "
        f"profiles `{summary.get('profiles')}`"
    )

    sample_lines = []
    for code in KEY_SAMPLE_CODES:
        row = by_code.get(code) or {}
        signals = [
            s
            for s in row.get("strategy_signals", [])
            if s.get("signal_source") == "formula"
        ][:5]
        signal_text = "; ".join(
            f"{s.get('strategy_name')} hp={s.get('best_holding_days')} sell={s.get('optimized_sell_rule')} variant={s.get('optimized_variant_id')}"
            for s in signals
        )
        sample_lines.append(
            f"- `{code}` recommended=`{bool(row.get('is_today_recommended'))}` "
            f"reason=`{row.get('today_recommend_reason')}` signals=`{signal_text or 'none'}`"
        )

    guard_lines = []
    for code in RECOMMENDATION_GUARD_CODES:
        row = by_code.get(code) or {}
        guard_lines.append(
            f"- `{code}` recommended=`{bool(row.get('is_today_recommended'))}` "
            f"reason=`{row.get('today_recommend_reason')}` "
            f"qualified_families=`{row.get('qualified_buy_family_count')}`"
        )
    return sample_lines, guard_lines, summary_line


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        # Subtract header; tolerate empty files.
        return max(0, sum(1 for _ in f) - 1)


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    profiles, ready = _load_ready_profiles()
    formula_profiles = [p for p in profiles.values() if p.get("signal_source") == "formula"]
    write_formula_summary(profiles, ready)
    write_stock_best(profiles, ready)
    write_execution_audit(ready)
    write_report(profiles, ready)
    print(f"strategy_rebuild_audit: ready_formula_caches={len(ready)}")
    if len(ready) < len(formula_profiles):
        missing = [
            p["id"]
            for p in formula_profiles
            if p["id"] not in ready
        ]
        print(
            "strategy_rebuild_audit:failed "
            f"missing_formula_caches={','.join(missing)}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
