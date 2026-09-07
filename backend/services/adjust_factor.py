"""adjust_factor — 自算复权因子（后复权落库 + 前复权现算 + 双重验证）。

算法（baostock 官方《复权因子简介》"涨跌幅复权法"逐字一致；DolphinDB 官方教程收录同一算法）::

    ratio[t] = close[t-1] / pre_close[t]      无除权时 = 1
    f[t]     = ∏ ratio                        累乘, 首日 = 1
    hfq[t]   = nominal[t] × f[t]               后复权: 只依赖 t 及更早, PIT 干净
    qfq[t]   = nominal[t] × f[t] / f[latest]   前复权: f[latest] 是未来值 -> 有未来函数

不读分红表。``pre_close`` 不是"昨天收盘价"，是交易所按官方除权除息公式已经算好的结果::

    除权(息)参考价 = [(前收盘价 − 每股现金红利) + 配股价 × 配股比例] / (1 + 送股率 + 转增率 + 配股率)

现金分红、送股、转增、配股全部已经在这一条公式里一次算完，没有先后顺序问题——所以不需要读
分红表、不需要拆三类事件、不需要判税前税后。``raw_tushare_dividend`` 在本模块里只在
``reconcile_vs_tushare`` 里当**诊断辅助**用（帮忙解释"为什么跟 tushare 对不上"），从不参与
``ratio``/``hfq_factor`` 本身的计算——那会重新引入本算法本来要避免的复杂度。

已实测（2026-09-07, 剔除北交所, 全市场 855 万行 / 5447 只股，见
``backend/config/adjust_factor.yaml`` 头部注释与本文件 ``pct_chg_self_check`` /
``reconcile_vs_tushare`` docstring）:

- vs ``raw_tushare_adj_factor``（两者各自归一到首个共同交易日=1 后比相对差）：
  中位 ~5.7e-05、p99 ~5.8e-04（量级 = pre_close 两位小数舍入精度）。
- vs ``canonical_nominal_ohlcv_daily.pct_chg``（完全不依赖任何复权因子的独立自检）：
  中位残差 2.4e-05 个百分点，p99 4.95e-05，仅 1 行残差达 0.065 个百分点（603005.SH
  2020-03-18 — 查明是 vendor 自己的 pct_chg 字段与它自己的 close/pre_close 不一致，
  不是本模块 bug；见 ``pct_chg_self_check`` 文档）。
- 与 tushare adj_factor 相对差 > 1% 的有 19 只股（当次实测数字；不写死名单，见
  ``reconcile_vs_tushare`` —— 分类成员是数据不是 YAML，会随 tushare 更新其 adj_factor 过期）。

设计取舍（先问"根因能不能修"再决定要不要加处理）:

1. **停牌跨越除权日导致复牌日 pre_close 漏调整**：这是源表
   （``canonical_nominal_ohlcv_daily``）的数据质量问题，不是本模块的算法问题——本模块忠实
   按 ``pre_close`` 计算，源表漏调整会被忠实保留（不会替它"纠正"，那是在编造数据）。
   ``reconcile_vs_tushare`` 用来发现这类日子（残差会显著偏离舍入噪声量级）。
2. **前复权是未来函数**（业界共识，米筐"动态复权"即为此；项目红线 1: 未来数据不进状态/
   分桶维度）——本模块只提供现算函数 ``qfq_sql``/``fetch_qfq``，不落表；落表意味着每次新
   分红都要重写全表历史，等于重新实现 ``build_price_kline_qfq_tushare.py`` 正在做（且已
   被裁决要退役）的模式，见该脚本与 ``tushare_sunset.yaml``（本模块不碰这两者）。
3. **缺失传播为缺失**：``close``/``pre_close`` 任一行为 NULL -> 该行 ``ratio`` = NULL ->
   该股从此往后所有 ``hfq_factor`` = NULL（累乘链条一环 unknown，后面继承 unknown；不做
   latest fallback、不假定"后面肯定没事"）。当前 ``canonical_nominal_ohlcv_daily`` 全列
   NOT NULL（schema 声明），这条路径当前不会被触发，但必须存在，防止未来源表放松约束时
   静默算错。
4. **ratio 超出合理区间**（见配置 ``ratio_bounds``）同样判 unknown、同样 poison 向后传播——
   一个荒谬的 ratio（例如源数据 pre_close 异常导致比例爆炸）如果被悄悄接受，会把错误永久
   固化进这只股此后的每一天。

Universe：不新写一份规则，直接复用 ``services.universe.sql_where_active_a_share``
（board_prefixes 60/00/30/68，排除北交所/新三板/B股/ETF 等）。

owner: 本文件 + ``backend/config/adjust_factor.yaml``。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import yaml

from services.universe import sql_where_active_a_share

__all__ = [
    "AdjustFactorConfig",
    "AdjustFactorConfigError",
    "load_config",
    "TABLE",
    "DDL",
    "RATIO_STATUS_VALUES",
    "DEFAULT_SOURCE_RELATION",
    "DEFAULT_ADJ_FACTOR_RELATION",
    "DEFAULT_DIVIDEND_RELATION",
    "hfq_sql",
    "qfq_sql",
    "fetch_hfq",
    "fetch_qfq",
    "rebuild_all",
    "build_latest",
    "PctChgSelfCheckReport",
    "pct_chg_self_check",
    "TushareDivergence",
    "TushareReconciliationReport",
    "reconcile_vs_tushare",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "backend" / "config" / "adjust_factor.yaml"

TABLE = "fact_adjust_factor_hfq_daily"
RATIO_STATUS_VALUES = frozenset({"first_day", "no_event", "adjusted", "missing_input", "out_of_bounds"})

DEFAULT_SOURCE_RELATION = "canonical_nominal_ohlcv_daily"
DEFAULT_ADJ_FACTOR_RELATION = "raw_tushare_adj_factor"
DEFAULT_DIVIDEND_RELATION = "raw_tushare_dividend"

# 只追加：hfq[t] 只依赖 t 及更早的数据，一旦某行写入就永远不需要因为"以后又来了新数据"而
# 改写（qfq 才有那个问题——qfq 依赖 latest，latest 一变全表就要重算）。所以这张表的 writer
# 只允许 INSERT，不允许 UPDATE/DELETE（rebuild_all 的 DELETE 是"重新生成同一份结果"，不是
# "改写已发布的历史结论"，二者语义不同：见下方 rebuild_all 文档）。
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    ts_code       VARCHAR   NOT NULL,
    trade_date    DATE      NOT NULL,
    ratio         DOUBLE,                 -- NULL = unknown（缺失/越界，poison 向后传播）
    ratio_status  VARCHAR   NOT NULL,     -- first_day | no_event | adjusted | missing_input | out_of_bounds
    hfq_factor    DOUBLE,                 -- NULL = 该股此日起链条已 unknown
    built_at      TIMESTAMP NOT NULL,
    config_hash   VARCHAR   NOT NULL,
    PRIMARY KEY (ts_code, trade_date)
)
"""


