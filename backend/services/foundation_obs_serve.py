"""Foundation observation projection — file-based, no DuckDB.

Matrix / health for the workbench must stay readable while daily_update holds
the writer lock. Source of truth = newest watermark SLA JSON + /tmp alert flags
+ latest daily_*.json. Display labels are static; numbers come from those files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
AUDIT = REPO / "data" / "audit"
REPORTS = REPO / "data" / "reports"
FLAG_DIR = Path("/tmp")

_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("日历与身份", ("trade_cal", "stock_basic", "stock_st", "suspend_d", "stk_limit")),
    ("行情与量价", ("daily", "kline_daily", "daily_basic", "adj_factor", "cyq_perf", "block_trade", "daily_info")),
    ("资金与情绪", (
        "moneyflow", "moneyflow_dc", "moneyflow_mkt_dc", "moneyflow_hsgt", "moneyflow_ind_dc",
        "margin", "margin_detail", "limit_list_d", "limit_cpt_list", "kpl_list", "hm_detail",
        "hm_list", "ths_hot", "top_list", "top_inst",
    )),
    ("指数与分类", (
        "index_daily", "index_daily_benchmark", "index_dailybasic", "sw_daily",
        "dc_daily", "dc_index", "dc_member", "index_member_all", "index_member_all_hist",
        "industry_dc",
    )),
    ("财报与披露", (
        "stk_holdernumber", "stk_holdertrade", "income", "balancesheet", "fina_indicator",
        "forecast", "dividend", "share_float", "report_rc", "org_holding", "holders_top10",
        "holders_top10_float", "stk_surv",
    )),
)

_CN = {
    "trade_cal": "交易日历",
    "stock_basic": "证券身份",
    "stock_st": "ST 成员",
    "suspend_d": "停牌",
    "stk_limit": "涨跌停价",
    "daily": "名义日K",
    "kline_daily": "名义日K",
    "daily_basic": "每日指标",
    "adj_factor": "复权因子",
    "cyq_perf": "筹码分布",
    "block_trade": "大宗交易",
    "daily_info": "每日概况",
    "moneyflow": "资金流向",
    "moneyflow_dc": "东财资金流",
    "moneyflow_mkt_dc": "东财大盘资金",
    "moneyflow_hsgt": "沪深港通",
    "moneyflow_ind_dc": "东财行业资金",
    "margin": "两融汇总",
    "margin_detail": "两融明细",
    "limit_list_d": "涨跌停榜",
    "limit_cpt_list": "连板榜",
    "kpl_list": "开盘啦",
    "hm_detail": "游资明细",
    "hm_list": "游资名录",
    "ths_hot": "同花顺热榜",
    "top_list": "龙虎榜",
    "top_inst": "龙虎榜席位",
    "index_daily": "指数日线",
    "index_daily_benchmark": "基准指数",
    "index_dailybasic": "指数指标",
    "sw_daily": "申万日线",
    "dc_daily": "东财板块",
    "dc_index": "东财指数",
    "dc_member": "东财成员",
    "index_member_all": "指数成员",
    "industry_dc": "东财行业",
    "stk_holdernumber": "股东人数",
    "stk_holdertrade": "股东增减持",
    "income": "利润表",
    "balancesheet": "资产负债表",
    "fina_indicator": "财务指标",
    "forecast": "业绩预告",
    "dividend": "分红",
    "share_float": "限售解禁",
    "report_rc": "研报",
    "org_holding": "机构持仓",
    "holders_top10": "十大流通股东",
    "holders_top10_float": "十大流通股东",
    "stk_surv": "董监高持股",
}


def _newest_sla(*, repo: Path = REPO) -> tuple[Path | None, dict[str, Any]]:
    audit = repo / "data" / "audit"
    candidates: list[Path] = []
    if audit.is_dir():
        candidates.extend(audit.glob("watermark_sla_before_*.json"))
        for path in audit.glob("watermark_sla_*.json"):
            name = path.name
            if name.startswith("watermark_sla_before_"):
                continue
            if name == "watermark_sla_latest.json":
                continue
            if re.fullmatch(r"watermark_sla_\d{8}\.json", name):
                candidates.append(path)
        latest = audit / "watermark_sla_latest.json"
        if latest.exists():
            candidates.append(latest)
    if not candidates:
        return None, {}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return path, {}
    return path, payload if isinstance(payload, dict) else {}


def _latest_report(*, repo: Path = REPO) -> dict[str, Any] | None:
    reports_dir = repo / "data" / "reports"
    if not reports_dir.is_dir():
        return None
    candidates = sorted(
        reports_dir.glob("daily_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[:5]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("run_outcome"):
            data = dict(data)
            data["_report_path"] = _rel(path, repo)
            return data
    return None


def _alert_flags(*, flag_dir: Path = FLAG_DIR) -> dict[str, bool]:
    out: dict[str, bool] = {}
    if not flag_dir.is_dir():
        return out
    for path in flag_dir.glob("chunkymonkey_ALERT_*.flag"):
        name = path.name.removeprefix("chunkymonkey_ALERT_").removesuffix(".flag")
        out[name] = True
    return out


def _lamp(status: str | None, alert: bool) -> str:
    if alert:
        return "hole"
    text = str(status or "").upper()
    if text in {"OK", "PASS"}:
        return "ok"
    if "STALE" in text or "FAIL" in text or "ALERT" in text:
        return "hole"
    if text in {"NO_PROBE_RULE", "UNVERIFIED", ""}:
        return "unk"
    return "soft"


_GROUPS_ORDER = {label: idx for idx, (label, _) in enumerate(_GROUPS)}
_GROUPS_ORDER["其他"] = 50


def _group_of(domain: str) -> str:
    for label, members in _GROUPS:
        if domain in members:
            return label
    return "其他"


def _short_domain(raw: str) -> str:
    name = str(raw or "")
    if name.startswith("sync:"):
        return name.removeprefix("sync:")
    return name


def _rel(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def matrix_payload(*, repo: Path | None = None, flag_dir: Path | None = None) -> dict[str, Any]:
    repo = REPO if repo is None else repo
    flag_dir = FLAG_DIR if flag_dir is None else flag_dir
    path, payload = _newest_sla(repo=repo)
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    rows: list[dict[str, Any]] = []
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        domain = _short_domain(str(entry.get("data_domain") or ""))
        if not domain:
            continue
        status = str(entry.get("status") or "")
        alert = bool(entry.get("alert"))
        rows.append(
            {
                "domain": domain,
                "cn": _CN.get(domain, domain),
                "group": _group_of(domain),
                "watermark": entry.get("watermark_date"),
                "days_ago": entry.get("watermark_days_ago"),
                "sla_days": entry.get("sla_days"),
                "status": status,
                "alert": alert,
                "lamp": _lamp(status, alert),
                "mode": entry.get("sla_axis") or "—",
                "note": entry.get("probe_error") or entry.get("probe_state") or "",
            }
        )
    rows.sort(key=lambda item: (_GROUPS_ORDER.get(item["group"], 99), str(item["domain"])))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["group"], []).append(row)
    flags = _alert_flags(flag_dir=flag_dir)
    rel = None if path is None else _rel(path, repo)
    return {
        "status": "ok" if rows else "empty",
        "as_of": payload.get("run_at") or payload.get("today"),
        "today": payload.get("today"),
        "source": rel,
        "n_alerts": int(payload.get("n_alerts") or 0),
        "n_domains": len(rows),
        "alert_flags": flags,
        "groups": [{"label": key, "n": len(val), "domains": val} for key, val in grouped.items()],
    }


def domain_payload(domain: str, *, repo: Path | None = None, flag_dir: Path | None = None) -> dict[str, Any]:
    matrix = matrix_payload(repo=repo, flag_dir=flag_dir)
    key = _short_domain(domain)
    for group in matrix.get("groups") or []:
        for row in group.get("domains") or []:
            if row.get("domain") == key:
                return {
                    "status": "ok",
                    "as_of": matrix.get("as_of"),
                    "today": matrix.get("today"),
                    "source": matrix.get("source"),
                    "item": row,
                }
    return {
        "status": "empty",
        "as_of": matrix.get("as_of"),
        "today": matrix.get("today"),
        "source": matrix.get("source"),
        "domain": key,
        "item": None,
    }


def health_payload(*, repo: Path | None = None, flag_dir: Path | None = None) -> dict[str, Any]:
    repo = REPO if repo is None else repo
    flag_dir = FLAG_DIR if flag_dir is None else flag_dir
    flags = _alert_flags(flag_dir=flag_dir)
    report = _latest_report(repo=repo)
    classified = []
    if report:
        raw = report.get("run_outcome_classified") or report.get("classified") or []
        if isinstance(raw, list):
            classified = raw[:20]
    checks = [
        {
            "id": "continuity",
            "label": "连续性",
            "lamp": "hole" if flags.get("continuity") else "unk",
            "state": "ALERT" if flags.get("continuity") else "unverified",
        },
        {
            "id": "daily_update",
            "label": "日更告警旗",
            "lamp": "hole" if flags.get("daily_update") or flags.get("daily_update_degraded") else "unk",
            "state": "ALERT" if flags.get("daily_update") or flags.get("daily_update_degraded") else "no_flag",
        },
        {
            "id": "cutover",
            "label": "cutover",
            "lamp": "unk",
            "state": "unverified",
        },
    ]
    if report:
        outcome = str(report.get("run_outcome") or "")
        lamp = {
            "success": "ok",
            "hard_fail": "hard",
            "integrity_observe": "soft",
            "soft_waiting_clock": "soft",
        }.get(outcome, "unk")
        checks.append(
            {
                "id": "last_run",
                "label": "最近一次日更",
                "lamp": lamp,
                "state": outcome or "unknown",
                "reason": report.get("run_outcome_reason"),
                "date": report.get("date"),
            }
        )
    return {
        "status": "ok",
        "alert_flags": flags,
        "checks": checks,
        "classified": classified,
        "report_path": None if not report else report.get("_report_path"),
        "run_outcome": None if not report else report.get("run_outcome"),
        "run_outcome_label": None if not report else report.get("run_outcome_label"),
        "run_outcome_reason": None if not report else report.get("run_outcome_reason"),
        "report_date": None if not report else report.get("date"),
    }
