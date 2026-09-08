"""北向资金研究页 —— services 层。

只回答两件事 (w3 规格 + b1 核验报告已裁定的边界, 2026-09-08):

  1) 单股 (``stock_series``): 香港中央结算有限公司(陆股通北向持仓在境内登记体系上的代理人,
     holder_code=10671586, 见 ``config/northbound_research.yaml``) 在这只股票的十大流通股东
     名单里, 历史上每个报告期占流通股比例多少、排第几、哪期进/哪期退。PIT 锚是 ``notice_date``
     (实际公告日), 不是 ``report_date`` (报告期末) —— 后者只是描述口径, 不是可见性判据。
  2) 全市场 (``market_breadth``): 每个标准季度末, 十大流通股东名单里含香港中央结算的股票数 /
     当期有十大流通股东披露的股票数, 这个覆盖面按季度如何变化。

这是季度级视图, 不是日频, 更不是资金流 —— 我们看到的是"期末持股比例的期间变化", 不是逐笔
买卖 (feedback-frontend-plain-finance-terms: 措辞不得暗示我们没有的交易行为信息)。

做不了、且本模块不建消费方的三件事 (逐条见 w3_northbound_spec.md §2 / §6, 已经 b1 核验报告
核实过数字):
  - 个股逐日北向资金流: ``hk_hold`` 已停披露(约 2025-07 起), 2025-08-15 起返回 0 行,
    dead-forward, 只有历史, 不能支撑 live 页面, 本模块不读它。
  - 沪深股通十大成交活跃股 (``hsgt_top10``): tushare 有此接口但项目从未注册、从未落库,
    是"没接"不是"没有", 不在本模块授权范围内新增数据域 (红线5)。
  - 北向市场每日净流入 (``moneyflow_hsgt``): 数据本身还在, 在库连续 20141117-20260828,
    但 ``backend/config/tushare_sunset.yaml:158-160`` 当前判定 ``decision: retire``,
    理由是"零消费"。本模块一旦读它, 就会让这条理由自动失效而不经 owner 同意 ——
    这是本模块外的前置阻塞项, 不是本模块能单方绕过的。``market_flow_status()`` 只回一个
    治理状态说明, 本文件不读 ``raw_tushare_moneyflow_hsgt`` 一行。

数据只读 accepted 面 (红线9): 全部经 ``services.data_access.DataAccess.get()`` 读
``canonical_top10_float_holders_period`` (PIT 已由该读层强制 ``notice_date<=as_of``)。
本文件不直连 duckdb、不内联 ``FROM raw_*`` —— 本文件不在 ``data_module_members.yaml``
成员名单里, 没有资格绕开 SERVE 读层 (``check_serve_read_layer.py`` D1 门)。

缺失只能传播为缺失 (红线3): 一个报告期查不到香港中央结算的行, 只代表"当期没有查到"——
可能是真的跌出十大(有其他持有人行证明当期确有披露), 也可能是当期该股完全没有十大流通股东
披露记录(停牌/未上市/未披露)。两种情况在 ``stock_series`` 里用不同的 ``status`` 区分,
不许都当 0 或都当"未持有"处理。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.data_access import DataAccess, get_data_access

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "northbound_research.yaml"

_LEGAL_TOP: frozenset[str] = frozenset(
    {"version", "data_access_entity", "hkscc", "quarter_end_suffixes", "disclosure_settle_days", "tabs"}
)
_LEGAL_HKSCC: frozenset[str] = frozenset(
    {"holder_code", "display_name", "holder_set", "excluded_holder_codes"}
)
_LEGAL_SETTLE: frozenset[str] = frozenset({"quarterly_days", "annual_days"})
_LEGAL_TABS: frozenset[str] = frozenset({"stock_series", "market_breadth", "market_flow"})
_LEGAL_TAB_STATUS: frozenset[str] = frozenset({"enabled", "blocked"})

# GRAIN (schema v4, holders_top10_schema.py) 是 (stock_code, report_date, notice_date,
# holder_set, holder_rank, row_seq, is_exit_row) —— notice_date 是版本轴。data_access.yaml
# 的 holders_top10 entity 目前没有投影 row_seq (只投影本页需要的 12 列), 所以本文件的版本
# 去重只能按 (stock_code, report_date, holder_set, holder_rank, is_exit_row) 取最新 notice_date
# ——比完整 GRAIN 少一维, 只在"同一 (股,期,rank) 有多个不同 holder_name 同时占位"这种项目
# 备注过的边界情形下会失真, 该情形与本页的 HKSCC 单一身份过滤无关(HKSCC 目前 0 例多行,
# 见开发时的只读核验)。
_DEDUPE_KEY = ("stock_code", "report_date", "holder_set", "holder_rank", "is_exit_row")


@dataclass(frozen=True)
class NorthboundResearchConfig:
    data_access_entity: str
    hkscc_holder_code: str
    hkscc_display_name: str
    holder_set: str
    excluded_holder_codes: tuple[str, ...]
    quarter_end_suffixes: tuple[str, ...]
    settle_days_quarterly: int
    settle_days_annual: int
    tabs: dict[str, str]


def load_config(path: Path | None = None) -> NorthboundResearchConfig:
    """读 typed YAML, fail closed —— 未知键/非法取值一律抛, 不给默认值 (红线11)。"""
    src = path or _CONFIG_PATH
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{src} 不是 mapping")
    unknown = set(raw) - _LEGAL_TOP
    if unknown:
        raise ValueError(f"{src}: 顶层未知键 {sorted(unknown)}")

    entity = raw.get("data_access_entity")
    if not isinstance(entity, str) or not entity:
        raise ValueError(f"{src}: data_access_entity 必须是非空字符串")

    hkscc = raw.get("hkscc")
    if not isinstance(hkscc, dict):
        raise ValueError(f"{src}: 缺 hkscc 段")
    unknown = set(hkscc) - _LEGAL_HKSCC
    if unknown:
        raise ValueError(f"{src}: hkscc 未知键 {sorted(unknown)}")
    holder_code = hkscc.get("holder_code")
    if not isinstance(holder_code, str) or not holder_code:
        raise ValueError(f"{src}: hkscc.holder_code 必须是非空字符串")
    display_name = hkscc.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        raise ValueError(f"{src}: hkscc.display_name 必须是非空字符串")
    holder_set = hkscc.get("holder_set")
    if not isinstance(holder_set, str) or not holder_set:
        raise ValueError(f"{src}: hkscc.holder_set 必须是非空字符串")
    excluded = hkscc.get("excluded_holder_codes")
    if excluded is None:
        excluded = []
    if not isinstance(excluded, list) or not all(isinstance(x, str) and x for x in excluded):
        raise ValueError(f"{src}: hkscc.excluded_holder_codes 必须是非空字符串列表")

    suffixes = raw.get("quarter_end_suffixes")
    if not isinstance(suffixes, list) or not suffixes or not all(isinstance(s, str) for s in suffixes):
        raise ValueError(f"{src}: quarter_end_suffixes 必须是非空字符串列表")

    settle = raw.get("disclosure_settle_days")
    if not isinstance(settle, dict):
        raise ValueError(f"{src}: 缺 disclosure_settle_days 段")
    unknown = set(settle) - _LEGAL_SETTLE
    if unknown:
        raise ValueError(f"{src}: disclosure_settle_days 未知键 {sorted(unknown)}")
    q_days = settle.get("quarterly_days")
    a_days = settle.get("annual_days")
    if not isinstance(q_days, int) or isinstance(q_days, bool) or q_days <= 0:
        raise ValueError(f"{src}: disclosure_settle_days.quarterly_days 必须是正整数")
    if not isinstance(a_days, int) or isinstance(a_days, bool) or a_days <= 0:
        raise ValueError(f"{src}: disclosure_settle_days.annual_days 必须是正整数")

    tabs = raw.get("tabs")
    if not isinstance(tabs, dict) or not tabs:
        raise ValueError(f"{src}: 缺 tabs 段")
    unknown = set(tabs) - _LEGAL_TABS
    if unknown:
        raise ValueError(f"{src}: tabs 未知键 {sorted(unknown)}")
    for k, v in tabs.items():
        if v not in _LEGAL_TAB_STATUS:
            raise ValueError(f"{src}: tabs.{k}={v!r} 不在合法取值 {sorted(_LEGAL_TAB_STATUS)} 内")

    return NorthboundResearchConfig(
        data_access_entity=entity,
        hkscc_holder_code=holder_code,
        hkscc_display_name=display_name,
        holder_set=holder_set,
        excluded_holder_codes=tuple(excluded),
        quarter_end_suffixes=tuple(suffixes),
        settle_days_quarterly=q_days,
        settle_days_annual=a_days,
        tabs=dict(tabs),
    )


_DEFAULT_CFG: NorthboundResearchConfig | None = None


def get_config() -> NorthboundResearchConfig:
    """进程级单例 (与 data_access.get_data_access 同模式)。"""
    global _DEFAULT_CFG
    if _DEFAULT_CFG is None:
        _DEFAULT_CFG = load_config()
    return _DEFAULT_CFG


def _to_iso(yyyymmdd: str | None) -> str | None:
    """YYYYMMDD -> YYYY-MM-DD; 已是 ISO/非 8 位/None 原样返回。"""
    if yyyymmdd is None:
        return None
    s = str(yyyymmdd)
    if len(s) == 8 and "-" not in s and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _dedupe_latest_version(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 (stock_code, report_date, holder_set, holder_rank, is_exit_row) 若因供应商重新
    公告而有多个 notice_date 版本, 只取 notice_date 最大(<=as_of 里最新)的那一版 ——
    "读方取 notice_date <= t 的最新版" (holders_top10_schema.py GRAIN 注释)。

    调用前提: 每行的 notice_date 已经是 ISO 字符串 (DataAccess.clean_rows 已归一),
    字符串字典序比较等价于时间序比较。
    """
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = tuple(r.get(k) for k in _DEDUPE_KEY)
        cur = best.get(key)
        if cur is None or (r.get("notice_date") or "") > (cur.get("notice_date") or ""):
            best[key] = r
    return list(best.values())