class AdjustFactorConfigError(ValueError):
    """Config 缺键 / 未知键 / 类型不对 / 悬空引用 -> fail closed，不做默认值兜底。"""


@dataclass(frozen=True)
class AdjustFactorConfig:
    """typed 快照；由 :func:`load_config` 产出，不接受直接构造。"""

    version: int
    ex_rights_threshold: float
    ratio_min: float
    ratio_max: float
    start_policy_mode: str
    listing_date_source: str
    listing_date_populated: bool
    universe_reuse_config: str
    universe_reuse_field: str
    source_db_alias: str
    source_nominal_table: str
    reconciliation_enabled: bool
    reconciliation_alert_threshold: float
    reconciliation_authority: str
    reconciliation_unresolved_disposition: str
    config_hash: str


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdjustFactorConfigError(f"{field} must be a mapping, got {type(value).__name__}")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise AdjustFactorConfigError(f"{field} missing keys: {missing}")
    if unknown:
        raise AdjustFactorConfigError(f"{field} unknown keys: {unknown}")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdjustFactorConfigError(f"{field} must be a number, got {type(value).__name__}")
    return float(value)


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AdjustFactorConfigError(f"{field} must be a bool, got {type(value).__name__}")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdjustFactorConfigError(f"{field} must be a non-empty string")
    return value


