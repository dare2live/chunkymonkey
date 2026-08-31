"""交易日历规则推导器 — 不取数的 source adapter。

**第一性原理**: 交易日 = 周一~周五 − 法定节假日, 没有例外。
节假日是国务院每年 Q4 公布的公开信息, 本就该是配置而非取数
(配置在 ``backend/config/market_holidays.yaml``, 含完整实证与维护说明)。

**为什么需要它**: 未来交易日曾是引入 baostock 的唯一理由
(``check_continuity_integrity`` 的 ``calendar_horizon`` 要求 today 之后仍有
>=60 个已登记交易日)。2026-08-31 baostock 被风控拉黑后实测: 扶摇日历端点窗口锁死
``[今日-1年, 今日]``、通达信只能由已发生 K 线反推、妙想无日历产品线 ——
**三源结构性地都给不了未来交易日**。本 adapter 让日历不依赖任何供应商。

**它与其它 adapter 的根本区别**: 没有网络、没有账号、没有限流、不会被拉黑,
同样输入永远同样输出。所以它也没有 fuyao/baostock/tdxhub 那些坑要封。

**响应形态**: 与 tushare ``trade_cal`` / baostock ``query_trade_dates`` 归一化后
完全一致 (``exchange`` / ``cal_date`` / ``is_open`` / ``pretrade_date``),
故 ``calendar_contract`` 与 ``sync_registry`` 只需改 ``source``/``api``, 其余契约不动。

**未配置年份的语义 (必须传导给消费方)**: 缺年份 = 只按周末规则 = 交易日**偏多**
(多出该年 16~24 个未知节假日), 是**上界不是精确值**。``fetch_raw`` 会把这类年份
列在返回的 ``unconfirmed_years`` 里 (见 ``derive_calendar_rows`` 的 docstring),
调用方不可把它当已确认日历用。
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

ALIAS = "calendar_rule"
API_QUERY_TRADE_DATES = "query_trade_dates"

_CONFIG_REL = Path("backend/config/market_holidays.yaml")
_SSE = "SSE"


class CalendarRuleError(RuntimeError):
    """配置缺失/格式非法/请求区间非法。"""


class CalendarRuleUnconfirmedYearError(CalendarRuleError):
    """请求区间跨入**未配置节假日**的年份 —— fail-closed, 不许静默产出上界日历。

    为什么是 raise 而不是 warning: 规则推导把"供应商断供"(会报错) 换成了"规则过期"
    (不报错), 后者更危险。实证 —— 本类加入前, ``fetch_raw`` 只 log 一条 warning 就把
    ``unconfirmed_years`` 丢掉, 而 2027 只按周末规则推导会多出 **19 个幻影交易日**
    (261 vs 真实约 242); 更糟的是 ``check_continuity_integrity`` 的 ``calendar_horizon``
    门只数 ``future_n``, **幻影日会满足它** —— 门显示绿、数据是错的。
    即项目教训 [[feedback-defer-requires-detectability]]:「说'先不做'前必答它出现时
    哪行代码告诉我」, 答不出就不是推迟是放弃。现在的答案就是这一行。
    """


def _repo_root() -> Path:
    # sources/ -> data_sources/ -> services/ -> backend/ -> repo
    return Path(__file__).resolve().parents[4]


def _config_path() -> Path:
    return _repo_root() / _CONFIG_REL


def _parse_compact(value: str) -> datetime.date:
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise CalendarRuleError(f"日期须为紧凑 8 位 YYYYMMDD, 收到 {value!r}")
    return datetime.date(int(text[:4]), int(text[4:6]), int(text[6:]))


def load_holidays(path: Path | None = None) -> dict[int, set[datetime.date]]:
    """读 market_holidays.yaml -> {年: {节假日}}。段格式 'D' 或 'D~D'。"""
    import yaml

    target = path or _config_path()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CalendarRuleError(f"节假日配置不存在: {target}") from exc
    if not isinstance(raw, dict) or "holidays" not in raw:
        raise CalendarRuleError(f"节假日配置格式非法 (缺 holidays 段): {target}")
    out: dict[int, set[datetime.date]] = {}
    for year, runs in (raw.get("holidays") or {}).items():
        days: set[datetime.date] = set()
        for item in runs or []:
            text = str(item).strip()
            if "~" in text:
                a, b = text.split("~", 1)
                start, end = _parse_compact(a), _parse_compact(b)
                if end < start:
                    raise CalendarRuleError(f"{year}: 段起止倒置 {text!r}")
                cur = start
                while cur <= end:
                    days.add(cur)
                    cur += datetime.timedelta(days=1)
            else:
                days.add(_parse_compact(text))
        out[int(year)] = days
    return out


def derive_calendar_rows(
    start: datetime.date,
    end: datetime.date,
    holidays: dict[int, set[datetime.date]],
) -> tuple[list[dict[str, Any]], list[int]]:
    """推导 [start, end] 每一天, 返回 (rows, unconfirmed_years)。

    ``rows`` 与 tushare trade_cal 同形: exchange / cal_date / is_open / pretrade_date。
    ``is_open`` 为 ``'1'``/``'0'`` 字符串 (与既有 landing 形态一致, 不是 int)。
    ``pretrade_date`` = 序列中上一个开市日; 首个开市日之前为 None。

    ``unconfirmed_years`` = 区间内**未配置节假日**的年份 —— 那些年只按周末规则,
    交易日偏多, 是上界。调用方必须传导这个信号, 不可静默当已确认日历。
    """
    if end < start:
        raise CalendarRuleError(f"区间倒置: {start} > {end}")
    configured = set(holidays)
    unconfirmed = sorted({y for y in range(start.year, end.year + 1) if y not in configured})
    rows: list[dict[str, Any]] = []
    prev_open: str | None = None
    cur = start
    while cur <= end:
        is_open = cur.weekday() < 5 and cur not in holidays.get(cur.year, ())
        compact = cur.strftime("%Y%m%d")
        rows.append(
            {
                "exchange": _SSE,
                "cal_date": compact,
                "is_open": "1" if is_open else "0",
                "pretrade_date": prev_open,
            }
        )
        if is_open:
            prev_open = compact
        cur += datetime.timedelta(days=1)
    return rows, unconfirmed


class CalendarRuleSource:
    """sync_runner 调用约定入口, 与 sources/baostock.py 等同型: fetch_raw(api, **params)。"""

    name = ALIAS

    def __init__(self, *, config_path: Path | None = None) -> None:
        self._config_path = config_path

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        # allow_unconfirmed: 显式承认"我只要上界"才放行 —— 默认 fail-closed。
        allow_unconfirmed = bool(params.pop("allow_unconfirmed", False))
        name = str(api or "").strip()
        if name != API_QUERY_TRADE_DATES:
            raise KeyError(
                f"calendar_rule: unknown api {api!r} (known: {API_QUERY_TRADE_DATES!r})"
            )
        start_raw = params.get("start_date")
        end_raw = params.get("end_date")
        if not start_raw or not end_raw:
            raise CalendarRuleError(
                "calendar_rule 需要显式 start_date/end_date (紧凑 8 位) —— "
                "推导器能生成任意区间, 不设隐式默认以免静默产出非预期跨度"
            )
        start = _parse_compact(start_raw)
        end = _parse_compact(end_raw)
        holidays = load_holidays(self._config_path)
        rows, unconfirmed = derive_calendar_rows(start, end, holidays)
        if unconfirmed and not allow_unconfirmed:
            raise CalendarRuleUnconfirmedYearError(
                f"calendar_rule: 请求区间 {start_raw}~{end_raw} 跨入未配置节假日的年份 "
                f"{unconfirmed} —— 这些年只按周末规则推导, 交易日**偏多**(每年多 16~24 个"
                f"幻影交易日), 是上界不是精确值。修法: 国务院公布该年放假安排后, 在 "
                f"backend/config/market_holidays.yaml 的 holidays 段加一个年份条目 "
                f"(约 10 行)。若确实只需要上界(如容量估算), 显式传 allow_unconfirmed=True。"
            )
        limit = params.get("limit")
        offset = int(params.get("offset") or 0)
        if limit is not None:
            rows = rows[offset : offset + int(limit)]
        elif offset:
            rows = rows[offset:]
        return rows


__all__ = [
    "ALIAS",
    "CalendarRuleError",
    "CalendarRuleUnconfirmedYearError",
    "CalendarRuleSource",
    "derive_calendar_rows",
    "load_holidays",
]
