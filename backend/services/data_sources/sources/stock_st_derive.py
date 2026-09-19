"""ST 名称派生器 — 不取数的 source adapter, 双水库派生 (2026-09-18 ST 契约 v2 刀3)。

**第一性原理** (未变): ST 的可观测形态就是**股票简称带 ``ST``/``*ST`` 前缀** ——
这是交易所自己的展示规则 (《股票上市规则》风险警示), 不是某个供应商的私有标注。

**架构 (业主 09-17 "水龙头 → 水库 → 加工 → 应用")**: 本 adapter 从两个本地水库
派生, 自己**仍然不触网**:
  1. **名称快照路径** (快照当天): 读 ``raw_tushare_stock_basic`` (stock_basic 域
     每日刷新的证券名称快照, 覆盖沪深京 + 停牌股, 09:20 前后可用) —— 与旧版同一
     机制, 只是逐行标 ``st_origin=derived_name_prefix``。
  2. **baostock 水库路径** (其它日期): 读 ``raw_baostock_daily_k`` (daily 适配器
     每天顺手灌的 baostock ``isST`` 观测, 只覆盖沪深, 只覆盖水库里当日有行的代码)
     —— 逐行标 ``st_origin=provider_baostock_isst``, ``name`` 结构性为 ``None``
     (baostock 不给简称)。
  3. 两者皆不满足 (既不是快照当天, 水库也没有该日含 isST 字段的行) → 结构性
     答不出, 抛 ``StockSTUnanswerableError`` (``SourceCannotAnswerDateError`` 的
     子类, ``sync_runner._fetch_with_retry`` 对它零重试零退避原样上抛)。

**为什么本 adapter 不能自己去连 baostock**: 同一个 ``chunkyctl sync``/daily_update
进程里, daily 适配器的 ``FuyaoSource`` 已经持有 baostock 的会话级 flock 且不释放
(``sources/baostock.py`` 坑 5) —— 本 adapter 若再起一个 ``BaostockSource`` 实例
去 login, 会当场 ``BaostockConcurrencyError``。让 ST 只读水库、水库由 daily 顺手
灌 (+ 一个独立进程的回填命令), 是唯一不打架的形状。

**名称快照仍是当天首选** (不是"退而求其次"): 它覆盖北交所与停牌股、零网络、
09:20 就能答; baostock 只覆盖沪深且要收盘后才有当天行。

**快照日归属** (F2 修法, 见 :func:`snapshot_day_for`): ``built_at`` (sync_runner
写入的 aware UTC 时刻) 必须先转到 ``stock_st_acquire.yaml`` 的
``name_snapshot.timezone`` 再取日期, 且当地时间早于 ``attribution_cutoff_local``
时不归属当天 (fail-closed, 而不是拿一个"可能还没反映当天戴帽/摘帽"的快照冒充)。
原来的实现直接取 ``built_at`` 的 **UTC** 日期, 会让 CST 00:00-08:00 之间跑的日更
把快照错误地归到前一天。

**历史正则实测 (2026-08-31/09-01, 见 test_stock_st_derive.py 复现, 未因本次改动
而改变)**:
- 召回: 本模块正则套到 ``canonical_stock_st_daily`` 全部历史 (2022-01-04~
  2026-08-28, 173,413 行, 600 只曾 ST 代码), 0 miss (未加固的朴素正则在同一份
  历史上有 116 处 miss: XD/XR/DR 除权除息装饰前缀 与 遗留 SST 无星号形态)。
- 精度: 同一正则套到 2026-08-31 全市场快照 (5563 行), 0 假阳性。

**historical 数据处置** (不变): ``canonical_stock_st_daily`` 现有 tushare 时代
历史 (2022-01-04~2026-08-28) 原样保留, 不可也不必用名称快照重建 (2026-09-18
起也不能用 baostock 水库重建那段历史之前的日子——水库回填从 08-28 起, 更早的
日子既无名称快照痕迹也无 baostock 水库记录, 保持原样)。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from services.data_sources.fetch_verdict import SourceCannotAnswerDateError
from services.data_sources.security_day_capture import ProviderPage
from services.data_sources.stock_st_acquire_rules import (
    StockStAcquireRules,
    load_stock_st_acquire_rules,
)

ALIAS = "stock_st_derive"
API_STOCK_ST = "stock_st"  # 与原 sync_registry `api: stock_st` 同名, 换源只需改 `source:`

# 剥离除权除息临时前缀 (XD/XR/DR, 可叠加多次理论上不会但防御性用 * 而非 ?) 后,
# 按 "可选 S(股改未完成遗留) + 可选 * + ST" 匹配。结构性正则, 不是参数 (不随供应
# 商/业务变化) —— 留在代码里, 与 membership_labels 这类真参数分开。见类 docstring
# 实测: 173,413 行历史 0 miss, 5563 行今日快照 0 假阳性。
_ST_NAME_RE = re.compile(r"^(?:XD|XR|DR)*S?\*?ST", re.IGNORECASE)


class StockSTDeriveError(RuntimeError):
    """配置/输入非法, 或两条路径都没有名称数据可读。"""


class StockSTUnanswerableError(SourceCannotAnswerDateError):
    """本 adapter 对某个具体 trade_date 结构性地答不出 —— 既不是快照当天 (名称
    路径), 水库也没有该日含 isST 字段的行 (baostock 路径)。``sync_runner.
    _fetch_with_retry`` 对它零重试零退避原样上抛; 计划器/运行器收口成 typed
    ``unanswerable`` 结果, 不进 ``ctx.degraded``。"""


@dataclass(frozen=True)
class DateAnswerability:
    answerable: bool
    path: str | None  # "name_snapshot" | "baostock_reservoir" | None
    reason: str | None  # None when answerable
    remedy: str | None = None  # None when answerable; same text fetch_raw() would raise with
    detail: Mapping[str, Any] | None = None  # 2026-09-19 返修 B1: 覆盖判据的缺失数/样例代码


def _unanswerable_remedy(day: str, reason: str, *, isst_field: str) -> str:
    """``reason`` -> 给人看的下一步动作建议。两处共用 (``fetch_raw`` 的
    ``StockSTUnanswerableError`` 与 ``answerable_dates`` 的
    ``DateAnswerability.remedy``), 不写两份会漂移的文案。

    ``isst_field`` (B4 修法, 2026-09-19 返修): 字段名从调用方现读的配置传入,
    无默认值、必须显式传——不在这里写字面量副本, 否则配置改了字段名这条人看
    的文案却不跟, 两处判据/文案分歧 (同 ``dates_with_isst_rows`` 那次教训)。"""

    field = isst_field
    if reason == "reservoir_coverage_incomplete":
        return (
            f"raw_baostock_daily_k 该日 ({day}) 覆盖不全 (required(D) 里有代码没有含 {field} "
            "字段的观测行——可能是当天停牌未进 dump, 也可能是升版前的旧行) —— "
            f"backend/scripts/ingest_baostock_daily_k_reservoir.py --start {day} --end {day} "
            "--execute 补齐缺失代码"
        )
    if reason == "reservoir_rows_lack_isst_field":
        return (
            f"raw_baostock_daily_k 该日 ({day}) 有行但都不含 {field} 字段 (契约升版前"
            f"落的旧行) —— 重新用 ingest_baostock_daily_k_reservoir.py 灌一版含 {field} 的行"
        )
    if reason == "no_local_source_for_date":
        return (
            f"backend/scripts/ingest_baostock_daily_k_reservoir.py --start {day} "
            f"--end {day} --execute, 或对齐 stock_basic 快照日"
        )
    return ""


def _exchange_suffix(ts_code: str) -> str:
    """``600000.SH`` -> ``SH``；无点号 (非法输入防御性) -> 空串。"""
    text = str(ts_code or "").strip().upper()
    return text.rsplit(".", 1)[-1] if "." in text else ""


def required_codes_for_date(conn: Any, day: date, rules: StockStAcquireRules) -> frozenset[str]:
    """required(D) —— B1 覆盖判据的分母, ``answerable_dates`` 与 ``fetch_raw``
    共用本函数 (一处定义, 不重复各写一套集合运算——B4 同款教训)。

    = D 日 ``canonical_nominal_ohlcv_daily`` 里的沪深代码
      ∪ 最近一个 ``< D`` 的已 accepted ``canonical_stock_st_daily`` 分区里的沪深代码。

    只读**已 accepted** 数据: D 日 K 线是 daily 域先落地才轮到 stock_st 规划
    (F10 顺序修法), 且只找严格早于 D 的 ST 分区——不读 D 当天或之后的 ST 分区
    (红线 1 PIT: 决策时点只能读当时可得的数据; 找"最近一个 < D" 而不是"上一个
    交易日", 是因为 D 之前也可能连续几天走同一条路径不可答, 上一个交易日未必
    有已 accepted 的 ST 分区)。

    "沪深" 的判定复用 ``coverage_exchanges_for("provider_baostock_isst")``
    (即 ``stock_st_acquire.yaml`` 的 ``sh_sz`` 覆盖组), 不写字面量 {"SH","SZ"}
    (业主 09-16 参数规则)。
    """
    exchanges = rules.coverage_exchanges_for("provider_baostock_isst")

    daily_rows = conn.execute(
        "SELECT DISTINCT ts_code FROM canonical_nominal_ohlcv_daily WHERE trade_date = ?",  # rule-compliance: ok evidence=read-accepted-daily-truth-source-for-st-coverage-denominator
        [day],
    ).fetchall()
    codes = {
        str(r[0]).strip().upper()
        for r in daily_rows
        if r and r[0] and _exchange_suffix(r[0]) in exchanges
    }

    prior = conn.execute(
        "SELECT MAX(trade_date) FROM canonical_stock_st_daily WHERE trade_date < ?",  # rule-compliance: ok evidence=find-most-recent-accepted-st-partition-strictly-before-d-pit-safe
        [day],
    ).fetchone()
    prior_date = prior[0] if prior else None
    if prior_date is not None:
        prior_rows = conn.execute(
            "SELECT DISTINCT ts_code FROM canonical_stock_st_daily WHERE trade_date = ?",  # rule-compliance: ok evidence=read-accepted-prior-st-partition-codes-for-coverage-denominator
            [prior_date],
        ).fetchall()
        codes |= {
            str(r[0]).strip().upper()
            for r in prior_rows
            if r and r[0] and _exchange_suffix(r[0]) in exchanges
        }
    return frozenset(codes)


def _default_required_codes_for_date(requested: date, rules: StockStAcquireRules) -> frozenset[str]:
    """``fetch_raw`` 用的默认 required(D) 取数: 自开自关只读连接 (与
    ``_DefaultReservoirReader``/``_default_name_rows_provider`` 同型, 不与同
    进程可能已开的写连接共存冲突)。"""

    from services.data_access.resolver import connect_ro

    conn = connect_ro("tushare_raw")
    try:
        return required_codes_for_date(conn, requested, rules)
    finally:
        conn.close()


@dataclass(frozen=True)
class _ReservoirCoverageVerdict:
    answerable: bool
    with_isst_rows: tuple[Any, ...]
    missing_codes: frozenset[str]


def _evaluate_reservoir_coverage(
    reservoir_rows: Sequence[Any], required: frozenset[str], *, isst_field: str
) -> _ReservoirCoverageVerdict:
    """B1 覆盖判据的核心比较, ``fetch_raw`` 与 ``answerable_dates`` 共用 (一处
    定义): required(D) 里的每一个代码, 水库该日必须有一行含 ``isst_field``
    (不论其值是 "1" 还是 "0" —— 覆盖问的是"观测到了没有", 不是"是不是 ST")。
    覆盖不全 (``missing_codes`` 非空) → 不可答, 不 accept 任何行。"""

    with_isst = tuple(_rows_with_isst_field(reservoir_rows, isst_field=isst_field))
    with_isst_codes = {str(r.ts_code).strip().upper() for r in with_isst}
    missing = required - with_isst_codes
    return _ReservoirCoverageVerdict(
        answerable=not missing, with_isst_rows=with_isst, missing_codes=frozenset(missing)
    )


def _coverage_incomplete_detail(missing: frozenset[str]) -> dict[str, Any]:
    return {"missing_count": len(missing), "missing_sample": sorted(missing)[:20]}


def name_flags_st(name: Any) -> bool:
    """当前证券简称是否带 ST/*ST 风险警示前缀 (含除权除息装饰前缀剥离)。"""
    text = str(name or "").strip().replace(" ", "")
    return bool(_ST_NAME_RE.match(text))


def snapshot_day_for(built_at: Any, rules: StockStAcquireRules) -> date | None:
    """名称快照归属日 (F2 修法)。``built_at`` 可以是 aware ``datetime`` 或可解析
    的 ISO 字符串; naive (无 tzinfo) 一律 ``None`` (fail-closed, 不猜时区)。

    转到 ``rules.name_snapshot_timezone`` 后, 当地时间早于
    ``rules.name_snapshot_attribution_cutoff_local`` 的快照不归属当天 (它可能
    还没反映当天的戴帽/摘帽) —— 否则归属当地日历日。

    与 ``availability_at`` (09:20, 消费方何时可用分区) 的关系: 两个独立参数,
    只是今天恰好同值 (见 stock_st_acquire.yaml name_snapshot 段注释)。
    """
    if isinstance(built_at, datetime):
        dt = built_at
    else:
        text = str(built_at or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    local = dt.astimezone(ZoneInfo(rules.name_snapshot_timezone))
    cutoff_h, cutoff_m = (int(part) for part in rules.name_snapshot_attribution_cutoff_local.split(":"))
    if (local.hour, local.minute) < (cutoff_h, cutoff_m):
        return None
    return local.date()


def derive_st_rows(
    name_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    trade_date: str,
    rules: StockStAcquireRules | None = None,
) -> list[dict[str, Any]]:
    """把 (ts_code, name) 快照行过滤/整形成名称路径的落地行 (含 ``st_origin``)。

    ``name_rows``: 形如 ``{"ts_code": "000010.SZ", "name": "*ST美丽"}`` 的可迭代
    对象 (来自 ``raw_tushare_stock_basic`` 或任何同形态的每日名称快照)。

    ``rules`` 默认现读 ``stock_st_acquire.yaml`` —— 传入自定义 rules 只影响
    ``type``/``type_name``/``st_origin`` 标签的取值 (C3: 参数无副本, 改配置就
    改派生行, 不需要改代码)。
    """
    day = str(trade_date or "").strip()
    if len(day) != 8 or not day.isdigit():
        raise StockSTDeriveError(f"trade_date 须为紧凑 8 位 YYYYMMDD, 收到 {trade_date!r}")
    active_rules = rules or load_stock_st_acquire_rules()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in name_rows or []:
        if not isinstance(item, dict):
            continue
        ts_code = str(item.get("ts_code") or "").strip().upper()
        name = item.get("name")
        if not ts_code or not name_flags_st(name):
            continue
        if ts_code in seen:  # grain=[ts_code, trade_date]; 防上游快照偶发重复行
            continue
        seen.add(ts_code)
        rows.append(
            {
                "ts_code": ts_code,
                "name": str(name),
                "trade_date": day,
                "type": active_rules.membership_type,
                "type_name": active_rules.membership_type_name,
                "st_origin": "derived_name_prefix",
            }
        )
    return rows


def _reservoir_st_rows(
    rows: Sequence[Any],
    *,
    trade_date: str,
    rules: StockStAcquireRules,
) -> list[dict[str, Any]]:
    """把水库 (已限定为该日含 isST 字段的) 行过滤/整形成水库路径的落地行。

    不看 tradestatus (停牌的 ST 仍是 ST, 与 tushare 语义一致——一只股票被 ST 是
    监管标签, 与它今天有没有成交是两件独立的事)。``name`` 结构性为 ``None``:
    baostock 这一路不给简称。
    """
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    isst_field = rules.reservoir_isst_field
    true_value = rules.reservoir_isst_true_value
    for row in rows:
        value = row.payload.get(isst_field)
        if value is None or str(value) != true_value:
            continue
        ts_code = str(row.ts_code).strip().upper()
        if not ts_code or ts_code in seen:
            continue
        seen.add(ts_code)
        members.append(
            {
                "ts_code": ts_code,
                "name": None,
                "trade_date": trade_date,
                "type": rules.membership_type,
                "type_name": rules.membership_type_name,
                "st_origin": "provider_baostock_isst",
            }
        )
    return members


def _parse_built_at_date(value: Any) -> datetime | None:
    """解析出 aware ``datetime`` (不在这里就地折成日期——归属日由
    :func:`snapshot_day_for` 统一算, 见该函数 docstring 的 F2 修法)。"""

    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _default_name_rows_provider() -> tuple[list[dict[str, Any]], datetime | None]:
    """默认名称快照来源: 只读连接 ``tushare_raw`` 库, 查
    ``stock_st_acquire.yaml`` 声明的 ``name_snapshot.table`` 全表 (不按 market
    过滤 —— 北交所记录本就完整在内)。

    返回 ``built_at`` 的**原始 aware datetime** (每行取 MAX), 不在这里折成日期
    —— 折算 (含时区转换与 cutoff 判断) 统一交给 :func:`snapshot_day_for`, 避免
    两处各算一次而漂移。
    """
    import duckdb

    from services.database_manifest import get_database_manifest

    table = load_stock_st_acquire_rules().name_snapshot_table
    raw_path = get_database_manifest().path_for("tushare_raw")
    conn = duckdb.connect(str(raw_path), read_only=True)  # rule-compliance: ok evidence=只读跨库读身份真相源 raw_tushare_stock_basic, 同 tdxhub._default_bj_codes_provider 读同一张表, 非业务阈值
    try:
        rows = conn.execute(
            f"SELECT ts_code, name, built_at FROM {table}"  # rule-compliance: ok evidence=read-identity-truth-source-for-st-name-derivation
        ).fetchall()
    finally:
        conn.close()
    name_rows = [{"ts_code": r[0], "name": r[1]} for r in rows if r and r[0]]
    built_dates = [d for d in (_parse_built_at_date(r[2]) for r in rows) if d is not None]
    max_built_at = max(built_dates) if built_dates else None
    return name_rows, max_built_at


class _DefaultReservoirReader:
    """自开自关只读连接的水库读取封装 (``fetch_raw`` 用) —— 与
    ``_default_name_rows_provider`` 同型只读连接, 不与同进程可能已开的写连接
    共存冲突 (每次调用即开即关)。"""

    def latest_rows_for_date(self, trade_date: str) -> list[Any]:
        from services.data_access.resolver import connect_ro
        from services.data_sources.baostock_daily_k_reservoir import (
            latest_rows_for_date,
        )

        conn = connect_ro("tushare_raw")
        try:
            return latest_rows_for_date(conn, trade_date)
        finally:
            conn.close()


def _rows_with_isst_field(rows: Iterable[Any], *, isst_field: str) -> list[Any]:
    return [r for r in rows if isst_field in str(r.fields_csv).split(",")]


class StockSTDeriveSource:
    """sync_runner 调用约定入口, 与 ``sources/calendar_rule.py::CalendarRuleSource``
    /``sources/baostock.py::BaostockSource`` 同型: ``fetch_raw(api, **params)``。
    """

    name = ALIAS

    def __init__(
        self,
        *,
        name_rows_provider: Callable[[], tuple[list[dict[str, Any]], Any]] | None = None,
        reservoir_reader: Any | None = None,
        required_codes_provider: Callable[[date], Iterable[str]] | None = None,
        rules: StockStAcquireRules | None = None,
        max_snapshot_age_days: int = 0,
    ) -> None:
        self._provider = name_rows_provider or _default_name_rows_provider
        self._reservoir_reader = reservoir_reader or _DefaultReservoirReader()
        self._rules = rules or load_stock_st_acquire_rules()
        self._max_age = int(max_snapshot_age_days)
        # B1 修法 (2026-09-19 返修): required(D) 覆盖判据的分母, fetch_raw 自开
        # 自关只读连接算 (与 reservoir_reader 同型); 可注入 (测试/自定义取数)。
        self._required_codes_provider = required_codes_provider or (
            lambda requested: _default_required_codes_for_date(requested, self._rules)
        )

    def _name_path_usable(self, requested: date, allow_stale: bool) -> tuple[bool, list[dict[str, Any]], datetime | None]:
        name_rows, built_at = self._provider()
        if not name_rows:
            raise StockSTDeriveError(
                f"{self._rules.name_snapshot_table} 空或不可读 —— 先同步 stock_basic 域 "
                "(services.data_sources.sync_runner --domain stock_basic), "
                "stock_st_derive 没有自己的名称取数能力, 依赖该域保鲜"
            )
        snapshot_date = snapshot_day_for(built_at, self._rules)
        if snapshot_date is None:
            return False, name_rows, built_at
        age = abs((requested - snapshot_date).days)
        usable = age <= self._max_age or allow_stale
        return usable, name_rows, built_at

    def fetch_raw(self, api: str, **params: Any) -> ProviderPage:
        allow_stale = bool(params.pop("allow_stale_snapshot", False))
        name = str(api or "").strip()
        if name != API_STOCK_ST:
            raise KeyError(f"stock_st_derive: unknown api {api!r} (known: {API_STOCK_ST!r})")
        trade_date = params.get("trade_date") or params.get("start_date")
        if not trade_date:
            raise StockSTDeriveError(
                "stock_st_derive 需要显式 trade_date (紧凑 8 位) —— 本 adapter 只能为"
                f"快照当天或水库已有 {self._rules.reservoir_isst_field} 观测的日期派生, "
                "不设隐式默认日期"
            )
        day = str(trade_date).strip()
        if len(day) != 8 or not day.isdigit():
            raise StockSTDeriveError(f"trade_date 须为紧凑 8 位 YYYYMMDD, 收到 {trade_date!r}")
        requested = date(int(day[:4]), int(day[4:6]), int(day[6:8]))

        usable, name_rows, built_at = self._name_path_usable(requested, allow_stale)

        if usable:
            member_rows = derive_st_rows(name_rows, trade_date=day, rules=self._rules)
            request_meta: dict[str, Any] = {
                "membership_path": "name_snapshot",
                "st_origin": "derived_name_prefix",
                "coverage_exchanges": sorted(self._rules.coverage_exchanges_for("derived_name_prefix")),
                "snapshot_built_at": built_at.isoformat() if isinstance(built_at, datetime) else None,
                "reservoir_rows_for_date": None,
                "reservoir_fetched_at_max": None,
            }
        else:
            reservoir_rows = self._reservoir_reader.latest_rows_for_date(day)
            isst_field = self._rules.reservoir_isst_field
            required = frozenset(self._required_codes_provider(requested))
            verdict = _evaluate_reservoir_coverage(reservoir_rows, required, isst_field=isst_field)
            if verdict.answerable:
                member_rows = _reservoir_st_rows(verdict.with_isst_rows, trade_date=day, rules=self._rules)
                fetched_at_max = max((r.fetched_at for r in verdict.with_isst_rows), default=None)
                request_meta = {
                    "membership_path": "baostock_reservoir",
                    "st_origin": "provider_baostock_isst",
                    "coverage_exchanges": sorted(self._rules.coverage_exchanges_for("provider_baostock_isst")),
                    "snapshot_built_at": None,
                    "reservoir_rows_for_date": len(verdict.with_isst_rows),
                    "reservoir_fetched_at_max": (
                        fetched_at_max.isoformat() if isinstance(fetched_at_max, datetime) else None
                    ),
                }
            else:
                # B1 修法: 可答判据从"有行即可"改成"覆盖 required(D)"——非快照日
                # 只要 required(D) 里有一个代码在水库该日没有含 isST 字段的观测行
                # (不论该值是不是 baostock 实际返回过, 只要字段缺席), 就不可答,
                # 不 accept 任何行 (原来的"有行就派生"会把停牌中的 ST 股漏成假阴)。
                raise StockSTUnanswerableError(
                    trade_date=day,
                    reason="reservoir_coverage_incomplete",
                    remedy=_unanswerable_remedy(
                        day, "reservoir_coverage_incomplete", isst_field=isst_field
                    ),
                    detail=_coverage_incomplete_detail(verdict.missing_codes),
                )

        limit = params.get("limit")
        offset = int(params.get("offset") or 0)
        rows = member_rows
        if limit is not None:
            rows = rows[offset : offset + int(limit)]
        elif offset:
            rows = rows[offset:]
        return ProviderPage(rows=rows, request_meta=request_meta)

    def answerable_dates(
        self, trade_dates: Sequence[str], *, conn: Any
    ) -> Mapping[str, DateAnswerability]:
        """同一套判定 (``_evaluate_reservoir_coverage`` + ``required_codes_for_date``,
        与 ``fetch_raw`` 共用——B1/B4 教训: 判定逻辑一处定义, 计划器与执行器不
        各写一份可能漂移的副本), 只读传入的 ``conn`` (计划器已开的只读连接) ——
        不落库、不触网、不另开水库连接 (避免 U5 那种读写连接同进程共存的疑虑)。"""
        from services.data_sources.baostock_daily_k_reservoir import latest_rows_for_date

        _name_rows, built_at = self._provider()
        snapshot_date = snapshot_day_for(built_at, self._rules)
        isst_field = self._rules.reservoir_isst_field

        result: dict[str, DateAnswerability] = {}
        for day in trade_dates:
            requested = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
            if snapshot_date is not None and abs((requested - snapshot_date).days) <= self._max_age:
                result[day] = DateAnswerability(True, "name_snapshot", None, None)
                continue
            required = required_codes_for_date(conn, requested, self._rules)
            reservoir_rows = latest_rows_for_date(conn, day)
            verdict = _evaluate_reservoir_coverage(reservoir_rows, required, isst_field=isst_field)
            if verdict.answerable:
                result[day] = DateAnswerability(True, "baostock_reservoir", None, None)
                continue
            reason = "reservoir_coverage_incomplete"
            result[day] = DateAnswerability(
                False, None, reason,
                _unanswerable_remedy(day, reason, isst_field=isst_field),
                _coverage_incomplete_detail(verdict.missing_codes),
            )
        return result


__all__ = [
    "ALIAS",
    "API_STOCK_ST",
    "DateAnswerability",
    "StockSTDeriveError",
    "StockSTDeriveSource",
    "StockSTUnanswerableError",
    "derive_st_rows",
    "name_flags_st",
    "required_codes_for_date",
    "snapshot_day_for",
]