def load_config(path: Path | str | None = None) -> AdjustFactorConfig:
    """Load one strict config snapshot. Missing/unknown keys, bad types, or a dangling
    ``universe.reuse_config`` reference all fail closed (raise), never silently default.
    """
    p = Path(path) if path is not None else _CONFIG_PATH
    raw = _mapping(yaml.safe_load(p.read_text(encoding="utf-8")), "root")
    _exact_keys(
        raw,
        {
            "version",
            "ex_rights_threshold",
            "ratio_bounds",
            "start_policy",
            "universe",
            "source",
            "tushare_reconciliation",
        },
        "root",
    )

    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise AdjustFactorConfigError("version must be a positive int")

    ex_rights_threshold = _number(raw["ex_rights_threshold"], "ex_rights_threshold")
    if ex_rights_threshold < 0:
        raise AdjustFactorConfigError("ex_rights_threshold must be >= 0")

    bounds = _mapping(raw["ratio_bounds"], "ratio_bounds")
    _exact_keys(bounds, {"min", "max"}, "ratio_bounds")
    ratio_min = _number(bounds["min"], "ratio_bounds.min")
    ratio_max = _number(bounds["max"], "ratio_bounds.max")
    if not (0 < ratio_min < 1 <= ratio_max):
        raise AdjustFactorConfigError("ratio_bounds must satisfy 0 < min < 1 <= max")

    start = _mapping(raw["start_policy"], "start_policy")
    _exact_keys(start, {"mode", "listing_date_source", "listing_date_populated"}, "start_policy")
    start_mode = _text(start["mode"], "start_policy.mode")
    if start_mode != "data_window_first_row":
        raise AdjustFactorConfigError(f"unsupported start_policy.mode: {start_mode!r}")
    listing_date_source = _text(start["listing_date_source"], "start_policy.listing_date_source")
    listing_date_populated = _bool(start["listing_date_populated"], "start_policy.listing_date_populated")

    universe = _mapping(raw["universe"], "universe")
    _exact_keys(universe, {"reuse_config", "reuse_field"}, "universe")
    universe_reuse_config = _text(universe["reuse_config"], "universe.reuse_config")
    universe_reuse_field = _text(universe["reuse_field"], "universe.reuse_field")
    if not (_REPO_ROOT / universe_reuse_config).is_file():
        raise AdjustFactorConfigError(
            f"universe.reuse_config dangling reference: {universe_reuse_config!r} does not exist "
            "(rule 11: 悬空引用 fail-closed)"
        )

    source = _mapping(raw["source"], "source")
    _exact_keys(source, {"db_alias", "nominal_table"}, "source")
    source_db_alias = _text(source["db_alias"], "source.db_alias")
    source_nominal_table = _text(source["nominal_table"], "source.nominal_table")

    recon = _mapping(raw["tushare_reconciliation"], "tushare_reconciliation")
    _exact_keys(
        recon,
        {"enabled", "relative_diff_alert_threshold", "authority", "unresolved_disposition"},
        "tushare_reconciliation",
    )
    reconciliation_enabled = _bool(recon["enabled"], "tushare_reconciliation.enabled")
    reconciliation_alert_threshold = _number(
        recon["relative_diff_alert_threshold"], "tushare_reconciliation.relative_diff_alert_threshold"
    )
    if reconciliation_alert_threshold <= 0:
        raise AdjustFactorConfigError("tushare_reconciliation.relative_diff_alert_threshold must be > 0")
    reconciliation_authority = _text(recon["authority"], "tushare_reconciliation.authority")
    if reconciliation_authority != "self_computed_from_pre_close":
        raise AdjustFactorConfigError(f"unsupported tushare_reconciliation.authority: {reconciliation_authority!r}")
    reconciliation_unresolved_disposition = _text(
        recon["unresolved_disposition"], "tushare_reconciliation.unresolved_disposition"
    )

    payload = {
        "version": version,
        "ex_rights_threshold": ex_rights_threshold,
        "ratio_bounds": {"min": ratio_min, "max": ratio_max},
        "start_policy": {
            "mode": start_mode,
            "listing_date_source": listing_date_source,
            "listing_date_populated": listing_date_populated,
        },
        "universe": {"reuse_config": universe_reuse_config, "reuse_field": universe_reuse_field},
        "source": {"db_alias": source_db_alias, "nominal_table": source_nominal_table},
        "tushare_reconciliation": {
            "enabled": reconciliation_enabled,
            "relative_diff_alert_threshold": reconciliation_alert_threshold,
            "authority": reconciliation_authority,
            "unresolved_disposition": reconciliation_unresolved_disposition,
        },
    }
    config_hash = sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return AdjustFactorConfig(
        version=version,
        ex_rights_threshold=ex_rights_threshold,
        ratio_min=ratio_min,
        ratio_max=ratio_max,
        start_policy_mode=start_mode,
        listing_date_source=listing_date_source,
        listing_date_populated=listing_date_populated,
        universe_reuse_config=universe_reuse_config,
        universe_reuse_field=universe_reuse_field,
        source_db_alias=source_db_alias,
        source_nominal_table=source_nominal_table,
        reconciliation_enabled=reconciliation_enabled,
        reconciliation_alert_threshold=reconciliation_alert_threshold,
        reconciliation_authority=reconciliation_authority,
        reconciliation_unresolved_disposition=reconciliation_unresolved_disposition,
        config_hash=config_hash,
    )


# --------------------------------------------------------------------------- SQL builders


