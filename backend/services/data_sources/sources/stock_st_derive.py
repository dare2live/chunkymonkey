"""ST 名称派生器 — 不取数的 source adapter, 形状照抄 ``calendar_rule.py``。

**背景 (2026-09-01)**: TuShare 2026-09-10 到期不续期, ``stock_st`` 域原源 tushare。
候选换源 baostock 已被其风控拉黑 (``login`` 返 ``10001011 黑名单用户``, 非本项目可控),
不能作为方案。

**第一性原理**: ST 的可观测形态就是**股票简称带 ``ST``/``*ST`` 前缀** —— 这是交易所自己
的展示规则 (《股票上市规则》风险警示), 不是某个供应商的私有标注。判断"今天谁是 ST"不需要
向供应商单独取一个 ST 接口, 只需要知道"今天每只股票叫什么名字"。

**它为什么"无网络无账号无限流" (与 calendar_rule 同型, 但机制不同)**: calendar_rule 的
真相源是国务院节假日公告(配置文件, 静态); 本 adapter 的真相源是**每日证券名称**,
这天然需要一个每日观测点, 无法像日历一样纯规则推导。但本项目已有独立域 ``stock_basic``
(2026-09-01 同批换源 tushare -> fuyao, 落地表名不变 = ``raw_tushare_stock_basic``,
``write_mode: replace_snapshot``) 每次同步都会把当前全市场证券名称原地刷新一份。
本 adapter **只读那张本地已同步表**, 自己不发一次网络请求、不占供应商账号、不吃供应商
限流 —— 网络依赖被 stock_basic 域独立承担 (与 calendar_rule 依赖人工维护的 YAML 同构:
两者都是"读一个别处已经保鲜好的本地真相源", 不是"零依赖")。

**实测校验 (2026-08-31/09-01, 见 backend/tests/services/test_stock_st_derive.py 复现)**:
- 召回: 把本模块 ``name_flags_st`` 套到 ``canonical_stock_st_daily`` 全部历史
  (2022-01-04~2026-08-28, 173,413 行, 600 只曾 ST 代码) 上, **0 miss**。未加固前的朴素
  正则 (``^(?:S)?\\*ST|^ST``, 取自 ``calendar_identity_recon.name_flags_st``) 在同一份
  历史上有 116 处 miss, 全部是两类被判定"合法 ST"却对不上前缀的形态: (a) 除权除息当日
  交易所在简称前叠加的 ``XD``/``XR``/``DR`` 标记 (与 ST 状态无关的临时装饰, 如
  ``XD*ST龙净``), (b) 遗留的股权分置改革未完成标记 ``S`` + 无星号 ``ST`` 组合
  ``SST`` (600182.SH ``SST佳通``, 2022-01-04~2022-02 期间 77 行, 该股是本项目窗口内
  唯一样本但历史确有此形态)。本模块的正则在剥离 XD/XR/DR 前缀后按
  ``S?\\*?ST`` 匹配, 两类形态全部转正。
- 精度: 同一正则套到 2026-08-31 全市场快照 (``raw_tushare_stock_basic``, 5563 行,
  与扶摇 ``meta-tickers-list`` 同日单点对账 0 差异) 上, 0 个假阳性 (未查到任何非 ST
  证券名称意外匹配)。
- 与 accepted 表 (``canonical_stock_st_daily`` 最新分区 2026-08-28) 对账: 205 只 accepted
  中 203 只当场匹配, 差的 2 只 (000711.SZ ST京蓝 / 002586.SZ ST围海) 经 ifind
  戴帽摘帽事件查证均为 **2026-08-31 当天摘帽** —— 即 accepted 表落后 (on_demand 手工
  发布, 上一分区停在 2026-08-28 周五), 名称派生反而是**当天生效、零延迟**, 不是
  派生方法的缺陷。
- 边界: 北交所 (BJ) 历史曾出现 4 只 ST 代码, ``raw_tushare_stock_basic`` 不做
  market 过滤 (与 dim_active_a_stock 不同), 北交所记录完整在内, 本模块无需额外处理;
  B 股 (900/200 前缀) 历史 0 只曾 ST, 未见需要专门处理的样本; 退市整理期
  ("退市XX" 前缀) **不在本域历史范围内** —— ``canonical_stock_st_daily`` 4.7 年
  173,413 行 ``type`` 恒为 ``'ST'``, 从未出现过其它 type, 说明这本就是 tushare
  stock_st 接口自己的范围边界, 换源不扩大也不收窄它。

**与原 tushare 域的根本差异 (必须传导给消费方, 见类 docstring)**: 原域 grain 是
``[ts_code, trade_date]`` 且可对**任意历史日期**发起手工单日请求 (``sync_policy: on_demand``
但历史可回填)。本 adapter 只能对**当前 stock_basic 快照所代表的那一天**派生 —— 因为
``raw_tushare_stock_basic`` 是 ``replace_snapshot``, 覆盖后**丢失所有历史名称**, 物理上
无法重建任意历史日的名称面貌。这不是本 adapter 的实现缺陷, 是"名称快照"这个真相源本身的
能力边界。fail-closed: 请求的 ``trade_date`` 与快照 ``built_at`` 的日历日差超过
``max_snapshot_age_days`` (默认 0, 即必须同一天) 就拒绝, 不静默拿旧快照冒充新日期的 ST 名单
(2026-08-31 的 000711/002586 案例正说明这类漂移足以致错)。

**历史数据处置 (裁决产出, 不由本 adapter 执行)**: ``canonical_stock_st_daily`` 现有
2022-01-04~2026-08-28 的 tushare 时代历史 **原样保留**, 无法且不需要用名称快照重建
(重建需要每一天的历史名称, 该表本身就是当时唯一的历史真相源)。换源只影响**这之后**的
新分区; 接线 (何时切/legacy 表名/target_table) 由主线决定, 本模块不做任何假设。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable

ALIAS = "stock_st_derive"
API_STOCK_ST = "stock_st"  # 与原 sync_registry `api: stock_st` 同名, 换源只需改 `source:`

_ST_TYPE = "ST"
_ST_TYPE_NAME = "风险警示板"
_NAME_TABLE = "raw_tushare_stock_basic"

# 剥离除权除息临时前缀 (XD/XR/DR, 可叠加多次理论上不会但防御性用 * 而非 ?) 后,
# 按 "可选 S(股改未完成遗留) + 可选 * + ST" 匹配。见类 docstring 实测: 173,413 行
# 历史 0 miss, 5563 行今日快照 0 假阳性。
_ST_NAME_RE = re.compile(r"^(?:XD|XR|DR)*S?\*?ST", re.IGNORECASE)


class StockSTDeriveError(RuntimeError):
    """配置/输入非法, 或快照陈旧到不能安全派生。"""


class StockSTStaleSnapshotError(StockSTDeriveError):
    """请求的 trade_date 与 stock_basic 快照的 built_at 日历日对不上 —— fail-closed。

    为什么不静默放行: ``canonical_stock_st_daily`` 是 "PIT universe 过滤真相源"
    (sync_registry.yaml stock_st 域注释), ST 批腰斩/漂移 = 禁入股漏进 universe,
    与红线相邻。2026-08-31 实测样本 (000711.SZ / 002586.SZ 当天摘帽) 证明:
    哪怕只差 3 个日历日 (跨一个周末), 名称快照就足以把"已摘帽"错判成"仍是 ST"
    的反方向 —— 拿旧快照冒充新日期一样会静默产错方向的名单。
    """


def name_flags_st(name: Any) -> bool:
    """当前证券简称是否带 ST/*ST 风险警示前缀 (含除权除息装饰前缀剥离)。"""
    text = str(name or "").strip().replace(" ", "")
    return bool(_ST_NAME_RE.match(text))


def derive_st_rows(
    name_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    trade_date: str,
) -> list[dict[str, Any]]:
    """把 (ts_code, name) 快照行过滤/整形成与原 tushare ``stock_st`` 落地形态一致的行。

    ``name_rows``: 形如 ``{"ts_code": "000010.SZ", "name": "*ST美丽"}`` 的可迭代对象
    (来自 ``raw_tushare_stock_basic`` 或任何同形态的每日名称快照)。

    返回行形态与 ``landing_tushare_stock_st.payload_json`` 现有字段严格一致:
    ``ts_code`` / ``name`` / ``trade_date`` / ``type`` / ``type_name`` —— sync_runner
    的落地 writer 按行内 key 自动建列, 字段名/个数必须与既有 accepted 形态对齐,
    不多不少。
    """
    day = str(trade_date or "").strip()
    if len(day) != 8 or not day.isdigit():
        raise StockSTDeriveError(f"trade_date 须为紧凑 8 位 YYYYMMDD, 收到 {trade_date!r}")
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
                "type": _ST_TYPE,
                "type_name": _ST_TYPE_NAME,
            }
        )
    return rows


def _parse_built_at_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.date()


def _default_name_rows_provider() -> tuple[list[dict[str, Any]], date | None]:
    """默认名称快照来源: 只读连接 ``tushare_raw`` 库, 查 ``raw_tushare_stock_basic``
    全表 (不按 market 过滤 —— 北交所记录本就完整在内, 与
    ``sources/tdxhub.py::_default_bj_codes_provider`` 同型只读连接, 同一张身份真相源表)。

    只管"正常路径怎么查", 不吞异常 (表不存在/库连不上原样往外抛); 是否把异常当
    fail-closed 由调用方 (``StockSTDeriveSource.fetch_raw``) 决定, 便于测试直接
    monkeypatch 掉整个 provider。
    """
    import duckdb

    from services.database_manifest import get_database_manifest

    raw_path = get_database_manifest().path_for("tushare_raw")
    conn = duckdb.connect(str(raw_path), read_only=True)  # rule-compliance: ok evidence=只读跨库读身份真相源 raw_tushare_stock_basic, 与 security_master.py/tdxhub.py 同型只读连接, 非业务阈值
    try:
        rows = conn.execute(
            f"SELECT ts_code, name, built_at FROM {_NAME_TABLE}"  # rule-compliance: ok evidence=read-identity-truth-source-for-st-name-derivation, 同 tdxhub._default_bj_codes_provider 读同一张表
        ).fetchall()
    finally:
        conn.close()
    name_rows = [{"ts_code": r[0], "name": r[1]} for r in rows if r and r[0]]
    built_dates = {d for d in (_parse_built_at_date(r[2]) for r in rows) if d is not None}
    snapshot_date = max(built_dates) if built_dates else None
    return name_rows, snapshot_date


class StockSTDeriveSource:
    """sync_runner 调用约定入口, 与 ``sources/calendar_rule.py::CalendarRuleSource``
    /``sources/baostock.py::BaostockSource`` 同型: ``fetch_raw(api, **params)``。
    """

    name = ALIAS

    def __init__(
        self,
        *,
        name_rows_provider: Callable[[], tuple[list[dict[str, Any]], date | None]] | None = None,
        max_snapshot_age_days: int = 0,
    ) -> None:
        self._provider = name_rows_provider or _default_name_rows_provider
        self._max_age = int(max_snapshot_age_days)

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        allow_stale = bool(params.pop("allow_stale_snapshot", False))
        name = str(api or "").strip()
        if name != API_STOCK_ST:
            raise KeyError(f"stock_st_derive: unknown api {api!r} (known: {API_STOCK_ST!r})")
        trade_date = params.get("trade_date") or params.get("start_date")
        if not trade_date:
            raise StockSTDeriveError(
                "stock_st_derive 需要显式 trade_date (紧凑 8 位) —— 本 adapter 只能为"
                "当前 stock_basic 快照所代表的那一天派生, 不设隐式默认日期"
            )
        day = str(trade_date).strip()
        if len(day) != 8 or not day.isdigit():
            raise StockSTDeriveError(f"trade_date 须为紧凑 8 位 YYYYMMDD, 收到 {trade_date!r}")
        requested = date(int(day[:4]), int(day[4:6]), int(day[6:8]))

        name_rows, snapshot_date = self._provider()
        if not name_rows:
            raise StockSTDeriveError(
                f"{_NAME_TABLE} 空或不可读 —— 先同步 stock_basic 域 "
                "(services.data_sources.sync_runner --domain stock_basic), "
                "stock_st_derive 没有自己的名称取数能力, 依赖该域保鲜"
            )
        if snapshot_date is None:
            raise StockSTDeriveError(
                f"{_NAME_TABLE} 缺可解析的 built_at, 无法判定快照新鲜度 —— fail-closed"
            )
        age = abs((requested - snapshot_date).days)
        if age > self._max_age and not allow_stale:
            raise StockSTStaleSnapshotError(
                f"stock_st_derive: 请求 trade_date={day} 但 {_NAME_TABLE} 快照 built_at "
                f"日期={snapshot_date.isoformat()}, 相差 {age} 个日历日 (阈值 "
                f"{self._max_age}) —— 名称快照是 replace_snapshot, 不携带历史, 用旧/新"
                "快照冒充别的日期会静默产出错误的 ST 名单 (2026-08-31 000711.SZ/"
                "002586.SZ 摘帽当天验证过这个方向的错)。先重新同步 stock_basic 使其"
                "built_at 对齐目标交易日; 明确只要近似值才显式传 "
                "allow_stale_snapshot=True。"
            )

        limit = params.get("limit")
        offset = int(params.get("offset") or 0)
        rows = derive_st_rows(name_rows, trade_date=day)
        if limit is not None:
            rows = rows[offset : offset + int(limit)]
        elif offset:
            rows = rows[offset:]
        return rows


__all__ = [
    "ALIAS",
    "API_STOCK_ST",
    "StockSTDeriveError",
    "StockSTDeriveSource",
    "StockSTStaleSnapshotError",
    "derive_st_rows",
    "name_flags_st",
]
