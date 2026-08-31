"""Miaoxiang (妙想 / 东方财富 datacenter v1) adapter — 龙虎榜两域 (top_inst / top_list).

源决策: ``backend/config/tushare_sunset.yaml`` — TuShare 授权 2026-09-10 到期不续期,
``top_inst``/``top_list`` 两域裁决 = replace, replacement = 妙想
``RPT_OPERATEDEPT_TRADE`` / ``RPT_DAILYBILLBOARD_DETAILSNEW``。对账证据 (2026-08-25,
``data/audit/historical/assignment_gap_recon.json`` "lhb_seats"/"lhb_vs_top_list"):
两域均 identity=true, jaccard=1.0 (526/526 seat keys, 60/60 codes), 精确集合对比
非抽样。生成该对账的脚本是 ``backend/scripts/recon_assignment_gaps.py`` — 本 adapter
复用它已验证的调用形态 (``client.get_v1(report, page=, page_size=, extra_filters=,
sort_columns=, sort_types=)``)，不是另起炉灶。

范式来源: ``services/holders_aif10.py`` 是本项目现行生产在用的妙想 aif10 接入范式
(建连接/限流/分页的形状); 本文件是它在 ``sync_runner`` 适配层的姊妹实现 —
``fetch_raw(api, **params) -> list[dict]``，与 ``sources/fuyao.py`` /
``sources/baostock.py`` / ``sources/tushare.py`` 同型。

**范围声明**: 本文件只提供接入能力 (source adapter) + 落地字段映射，**不**改
``sync_registry.yaml`` — ``top_inst``/``top_list`` 两域的 ``source:`` 仍是
``tushare``，接线 (改 ``source:``、接 ``sync_runner._adapter()`` 分发表) 是另一刀。

字段映射证据 (2026-08-31 逐字段实测核对, 非猜测 — 见 ``backend/scripts`` 下同日
生成的临时核对脚本, 结论落这里):

``top_inst`` ← ``RPT_OPERATEDEPT_TRADE`` (grain: trade_date × ts_code × exalter ×
side, registry 备注 "TRADE_DIRECTION=0 买 / 1 卖" 与 tushare ``side`` 值域 '0'/'1'
逐位相同, 无需转换):

=====================  =====================  ==============================
tushare (raw_tushare_top_inst)   妙想 (RPT_OPERATEDEPT_TRADE)   备注
=====================  =====================  ==============================
trade_date             TRADE_DATE             "YYYY-MM-DD 00:00:00" -> compact
ts_code                SECUCODE               已是 "600000.SH" 形态, 直通
exalter                OPERATEDEPT_NAME       营业部全名, 直通
side                   TRADE_DIRECTION        '0'/'1' 字符串, 直通 (值域相同)
buy                    BUY_AMT_REAL           实测逐行相等 (20260825 000017.SZ);
                                               单向席位(只买或只卖)另一侧妙想返回
                                               JSON null 而 tushare 是 0.0 (实测
                                               20260825 全天 130/650=20% 行命中),
                                               ``_amount()`` 把 None 归零对齐
sell                   SELL_AMT_REAL          实测逐行相等; None->0.0 同上
buy_rate               BUY_RATIO              tushare 显示 2 位小数四舍五入版本,
                                               妙想更高精度, 语义相同不改精度;
                                               None->0.0 同 buy
sell_rate              SELL_RATIO             同 buy_rate 精度说明; None->0.0 同 sell
net_buy                NET                    **不是** NET_BUY (那是整只股票全日
                                               净买额, 与 top_list.net_amount 对应);
                                               行级净买实测等于 BUY_AMT_REAL-
                                               SELL_AMT_REAL, 与 tushare net_buy
                                               逐行相等; None->0.0 同 buy (未实测到
                                               真实 None 案例, 防御性对齐)
reason                 EXPLANATION            上榜理由原文, 逐行相等
built_at               (无 — sync_runner 落地统一生成, adapter 不返回此列)
=====================  =====================  ==============================

``top_list`` ← ``RPT_DAILYBILLBOARD_DETAILSNEW`` (grain: trade_date × ts_code ×
reason):

=====================  =========================  ==========================
tushare (raw_tushare_top_list)   妙想 (RPT_DAILYBILLBOARD_DETAILSNEW)  备注
=====================  =========================  ==========================
trade_date             TRADE_DATE                 compact 同上
ts_code                SECUCODE                   直通
name                   SECURITY_NAME_ABBR         直通
close                  CLOSE_PRICE                实测逐行相等
pct_change             CHANGE_RATE                实测逐行相等
turnover_rate          TURNOVERRATE               tushare 2 位小数四舍五入
amount                 ACCUM_AMOUNT               实测逐行相等 (全天成交额)
l_sell                 BILLBOARD_SELL_AMT         龙虎榜席位合计卖出额
l_buy                  BILLBOARD_BUY_AMT          龙虎榜席位合计买入额
l_amount               BILLBOARD_DEAL_AMT         龙虎榜席位合计成交额
net_amount             BILLBOARD_NET_AMT          实测逐行相等 (非 NET_BS_AMT,
                                                   两者本次样本数值相同但取
                                                   BILLBOARD_NET_AMT 因其命名
                                                   与 tushare net_amount 语义
                                                   直接对应)
net_rate               DEAL_NET_RATIO             tushare 2 位小数四舍五入
amount_rate            DEAL_AMOUNT_RATIO          tushare 1 位小数四舍五入
float_values           FREE_MARKET_CAP            实测逐行相等 (流通市值)
reason                 EXPLANATION                上榜理由原文, 逐行相等
built_at               (无 — 同上)
=====================  =========================  ==========================

已知的 grain 内碰撞 (非本 adapter bug, 接线前必读): ``top_inst`` 的 registry
grain ``[trade_date, ts_code, exalter, side]`` 在东财原始数据里**不总是唯一**——
实测 20260825 全天 613 条原始行里 87 个 grain key 对应 >1 行, 成因两类:
(1) ``exalter='机构专用'`` 是匿名标签, 同一 (ts_code, side) 下可能有多个不同的
真实机构账户共用这个名字 (无法进一步区分, 东财自己也不暴露账户标识);
(2) 同一席位当天登上该股**两块不同龙虎榜** (如换手率 20% 触发榜 + 跌幅偏离值 7%
触发榜), 该席位的买卖数字在两块榜上相同, 但 ``reason``(EXPLANATION) 不同。
逐行核对 (见 2026-08-31 scratch 脚本, 结论落此) 证实: 对每一个 tushare 侧的
grain key, 上述碰撞组里**总能找到一行**在 buy/sell/net_buy/reason 上与 tushare
逐位相等 (526/526) —— 即 tushare 自己也在做同样的"同 grain 落地行取一"选择,
只是没暴露落选的那些行。本 adapter **不**在 ``fetch_raw`` 内去重 (保留全部原始
行, 与 ``fuyao.py`` 同一路数据的处理方式一致) —— grain 去重是
``sync_runner._prepare_batch_df`` 的 ``drop_duplicates(grain, keep='last')``
统一职责, 不在 adapter 层重复实现; 落地时"取哪一行"由此变成确定性的
(keep='last'), 不再是未知的 tushare 内部规则, 属于换源后行为收敛而非退化。

失败姿态 (fail-closed, 教训: 静默半批比报错更危险):
  - 未知 ``api`` / 传了 ``limit``/``offset``/``page``/``page_size`` (分页仅限
    adapter 内部, 调用方不得指定) -> ``MiaoxiangSourceError``
  - 分页落地行数 < 供应商声明 count (超容差) -> ``MiaoxiangTruncationError``
    (复用本仓 ``services/data_sources/pagination_integrity.py`` 的东财 v1
    100 页硬上限截断判定, 不重新发明)
  - 单行缺 grain 关键字段 (SECUCODE/TRADE_DATE/OPERATEDEPT_NAME/TRADE_DIRECTION
    之一) -> ``MiaoxiangMissingFieldError``
  - 上游 HTTP/JSON 错误 -> ``aif10_scraper`` 客户端自带 retry(3 次, 指数退避)
    耗尽后原样抛出 ``AIF10Error``, 本 adapter 不吞

依赖注入: ``MiaoxiangSource(client=...)`` 可注入假客户端 (测试用, 只需实现
``get_v1(report_name, *, page, page_size, sort_columns, sort_types, columns,
secucode, extra_filters) -> {"pages": int, "data": list[dict], "count": int}``
这一个方法, 与 ``aif10_scraper.client.AIF10Client.get_v1`` 同签名)。不传时首次
调用才真实 import ``aif10_scraper`` 并 ``ensure_import_path("miaoxiang")``
(sibling repo 不存在也不报错 —— ``ensure_import_path`` 默认 ``strict=False``),
测试因此从不触网、也不要求 sibling checkout 存在 (CI 浅克隆无此目录)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from services.data_sources.pagination_integrity import (
    EASTMONEY_V1_MAX_PAGES_PER_QUERY,
    assess_paginated_land,
)
from services.data_sources.sibling_repos import ensure_import_path

ALIAS = "miaoxiang"

REPORT_TOP_INST = "RPT_OPERATEDEPT_TRADE"
REPORT_TOP_LIST = "RPT_DAILYBILLBOARD_DETAILSNEW"

# api 名 -> 妙想 reportName。刻意用 registry 现有 `api:` 同名值 (top_inst/top_list),
# 接线时只需改 `source:`, 不用改 `api:` (任务边界: 接线由主线做, 这里只保证形状对上)。
API_REPORT_NAMES: dict[str, str] = {
    "top_inst": REPORT_TOP_INST,
    "top_list": REPORT_TOP_LIST,
}

# 东财 datacenter v1 单页上限 (实测, 与 aif10_scraper/registry.py 一致); 龙虎榜单日
# 行数远小于此 (实测 20260825 top_inst 650 行/2 页, top_list 60 行/1 页 @page_size=500),
# 但仍按同源其它域 (fuyao MAX_PAGES=50) 的防御性上限做法, 防接口异常死循环。
PAGE_SIZE = 500
MAX_PAGES = 20

# 排序: 直接抄 aif10_scraper/registry.py 里这两个 ReportSpec 已登记的值 (2026-08-31
# 读取核实), 不经 aif10_scraper.registry.get_report() 反查 —— 避免测试路径依赖
# sibling repo 是否安装。RPT_OPERATEDEPT_TRADE 排序为空 (spec 原样如此,
# recon_assignment_gaps.py._miaoxiang_pages 同样传空), 不影响正确性: 按单 trade_date
# 全量分页拉取、grain 去重在 sync_runner._prepare_batch_df 完成。
_SORT_BY_API: dict[str, tuple[str, str]] = {
    "top_inst": ("", "-1"),
    "top_list": ("SECURITY_CODE,TRADE_DATE", "1,-1"),
}

_BANNED_CALLER_PAGING_KEYS = frozenset({"limit", "offset", "page", "page_size"})


class MiaoxiangSourceError(RuntimeError):
    """Adapter-level failure: unknown api / caller passed forbidden paging kwargs."""


class MiaoxiangTruncationError(RuntimeError):
    """Pagination landed fewer rows than the provider declared (fail-closed)."""


class MiaoxiangMissingFieldError(ValueError):
    """A grain-critical vendor field was absent/empty on a landed row."""


def _reject_caller_paging(params: dict[str, Any]) -> None:
    present = sorted(k for k in _BANNED_CALLER_PAGING_KEYS if k in params)
    if present:
        raise MiaoxiangSourceError(
            "miaoxiang pagination is internal (caller passes trade_date only); "
            f"got {present}"
        )


def compact_trade_date(value: Any) -> str:
    """Normalize a caller-supplied date (or a vendor ``TRADE_DATE`` timestamp
    string) to compact ``YYYYMMDD``. Raises on anything that doesn't parse to
    a real calendar date — a bad trade_date must never silently become an
    empty/garbage-filtered query."""
    text = str(value or "").strip()
    if not text:
        raise MiaoxiangSourceError("miaoxiang: trade_date required")
    if "T" in text:
        text = text[:10]
    if " " in text:
        text = text.split(" ", 1)[0]
    digits = text.replace("-", "")[:8]
    if len(digits) != 8 or not digits.isdigit():
        raise MiaoxiangSourceError(f"miaoxiang: bad trade_date {value!r}")
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError as exc:
        raise MiaoxiangSourceError(f"miaoxiang: bad trade_date {value!r}") from exc
    return digits


def _dashed_date(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    t = str(value).strip()
    return t or None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amount(value: Any) -> float:
    """Trade-amount field where "no activity on this side" is a real zero, not
    an unknown. 实测 2026-08-31 (20260825 全天, 130/650 行, 20%): 东财
    ``RPT_OPERATEDEPT_TRADE`` 对单向席位 (只买不卖 / 只卖不买) 把另一侧
    ``BUY_AMT_REAL``/``SELL_AMT_REAL``/``BUY_RATIO``/``SELL_RATIO`` 返回 JSON
    null, 而 tushare 对应字段是 ``0.0`` —— 两者语义相同 ("这一侧没有交易"),
    只是 null-vs-zero 的表示差异, 不是缺数据。``_float`` 对其它字段 (可能真的
    无意义, 如债券的 turnover_rate/float_values) 保留 None 传透, 此处专用于
    这四个金额/占比字段, 避免下游 ``SUM(net_buy)`` 等聚合遇 NULL 传播。"""
    v = _float(value)
    return 0.0 if v is None else v


_REQUIRED_TOP_INST_FIELDS = ("SECUCODE", "TRADE_DATE", "OPERATEDEPT_NAME", "TRADE_DIRECTION")
_REQUIRED_TOP_LIST_FIELDS = ("SECUCODE", "TRADE_DATE")


def clean_top_inst_row(row: dict[str, Any], *, trade_date: str) -> dict[str, Any]:
    """``RPT_OPERATEDEPT_TRADE`` row -> ``raw_tushare_top_inst``-shaped dict.

    See module docstring field-mapping table for provenance of every mapping.
    """
    missing = [f for f in _REQUIRED_TOP_INST_FIELDS if row.get(f) in (None, "")]
    if missing:
        raise MiaoxiangMissingFieldError(
            f"RPT_OPERATEDEPT_TRADE row missing grain fields {missing}"
        )
    ts_code = _text(row.get("SECUCODE"))
    exalter = _text(row.get("OPERATEDEPT_NAME"))
    side = _text(row.get("TRADE_DIRECTION"))
    if not ts_code or not exalter or side not in ("0", "1"):
        raise MiaoxiangMissingFieldError(
            f"RPT_OPERATEDEPT_TRADE bad grain values ts_code={ts_code!r} "
            f"exalter={exalter!r} side={side!r}"
        )
    return {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "exalter": exalter,
        "side": side,
        "buy": _amount(row.get("BUY_AMT_REAL")),
        "buy_rate": _amount(row.get("BUY_RATIO")),
        "sell": _amount(row.get("SELL_AMT_REAL")),
        "sell_rate": _amount(row.get("SELL_RATIO")),
        "net_buy": _amount(row.get("NET")),
        "reason": _text(row.get("EXPLANATION")),
    }


def clean_top_list_row(row: dict[str, Any], *, trade_date: str) -> dict[str, Any]:
    """``RPT_DAILYBILLBOARD_DETAILSNEW`` row -> ``raw_tushare_top_list``-shaped dict."""
    missing = [f for f in _REQUIRED_TOP_LIST_FIELDS if row.get(f) in (None, "")]
    if missing:
        raise MiaoxiangMissingFieldError(
            f"RPT_DAILYBILLBOARD_DETAILSNEW row missing grain fields {missing}"
        )
    ts_code = _text(row.get("SECUCODE"))
    if not ts_code:
        raise MiaoxiangMissingFieldError("RPT_DAILYBILLBOARD_DETAILSNEW missing SECUCODE")
    return {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "name": _text(row.get("SECURITY_NAME_ABBR")),
        "close": _float(row.get("CLOSE_PRICE")),
        "pct_change": _float(row.get("CHANGE_RATE")),
        "turnover_rate": _float(row.get("TURNOVERRATE")),
        "amount": _float(row.get("ACCUM_AMOUNT")),
        "l_sell": _float(row.get("BILLBOARD_SELL_AMT")),
        "l_buy": _float(row.get("BILLBOARD_BUY_AMT")),
        "l_amount": _float(row.get("BILLBOARD_DEAL_AMT")),
        "net_amount": _float(row.get("BILLBOARD_NET_AMT")),
        "net_rate": _float(row.get("DEAL_NET_RATIO")),
        "amount_rate": _float(row.get("DEAL_AMOUNT_RATIO")),
        "float_values": _float(row.get("FREE_MARKET_CAP")),
        "reason": _text(row.get("EXPLANATION")),
    }


_CLEANERS: dict[str, Callable[..., dict[str, Any]]] = {
    "top_inst": clean_top_inst_row,
    "top_list": clean_top_list_row,
}

ClientFactory = Callable[[], Any]


class MiaoxiangSource:
    """sync_runner adapter: ``fetch_raw(api, **params) -> list[dict]``.

    Pagination/rate-limit/retry are entirely internal (mirrors ``fuyao.py`` /
    ``baostock.py``): callers pass only ``trade_date``; the underlying
    ``aif10_scraper.client.AIF10Client`` already retries transient HTTP/JSON
    failures (3 attempts, exponential backoff) before raising, so this class
    does not add a second retry layer on top of it.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        ensure_import_path("miaoxiang")
        from aif10_scraper.client import AIF10Client  # noqa: E402

        self._client = AIF10Client()
        return self._client

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        name = str(api or "").strip()
        if name not in API_REPORT_NAMES:
            raise MiaoxiangSourceError(
                f"miaoxiang: unknown api {api!r}; known={sorted(API_REPORT_NAMES)}"
            )
        _reject_caller_paging(params)
        trade_date = compact_trade_date(params.get("trade_date"))
        report_name = API_REPORT_NAMES[name]
        sort_columns, sort_types = _SORT_BY_API[name]
        raw_rows = self._fetch_report_day(
            report_name,
            trade_date,
            sort_columns=sort_columns,
            sort_types=sort_types,
        )
        cleaner = _CLEANERS[name]
        return [cleaner(r, trade_date=trade_date) for r in raw_rows]

    def _fetch_report_day(
        self,
        report_name: str,
        trade_date: str,
        *,
        sort_columns: str,
        sort_types: str,
    ) -> list[dict[str, Any]]:
        client = self._get_client()
        extra_filters = [f"(TRADE_DATE='{_dashed_date(trade_date)}')"]
        rows: list[dict[str, Any]] = []
        provider_count = 0
        for page in range(1, MAX_PAGES + 1):
            resp = client.get_v1(
                report_name,
                page=page,
                page_size=PAGE_SIZE,
                sort_columns=sort_columns,
                sort_types=sort_types,
                columns="ALL",
                secucode=None,
                extra_filters=extra_filters,
            )
            data = list((resp or {}).get("data") or [])
            provider_count = int((resp or {}).get("count") or 0)
            pages_total = int((resp or {}).get("pages") or 0)
            rows.extend(data)
            if not data:
                break
            if page >= pages_total:
                break
        else:
            raise MiaoxiangTruncationError(
                f"miaoxiang {report_name} {trade_date} exceeded {MAX_PAGES} pages "
                "without exhausting pagination"
            )
        verdict = assess_paginated_land(
            expected_count=provider_count,
            landed_rows=len(rows),
            page_size=PAGE_SIZE,
            max_pages_per_query=EASTMONEY_V1_MAX_PAGES_PER_QUERY,
        )
        if verdict.truncated:
            raise MiaoxiangTruncationError(
                f"miaoxiang {report_name} {trade_date} truncated: "
                f"{','.join(verdict.reasons)} "
                f"expected={verdict.expected_count} landed={verdict.landed_rows}"
            )
        return rows


__all__ = [
    "ALIAS",
    "API_REPORT_NAMES",
    "MAX_PAGES",
    "PAGE_SIZE",
    "REPORT_TOP_INST",
    "REPORT_TOP_LIST",
    "MiaoxiangMissingFieldError",
    "MiaoxiangSource",
    "MiaoxiangSourceError",
    "MiaoxiangTruncationError",
    "clean_top_inst_row",
    "clean_top_list_row",
    "compact_trade_date",
]