def _ratio_cte_sql(cfg: AdjustFactorConfig, source_relation: str) -> str:
    """Returns a comma-separated CTE chain (no leading ``WITH``, no trailing bare
    ``SELECT``) ending in a named CTE ``ratio_calc`` with columns
    ``ts_code, trade_date, close, pre_close, pct_chg, ratio, ratio_status``.
    Callers splice this into their own ``WITH`` clause, e.g.
    ``f"WITH {_ratio_cte_sql(cfg, rel)}, next_cte AS (SELECT * FROM ratio_calc ...) ..."``.

    Universe filter reused from ``services.universe`` (not re-declared here — see
    module docstring point on universe).
    """
    where = sql_where_active_a_share("ts_code")
    thr = cfg.ex_rights_threshold
    lo, hi = cfg.ratio_min, cfg.ratio_max
    return f"""
    lagged AS (
        SELECT ts_code, trade_date, close, pre_close, pct_chg,
               LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
        FROM {source_relation}
        WHERE {where}
    ),
    scored AS (
        SELECT *,
            CASE
                WHEN prev_close IS NULL THEN 1.0
                WHEN pre_close IS NULL OR close IS NULL THEN NULL
                WHEN abs(pre_close - prev_close) <= {thr} THEN 1.0
                ELSE prev_close / pre_close
            END AS raw_ratio
        FROM lagged
    ),
    ratio_calc AS (
        SELECT
            ts_code, trade_date, close, pre_close, pct_chg,
            CASE
                WHEN raw_ratio IS NOT NULL AND (raw_ratio < {lo} OR raw_ratio > {hi}) THEN NULL
                ELSE raw_ratio
            END AS ratio,
            CASE
                WHEN prev_close IS NULL THEN 'first_day'
                WHEN pre_close IS NULL OR close IS NULL THEN 'missing_input'
                WHEN raw_ratio IS NOT NULL AND (raw_ratio < {lo} OR raw_ratio > {hi}) THEN 'out_of_bounds'
                WHEN abs(pre_close - prev_close) <= {thr} THEN 'no_event'
                ELSE 'adjusted'
            END AS ratio_status
        FROM scored
    )
    """


def hfq_sql(cfg: AdjustFactorConfig | None = None, source_relation: str = DEFAULT_SOURCE_RELATION) -> str:
    """Full SELECT (not wrapped) producing the 后复权 series. Columns:
    ``ts_code, trade_date, close, pre_close, pct_chg, ratio, ratio_status, hfq_factor, hfq_close``.

    ``hfq_factor``/``hfq_close`` are NULL for every row from (and including) the first
    ``ratio IS NULL`` row onward for that ``ts_code`` — missing propagates as missing,
    it never resets even if a later row's own ratio would compute cleanly.
    """
    cfg = cfg or load_config()
    ratio_cte = _ratio_cte_sql(cfg, source_relation)
    return f"""
    WITH {ratio_cte},
    factor_calc AS (
        SELECT *,
            MAX(CASE WHEN ratio IS NULL THEN 1 ELSE 0 END) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS null_seen,
            SUM(LN(COALESCE(ratio, 1.0))) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS log_cumsum
        FROM ratio_calc
    )
    SELECT
        ts_code, trade_date, close, pre_close, pct_chg, ratio, ratio_status,
        CASE WHEN null_seen = 1 THEN NULL ELSE EXP(log_cumsum) END AS hfq_factor,
        CASE WHEN null_seen = 1 THEN NULL ELSE close * EXP(log_cumsum) END AS hfq_close
    FROM factor_calc
    """


def qfq_sql(cfg: AdjustFactorConfig | None = None, source_relation: str = DEFAULT_SOURCE_RELATION) -> str:
    """前复权 —— 现算视图, 不落表 (f[latest] 是未来值; 项目红线 1: 未来数据不进状态/分桶维度).

    调用方每次查询都会拿到"以查询当天为基准"的前复权序列；两次不同日期的查询对同一历史行
    给出不同的 ``qfq_close`` 是这个定义本身的性质，不是 bug。任何要写死/落库/进状态或分桶
    维度的消费方必须改用 ``hfq_sql``。
    """
    cfg = cfg or load_config()
    hfq = hfq_sql(cfg, source_relation)
    return f"""
    WITH hfq AS (
        {hfq}
    ),
    latest AS (
        SELECT ts_code, hfq_factor AS latest_factor
        FROM hfq
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1
    )
    SELECT
        h.ts_code, h.trade_date, h.close, h.hfq_factor, h.ratio_status,
        CASE
            WHEN l.latest_factor IS NULL OR l.latest_factor = 0 THEN NULL
            ELSE h.close * h.hfq_factor / l.latest_factor
        END AS qfq_close
    FROM hfq h
    JOIN latest l USING (ts_code)
    """


def fetch_hfq(con: Any, cfg: AdjustFactorConfig | None = None, source_relation: str = DEFAULT_SOURCE_RELATION):
    """Convenience wrapper: run :func:`hfq_sql` and return ``fetchall()``."""
    cfg = cfg or load_config()
    return con.execute(hfq_sql(cfg, source_relation)).fetchall()


def fetch_qfq(con: Any, cfg: AdjustFactorConfig | None = None, source_relation: str = DEFAULT_SOURCE_RELATION):
    """Convenience wrapper: run :func:`qfq_sql` and return ``fetchall()``."""
    cfg = cfg or load_config()
    return con.execute(qfq_sql(cfg, source_relation)).fetchall()


# --------------------------------------------------------------------------- persistence (append-only)