def _is_quarter_end(report_date_iso: str, cfg: NorthboundResearchConfig) -> bool:
    return report_date_iso[5:] in cfg.quarter_end_suffixes


def _fetch(
    cfg: NorthboundResearchConfig,
    *,
    codes: list[str] | None,
    as_of: str | None,
    conn: Any,
    data_access: DataAccess | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    da = data_access or get_data_access()
    result = da.get(cfg.data_access_entity, codes=codes, as_of=as_of, conn=conn)
    rows = []
    for r in result.rows:
        r = dict(r)
        r["report_date"] = _to_iso(r.get("report_date"))
        rows.append(r)
    return rows, result.provenance


def stock_series(
    stock_code: str,
    as_of: str | None = None,
    *,
    conn: Any = None,
    cfg: NorthboundResearchConfig | None = None,
    data_access: DataAccess | None = None,
) -> dict[str, Any]:
    """单股: 香港中央结算在这只股票十大流通股东名单里的季度序列。

    stock_code: 6 位股票代码 (holders_top10 entity code_col=stock_code, code_mode=plain,
        不做 ts_code 转换)。
    as_of: ISO 决策日, PIT 上界 (notice_date<=as_of)。省略且未注入 conn 时, DataAccess.get
        缺省取"最近完整交易日" (生产路径); 注入 conn 做测试时若也不传 as_of, 则不加上界
        (由调用方自控, 与 DataAccess.get 本身的约定一致)。
    """
    cfg = cfg or get_config()
    stock_code = str(stock_code).strip()
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    rows, provenance = _fetch(cfg, codes=[stock_code], as_of=as_of, conn=conn, data_access=data_access)

    by_period: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_period.setdefault(r["report_date"], []).append(r)

    periods: list[dict[str, Any]] = []
    for report_date in sorted(by_period):
        group = _dedupe_latest_version(by_period[report_date])
        held = [
            r for r in group
            if not r.get("is_exit_row") and r.get("holder_code") == cfg.hkscc_holder_code
        ]
        exited = [
            r for r in group
            if r.get("is_exit_row") and r.get("holder_code") == cfg.hkscc_holder_code
        ]
        any_disclosure = any(not r.get("is_exit_row") for r in group)

        if held:
            # 同一 (股,期,码) 理论上可合法多行 (自有资金/QFII 等不同通道各占一仓) ——
            # 聚合按 SUM, 不假设唯一 (今天刚落地的地基备注)。ratio 若有任一行是 NULL,
            # 视整体为 unknown 而不是"部分求和"——防止用不完整的求和冒充完整比例。
            ratios = [r.get("hold_ratio_float") for r in held]
            ratio_pct = None if any(x is None for x in ratios) else round(sum(ratios), 4)
            ranks = sorted({r.get("holder_rank") for r in held if r.get("holder_rank") is not None})
            periods.append({
                "report_date": report_date,
                "notice_date": max(r.get("notice_date") for r in held),
                "status": "held",
                "holder_rank": ranks[0] if len(ranks) == 1 else ranks,
                "hold_ratio_pct": ratio_pct,
            })
        elif exited:
            last_ratio = next((r.get("hold_ratio_float") for r in exited if r.get("hold_ratio_float") is not None), None)
            periods.append({
                "report_date": report_date,
                "notice_date": max(r.get("notice_date") for r in exited),
                "status": "exited_this_period",
                "holder_rank": None,
                "hold_ratio_pct": None,
                "last_known_hold_ratio_pct": last_ratio,
            })
        elif any_disclosure:
            periods.append({
                "report_date": report_date,
                "notice_date": max(r.get("notice_date") for r in group if not r.get("is_exit_row")),
                "status": "not_in_top10",
                "holder_rank": None,
                "hold_ratio_pct": None,
            })
        # else: 该期只有跟 HKSCC 无关的退出行、没有任何正常持有行 —— 理论上不应发生
        # (退出总意味着别的持有人在场), 出现即代表数据本身有洞, 不编造一条 unknown 记录去
        # 掩盖它; 保持沉默(不产出该期), 交给上层数据质量审计而不是这个展示页去猜。

    return {
        "stock_code": stock_code,
        "holder_code": cfg.hkscc_holder_code,
        "holder_display_name": cfg.hkscc_display_name,
        "holder_set": cfg.holder_set,
        "as_of": provenance.get("as_of"),
        "provenance": provenance,
        "periods": periods,
        "note": (
            None if periods else
            "该股票在可见窗口内没有十大流通股东披露记录 —— 可能从未进入过任何十大流通股东名单, "
            "也可能股票代码本身无效; 本页不做代码有效性校验。"
        ),
    }


def market_breadth(
    as_of: str | None = None,
    *,
    conn: Any = None,
    cfg: NorthboundResearchConfig | None = None,
    data_access: DataAccess | None = None,
) -> dict[str, Any]:
    """全市场: 按标准季度末的"十大流通股东含香港中央结算"覆盖面序列。

    分母口径: 当期有十大流通股东披露的股票数 (不是全市场股票数) —— 停牌/退市清算期/
    未披露的股票不计入分母, 避免把"没数据"误算成"没有北向持仓" (w3 规格 §4.2 附注)。
    """
    cfg = cfg or get_config()
    rows, provenance = _fetch(cfg, codes=None, as_of=as_of, conn=conn, data_access=data_access)

    quarter_rows = [r for r in rows if _is_quarter_end(r["report_date"], cfg)]
    by_period: dict[str, list[dict[str, Any]]] = {}
    for r in quarter_rows:
        by_period.setdefault(r["report_date"], []).append(r)

    decision_ref = provenance.get("as_of")

    periods: list[dict[str, Any]] = []
    for report_date in sorted(by_period):
        group = _dedupe_latest_version(by_period[report_date])
        non_exit = [r for r in group if not r.get("is_exit_row")]
        disclosed_stocks = {r["stock_code"] for r in non_exit}
        hkscc_stocks = {r["stock_code"] for r in non_exit if r.get("holder_code") == cfg.hkscc_holder_code}
        n_disclosed = len(disclosed_stocks)
        n_with_hkscc = len(hkscc_stocks)
        coverage_pct = round(100.0 * n_with_hkscc / n_disclosed, 1) if n_disclosed else None
        notice_dates = [r.get("notice_date") for r in group if r.get("notice_date")]

        disclosure_status = "unknown"
        if decision_ref is not None:
            settle_days = cfg.settle_days_annual if report_date[5:] == "12-31" else cfg.settle_days_quarterly
            try:
                from datetime import date
                ref = date.fromisoformat(decision_ref)
                rpt = date.fromisoformat(report_date)
                disclosure_status = "in_progress" if (ref - rpt).days < settle_days else "settled"
            except ValueError:
                disclosure_status = "unknown"

        periods.append({
            "report_date": report_date,
            "n_disclosed_any_top10": n_disclosed,
            "n_with_hkscc": n_with_hkscc,
            "coverage_pct": coverage_pct,
            "notice_date_min": min(notice_dates) if notice_dates else None,
            "notice_date_max": max(notice_dates) if notice_dates else None,
            "disclosure_status": disclosure_status,
        })

    return {
        "holder_code": cfg.hkscc_holder_code,
        "holder_display_name": cfg.hkscc_display_name,
        "holder_set": cfg.holder_set,
        "as_of": decision_ref,
        "provenance": provenance,
        "periods": periods,
    }


def market_flow_status(cfg: NorthboundResearchConfig | None = None) -> dict[str, Any]:
    """Tab3 (市场资金流概览) 的治理状态 —— 不是数据查询, 不读任何 moneyflow_hsgt 行。

    ``moneyflow_hsgt`` 数据本身在库连续 (20141117-20260828 实测), 但
    ``tushare_sunset.yaml`` 判定 ``decision: retire``, 理由是"零消费"。本模块一旦真的
    去读它, 就会让这条理由自动失效而不经 owner 同意改判 —— 所以本函数只回一段状态说明,
    永远不建立对该表的读取路径。
    """
    cfg = cfg or get_config()
    status = cfg.tabs.get("market_flow", "blocked")
    if status == "blocked":
        return {
            "available": False,
            "reason": (
                "moneyflow_hsgt 在 backend/config/tushare_sunset.yaml 判定 decision=retire, "
                "现行理由是零消费(唯一读取者 northbound_market_flow.py 全仓库零调用方, 是死代码)。"
                "本页一旦读取该表就会成为第一个真实消费方, 让这条理由自动失效——这是需要 owner "
                "先拍板改判(replace 到替代源, 或解除冻结继续用 tushare 到授权到期)的前置阻塞项, "
                "不是本模块能单方绕过的决定。数据本身连续在库, 不是数据不可得。"
            ),
            "blocking_reference": "backend/config/tushare_sunset.yaml:158-160 (moneyflow_hsgt decision=retire)",
        }
    raise NotImplementedError(
        "northbound_research.yaml tabs.market_flow=enabled 但本模块尚无实现 —— "
        "改判后需要新写一条读路径 (含新的 data_access entity 注册), 不是把这个占位改一下状态字符串就行。"
    )


__all__ = [
    "NorthboundResearchConfig",
    "get_config",
    "load_config",
    "market_breadth",
    "market_flow_status",
    "stock_series",
]