def rebuild_all(
    con: Any,
    *,
    target_table: str = TABLE,
    source_relation: str = DEFAULT_SOURCE_RELATION,
    cfg: AdjustFactorConfig | None = None,
) -> int:
    """全量重算并写入 ``target_table``。

    这不是"改写已发布历史"的语义（qfq 那种反例）：``hfq[t]`` 只依赖 ``t`` 及更早的数据，
    重跑对每一行给出的结果和上一次跑出来的逐行相同（除非源数据本身变了，那正是应该反映的）。
    这里的 DELETE+INSERT 只是"重新生成同一份结论"的实现手段，不是业务语义上的改写。
    生产环境常规增量应改用 :func:`build_latest`（真正只 INSERT，不 DELETE）。
    """
    cfg = cfg or load_config()
    con.execute(DDL)
    con.execute(f"DELETE FROM {target_table}")
    sql = hfq_sql(cfg, source_relation)
    con.execute(
        f"""
        INSERT INTO {target_table} (ts_code, trade_date, ratio, ratio_status, hfq_factor, built_at, config_hash)
        SELECT ts_code, trade_date, ratio, ratio_status, hfq_factor, CURRENT_TIMESTAMP, ?
        FROM ({sql})
        """,
        [cfg.config_hash],
    )
    return int(con.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0])


def build_latest(
    con: Any,
    *,
    target_table: str = TABLE,
    source_relation: str = DEFAULT_SOURCE_RELATION,
    cfg: AdjustFactorConfig | None = None,
) -> int:
    """增量追加：只 INSERT 每只股在 ``target_table`` 里尚未落库的新交易日，从不 UPDATE/DELETE
    已经写入的历史行——"历史值不变、只追加"字面上的实现。

    已知的计算成本权衡：本函数每次调用都会在内存里重算一遍全市场完整历史链条（因为
    ``hfq_factor`` 是从第一天开始的累乘，要算出"新的一天"必须知道链条到那天为止的完整状态），
    但只把每只股 **frontier 之后** 的新行写回表里——已经落库的行永远不会被本函数触碰或改写。
    这是数据语义上的正确性保证（不是计算复杂度上的优化）；若未来需要把全表重扫的成本降下来，
    应改为把每只股的 ``(log_cumsum, poisoned)`` 状态也持久化，而不是放松"不改写历史行"这条。

    返回本次实际新增的行数。
    """
    cfg = cfg or load_config()
    con.execute(DDL)

    frontier = con.execute(f"SELECT ts_code, MAX(trade_date) AS last_date FROM {target_table} GROUP BY ts_code").fetchall()
    frontier_map = {row[0]: row[1] for row in frontier}

    full_sql = hfq_sql(cfg, source_relation)
    rows = con.execute(
        f"SELECT ts_code, trade_date, ratio, ratio_status, hfq_factor FROM ({full_sql}) ORDER BY ts_code, trade_date"
    ).fetchall()

    to_insert = [
        (ts_code, trade_date, ratio, ratio_status, hfq_factor, cfg.config_hash)
        for ts_code, trade_date, ratio, ratio_status, hfq_factor in rows
        if frontier_map.get(ts_code) is None or trade_date > frontier_map[ts_code]
    ]
    if to_insert:
        con.executemany(
            f"""
            INSERT INTO {target_table} (ts_code, trade_date, ratio, ratio_status, hfq_factor, built_at, config_hash)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """,
            to_insert,
        )
    return len(to_insert)


# --------------------------------------------------------------------------- validation #1: pct_chg self-check


@dataclass(frozen=True)
class PctChgSelfCheckReport:
    n_rows: int
    median_abs_resid_pp: float | None
    p90_abs_resid_pp: float | None
    p99_abs_resid_pp: float | None
    max_abs_resid_pp: float | None
    n_over_alert_pp: int
    alert_threshold_pp: float
    worst_rows: tuple[dict[str, Any], ...]


def pct_chg_self_check(
    con: Any,
    cfg: AdjustFactorConfig | None = None,
    *,
    source_relation: str = DEFAULT_SOURCE_RELATION,
    alert_threshold_pp: float = 0.01,
    worst_n: int = 20,
) -> PctChgSelfCheckReport:
    """完全不依赖任何复权因子（不读 tushare adj_factor、不读自己的 hfq_factor 之外任何东西）
    的独立验证：把自算后复权价的逐日收益率，跟 ``canonical_nominal_ohlcv_daily.pct_chg``
    （交易所口径当日涨跌幅，分母是当日 pre_close）对比。

    数学上要诚实交代这条检验证明的是什么、不证明什么：

        hfq[t] / hfq[t-1] = (nominal[t] * f[t]) / (nominal[t-1] * f[t-1])
                          = nominal[t] * ratio[t] / nominal[t-1]
                          = nominal[t] * (nominal[t-1] / pre_close[t]) / nominal[t-1]
                          = nominal[t] / pre_close[t]
                          = close[t] / pre_close[t]
                          = pct_chg[t] / 100 + 1

    这是一个恒等式——只要 ``ratio`` 的实现没有 bug（没有错位、没有重复行、没有跨股串号、
    没有累乘顺序错误），左右两边**必然**在浮点精度内相等，与 ``pre_close`` 是否真的正确
    反映了实际除权除息**无关**（因为等式两边用的是同一个 ``close``/``pre_close``）。
    所以这条检验证明的是"实现没有 bug"，不是"复权在经济含义上是对的"——后者永远要回到
    ``pre_close`` 本身是否权威（这是本模块的算法前提，来自交易所公式，见模块 docstring）。

    实测价值：855 万行全市场里，恒等式在浮点精度内成立的比例是 8552883/8552884，唯一的
    例外（603005.SH 2020-03-18，残差 0.065 个百分点）经查是 vendor 自己的 ``pct_chg`` 字段
    与它自己的 ``close``/``pre_close`` 不一致（``(86.8-91.89)/91.89*100 = -5.5392``，
    与 vendor 记录的 ``pct_chg = -5.4739`` 对不上），不是本模块的实现 bug——这正是这条检验
    真正能抓到的问题类型：源数据内部自相矛盾、重复主键、非单调日期等会打破恒等式的情形。
    """
    cfg = cfg or load_config()
    hfq = hfq_sql(cfg, source_relation)
    sql = f"""
    WITH hfq AS (
        {hfq}
    ),
    ret AS (
        SELECT ts_code, trade_date, pct_chg, hfq_close,
               LAG(hfq_close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_hfq_close
        FROM hfq
    ),
    resid AS (
        SELECT ts_code, trade_date, pct_chg,
               (hfq_close / prev_hfq_close - 1.0) * 100 AS hfq_pct_chg,
               (hfq_close / prev_hfq_close - 1.0) * 100 - pct_chg AS resid
        FROM ret
        WHERE prev_hfq_close IS NOT NULL AND prev_hfq_close != 0
          AND hfq_close IS NOT NULL AND pct_chg IS NOT NULL
    )
    SELECT
        count(*) AS n,
        median(abs(resid)) AS med,
        quantile_cont(abs(resid), 0.90) AS p90,
        quantile_cont(abs(resid), 0.99) AS p99,
        max(abs(resid)) AS mx,
        sum(CASE WHEN abs(resid) > {alert_threshold_pp} THEN 1 ELSE 0 END) AS n_over
    FROM resid
    """
    row = con.execute(sql).fetchone()
    n = int(row[0] or 0)
    if n == 0:
        return PctChgSelfCheckReport(
            n_rows=0, median_abs_resid_pp=None, p90_abs_resid_pp=None, p99_abs_resid_pp=None,
            max_abs_resid_pp=None, n_over_alert_pp=0, alert_threshold_pp=alert_threshold_pp, worst_rows=(),
        )

    worst_sql = f"""
    WITH hfq AS (
        {hfq}
    ),
    ret AS (
        SELECT ts_code, trade_date, pct_chg, hfq_close,
               LAG(hfq_close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_hfq_close
        FROM hfq
    ),
    resid AS (
        SELECT ts_code, trade_date, pct_chg,
               (hfq_close / prev_hfq_close - 1.0) * 100 AS hfq_pct_chg,
               (hfq_close / prev_hfq_close - 1.0) * 100 - pct_chg AS resid
        FROM ret
        WHERE prev_hfq_close IS NOT NULL AND prev_hfq_close != 0
          AND hfq_close IS NOT NULL AND pct_chg IS NOT NULL
    )
    SELECT ts_code, trade_date, pct_chg, hfq_pct_chg, resid
    FROM resid
    ORDER BY abs(resid) DESC
    LIMIT {int(worst_n)}
    """
    worst_rows = tuple(
        {"ts_code": r[0], "trade_date": r[1], "pct_chg": r[2], "hfq_pct_chg": r[3], "resid": r[4]}
        for r in con.execute(worst_sql).fetchall()
    )

    return PctChgSelfCheckReport(
        n_rows=n,
        median_abs_resid_pp=float(row[1]) if row[1] is not None else None,
        p90_abs_resid_pp=float(row[2]) if row[2] is not None else None,
        p99_abs_resid_pp=float(row[3]) if row[3] is not None else None,
        max_abs_resid_pp=float(row[4]) if row[4] is not None else None,
        n_over_alert_pp=int(row[5] or 0),
        alert_threshold_pp=alert_threshold_pp,
        worst_rows=worst_rows,
    )


# --------------------------------------------------------------------------- validation #2: tushare reconciliation


@dataclass(frozen=True)
class TushareDivergence:
    ts_code: str
    max_rel_diff: float
    n_rows_compared: int
    first_divergence_date: Any
    dividend_match: bool
    matched_ex_date: str | None
    our_ratio_at_divergence: float | None
    tushare_adj_factor_changed: bool | None
    classification: str


@dataclass(frozen=True)
class TushareReconciliationReport:
    n_rows_compared: int
    median_rel_diff: float | None
    p99_rel_diff: float | None
    max_rel_diff: float | None
    alert_threshold: float
    divergences: tuple[TushareDivergence, ...]


# 分类常量：只描述"证据指向哪一类原因"，不是可调阈值——阈值在 config 的
# tushare_reconciliation.relative_diff_alert_threshold。
CLASS_TUSHARE_STALE = "tushare_adj_factor_stale_or_missing"
CLASS_TUSHARE_UNEXPLAINED_JUMP = "tushare_adj_factor_unexplained_jump"
CLASS_UNRESOLVED = "unresolved_unknown_cause"


def reconcile_vs_tushare(
    con: Any,
    cfg: AdjustFactorConfig | None = None,
    *,
    source_relation: str = DEFAULT_SOURCE_RELATION,
    adj_factor_relation: str = DEFAULT_ADJ_FACTOR_RELATION,
    dividend_relation: str = DEFAULT_DIVIDEND_RELATION,
    dividend_window_days: int = 10,
) -> TushareReconciliationReport:
    """诊断用对账："跟 tushare 一致"只证明两者相同，不证明两者都对（tushare 的 adj_factor
    在公开渠道 GitHub waditu/tushare issues #1555 #1736 #924 已知存在失准/快照不一致）。
    本函数从不据此改写、丢弃或置空 ``hfq_sql`` 的输出——config 的
    ``tushare_reconciliation.authority = self_computed_from_pre_close`` 就是这条声明。

    分类成员是数据不是 YAML：具体哪些股偏差大、判成哪一类，会随 tushare 更新其 adj_factor
    而变化，所以本函数**实时计算**，不读一份写死的名单。

    已实测（2026-09-07, 剔除北交所）三类证据模式：

    1. ``tushare_adj_factor_stale_or_missing``（19 只里 ≥16 只，如 000908.SZ/600165.SH/
       300506.SZ/000691.SZ/002713.SZ/300125.SZ/300159.SZ/000430.SZ/300093.SZ/002200.SZ/
       000615.SZ/002822.SZ/000793.SZ/002742.SZ/300091.SZ/600717.SH——具体名单以本函数当次
       调用结果为准，这里只记录曾经观测到的模式）：``raw_tushare_dividend`` 能查到
       ``div_proc='实施'`` 的送转/分红记录，``ex_date`` 与本模块检测到的 ``pre_close`` 跳变日
       一致或接近，而 ``raw_tushare_adj_factor`` 在同一过渡前后未变（或变化时点对不上）——
       判定 tushare 一侧没跟上已执行的公司行动，不是本模块误判。
    2. ``tushare_adj_factor_unexplained_jump``（19 只里 2 只：000545.SZ / 000998.SZ）：
       本模块当日 ``ratio=1``（``pre_close`` 与前收完全相等，可验证当天确无价格跳变），但
       ``raw_tushare_adj_factor`` 在同一天无端跳变——判定 tushare 快照/来源不一致，同样不是
       本模块误判（本模块的输入 ``pre_close`` 那天根本没变化，无从"算错"）。
    3. ``unresolved_unknown_cause``（19 只里 1 只：300176.SZ）：该股价格跳变发生在连续多个
       交易日停牌之后，``raw_tushare_dividend`` 附近窗口查无匹配记录——根因不明（很可能是
       ``raw_tushare_dividend`` 本身的覆盖缺口，该表对同一只股已知有多年空档，例如
       000998.SZ 2019-07-15 到 2024-07-16 之间无任何记录，不代表这五年真的零分红）。
       处置：保留自算值（``pre_close`` 仍是交易所权威字段），但在返回结果里显式标
       ``unresolved_unknown_cause``，不允许调用方把它悄悄当成"已核实一致"。
    """
    cfg = cfg or load_config()
    hfq = hfq_sql(cfg, source_relation)
    sql = f"""
    WITH hfq AS (
        {hfq}
    ),
    joined AS (
        SELECT h.ts_code, h.trade_date, h.ratio, h.hfq_factor AS f, a.adj_factor
        FROM hfq h
        JOIN {adj_factor_relation} a
          ON a.ts_code = h.ts_code AND a.trade_date = strftime(h.trade_date, '%Y%m%d')
        WHERE h.hfq_factor IS NOT NULL AND a.adj_factor IS NOT NULL AND a.adj_factor != 0
    ),
    norm AS (
        SELECT j.*,
            FIRST_VALUE(f) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS f0,
            FIRST_VALUE(adj_factor) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS a0
        FROM joined j
    ),
    reldiff AS (
        SELECT ts_code, trade_date, ratio, adj_factor,
               abs((f / f0) - (adj_factor / a0)) / (adj_factor / a0) AS rel_diff
        FROM norm
        WHERE f0 IS NOT NULL AND a0 IS NOT NULL AND a0 != 0 AND f0 != 0
    )
    SELECT
        count(*) AS n,
        median(rel_diff) AS med,
        quantile_cont(rel_diff, 0.99) AS p99,
        max(rel_diff) AS mx
    FROM reldiff
    """
    row = con.execute(sql).fetchone()
    n = int(row[0] or 0)
    if n == 0:
        return TushareReconciliationReport(
            n_rows_compared=0, median_rel_diff=None, p99_rel_diff=None, max_rel_diff=None,
            alert_threshold=cfg.reconciliation_alert_threshold, divergences=(),
        )

    reldiff_cte = f"""
    WITH hfq AS (
        {hfq}
    ),
    joined AS (
        SELECT h.ts_code, h.trade_date, h.ratio, h.hfq_factor AS f, a.adj_factor
        FROM hfq h
        JOIN {adj_factor_relation} a
          ON a.ts_code = h.ts_code AND a.trade_date = strftime(h.trade_date, '%Y%m%d')
        WHERE h.hfq_factor IS NOT NULL AND a.adj_factor IS NOT NULL AND a.adj_factor != 0
    ),
    norm AS (
        SELECT j.*,
            FIRST_VALUE(f) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS f0,
            FIRST_VALUE(adj_factor) OVER (
                PARTITION BY ts_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS a0
        FROM joined j
    )
    SELECT ts_code, trade_date, ratio, adj_factor,
           abs((f / f0) - (adj_factor / a0)) / (adj_factor / a0) AS rel_diff
    FROM norm
    WHERE f0 IS NOT NULL AND a0 IS NOT NULL AND a0 != 0 AND f0 != 0
    """

    bad_stocks = con.execute(
        f"""
        SELECT ts_code, max(rel_diff) AS max_rd, count(*) AS n_rows
        FROM ({reldiff_cte})
        GROUP BY ts_code
        HAVING max(rel_diff) > {cfg.reconciliation_alert_threshold}
        ORDER BY max_rd DESC
        """
    ).fetchall()

    divergences: list[TushareDivergence] = []
    for ts_code, max_rd, n_rows in bad_stocks:
        stock_rows = con.execute(
            f"""
            SELECT trade_date, ratio, adj_factor, rel_diff
            FROM ({reldiff_cte})
            WHERE ts_code = ?
            ORDER BY trade_date
            """,
            [ts_code],
        ).fetchall()
        first_div = next((r for r in stock_rows if r[3] > cfg.reconciliation_alert_threshold / 10), stock_rows[0])
        first_div_date, our_ratio, adj_factor_at, _ = first_div

        div_row = con.execute(
            f"""
            SELECT ex_date, div_proc, stk_div, cash_div
            FROM {dividend_relation}
            WHERE ts_code = ?
              AND ex_date BETWEEN strftime(CAST(? AS DATE) - INTERVAL '{dividend_window_days} days', '%Y%m%d')
                              AND strftime(CAST(? AS DATE) + INTERVAL '{dividend_window_days} days', '%Y%m%d')
              AND div_proc = '实施'
            ORDER BY abs(datediff('day', strptime(ex_date, '%Y%m%d')::DATE, CAST(? AS DATE)))
            LIMIT 1
            """,
            [ts_code, str(first_div_date), str(first_div_date), str(first_div_date)],
        ).fetchone()

        dividend_match = div_row is not None
        matched_ex_date = str(div_row[0]) if div_row else None

        # divergence 前后两行的 adj_factor 是否真的动过 —— 用来区分"漏更新"vs"更新了但时点不同步"。
        idx = next(i for i, r in enumerate(stock_rows) if r[0] == first_div_date)
        before_af = stock_rows[idx - 1][2] if idx > 0 else None
        after_af = stock_rows[idx][2]
        tushare_changed = (before_af is not None) and (abs(after_af - before_af) > 1e-9)

        if dividend_match:
            classification = CLASS_TUSHARE_STALE
        elif our_ratio is not None and abs(our_ratio - 1.0) < 1e-9:
            classification = CLASS_TUSHARE_UNEXPLAINED_JUMP
        else:
            classification = CLASS_UNRESOLVED

        divergences.append(
            TushareDivergence(
                ts_code=ts_code,
                max_rel_diff=float(max_rd),
                n_rows_compared=int(n_rows),
                first_divergence_date=first_div_date,
                dividend_match=dividend_match,
                matched_ex_date=matched_ex_date,
                our_ratio_at_divergence=float(our_ratio) if our_ratio is not None else None,
                tushare_adj_factor_changed=tushare_changed,
                classification=classification,
            )
        )

    return TushareReconciliationReport(
        n_rows_compared=n,
        median_rel_diff=float(row[1]) if row[1] is not None else None,
        p99_rel_diff=float(row[2]) if row[2] is not None else None,
        max_rel_diff=float(row[3]) if row[3] is not None else None,
        alert_threshold=cfg.reconciliation_alert_threshold,
        divergences=tuple(divergences),
    )
