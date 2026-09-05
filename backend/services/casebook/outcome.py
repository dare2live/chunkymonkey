"""判例引擎地基: 每股每日固定窗口结果 + 两个自比基线。**与策略无关。**

为什么这层要独立于策略: 它是所有策略共用的分母。若每条策略各自算自己的基线,
迟早一处改了 H 而另一处没改 —— 本项目 2026-06-27「第二真相源」教训。
所以: 事实算一次, 策略只贡献「哪些股票日是它的触发点」。

三张产物 (feature_store.duckdb, writer 唯一 = 本模块):
  casebook_outcome_day    每股每日: 入场价 + 各 H 的出场价 + 截尾标记。8.5M 行。
  casebook_stock_baseline base(X): 每股一行 —— 这只股"什么都不做"是什么样。
  casebook_market_day     mkt(t): 每交易日一行 —— 这一天的市场环境是什么样。

口径全部读 `backend/config/casebook.yaml`, 本文件不设常量 —— 改口径等于改全部历史结论,
那种东西必须在配置里可见, 不能藏在代码里 (CLAUDE.md 11)。

**截尾永不当 0** (红线 3): 窗口越过该股最后一根 K 线时收益是 NULL 且 censored=true,
不是 0、不是 latest fallback。退市股的最后 H 根就是这种。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

_CONFIG = Path(__file__).resolve().parents[3] / "backend" / "config" / "casebook.yaml"

_LEGAL_WINDOW = {
    "entry": {"next_open"},
    "exit": {"close_after_h"},
    "win_rule": {"ret_gt_zero"},
    "censored_policy": {"null_never_zero"},
}


@dataclass(frozen=True)
class CasebookWindow:
    """固定窗口口径。四个字段都是闭合取值集, 未知值直接抛。"""

    entry: str
    exit: str
    win_rule: str
    censored_policy: str
    horizons: tuple[int, ...]


def load_window(path: Path | None = None) -> CasebookWindow:
    """读口径, fail closed —— 未知键/未知取值/空 horizons 一律抛, 不给默认值。

    为什么不给默认值: 口径读错一个字, 全部历史结论都错且不报错。宁可起不来。
    """
    src = path or _CONFIG
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{src} 不是 mapping")

    horizons = raw.get("horizons")
    if not isinstance(horizons, list) or not horizons or not all(
        isinstance(h, int) and h > 0 for h in horizons
    ):
        raise ValueError(f"{src}: horizons 必须是非空正整数列表, 实得 {horizons!r}")

    win = raw.get("window")
    if not isinstance(win, dict):
        raise ValueError(f"{src}: 缺 window 段")
    unknown = set(win) - set(_LEGAL_WINDOW)
    if unknown:
        raise ValueError(f"{src}: window 未知键 {sorted(unknown)} —— 闭合键集, 加键要先改 loader")
    for key, legal in _LEGAL_WINDOW.items():
        val = win.get(key)
        if val not in legal:
            raise ValueError(f"{src}: window.{key}={val!r} 不在合法取值 {sorted(legal)} 内")

    return CasebookWindow(
        entry=win["entry"],
        exit=win["exit"],
        win_rule=win["win_rule"],
        censored_policy=win["censored_policy"],
        horizons=tuple(sorted(horizons)),
    )


def _outcome_sql(horizons: tuple[int, ...]) -> str:
    """入场 = 下一根开盘, 出场 = 入场后第 H 根收盘 ⇒ LEAD(close, H+1)。

    信号日 t 收盘后才知道信号, 所以当日不可成交 —— entry 用 LEAD(open,1) 而不是 close[t]。
    这是 PIT 红线在本层的具体形态。
    """
    exits = ",\n       ".join(
        f"LEAD(close, {h + 1}) OVER w AS exit_{h}" for h in horizons
    )
    cens = ",\n       ".join(
        f"(i + {h + 1}) > n_bars AS censored_{h}" for h in horizons
    )
    return f"""
CREATE OR REPLACE TABLE casebook_outcome_day AS
WITH bars AS (
  SELECT code, date, open, close,
         row_number() OVER (PARTITION BY code ORDER BY date) AS i,
         count(*)     OVER (PARTITION BY code)               AS n_bars
  FROM mkt.v_price_kline_qfq
)
SELECT code, date, i, n_bars,
       LEAD(open, 1) OVER w AS entry_open,
       {exits},
       {cens}
FROM bars
WINDOW w AS (PARTITION BY code ORDER BY date)
"""


def _ret_expr(h: int) -> str:
    return f"exit_{h} / entry_open - 1"


def _valid_pred(h: int) -> str:
    """一行在 H 上"有效" = 入场价与出场价都在且入场价 > 0。censored 行天然被排除。"""
    return f"exit_{h} IS NOT NULL AND entry_open IS NOT NULL AND entry_open > 0"


def build(*, horizon_for_baseline: int | None = None) -> dict[str, Any]:
    """全量重建三张表。实测 2.4 s —— 无界回看在这里廉价, 不设计增量。

    horizon_for_baseline: 基线表用哪个 H (默认取 horizons 的中位那个)。
    """
    win = load_window()
    h_base = horizon_for_baseline if horizon_for_baseline is not None else win.horizons[
        len(win.horizons) // 2
    ]
    if h_base not in win.horizons:
        raise ValueError(f"horizon_for_baseline={h_base} 不在 horizons={win.horizons}")

    manifest = get_database_manifest()
    conn = duck_connect(str(manifest.path_for("feature_store")), read_only=False)
    try:
        conn.execute(f"ATTACH '{manifest.path_for('market')}' AS mkt (READ_ONLY)")
        conn.execute(_outcome_sql(win.horizons))

        valid = _valid_pred(h_base)
        ret = _ret_expr(h_base)
        conn.execute(f"""
CREATE OR REPLACE TABLE casebook_stock_baseline AS
SELECT code,
       {h_base}                                              AS horizon,
       count(*)                                              AS base_n_days,
       avg(CASE WHEN {ret} > 0 THEN 1.0 ELSE 0.0 END)        AS base_wr,
       median({ret})                                         AS base_med_ret,
       min(date)                                             AS first_bar,
       max(date)                                             AS last_bar
FROM casebook_outcome_day
WHERE {valid}
GROUP BY code
""")
        conn.execute(f"""
CREATE OR REPLACE TABLE casebook_market_day AS
SELECT date,
       {h_base}                                              AS horizon,
       count(*)                                              AS n_codes,
       avg(CASE WHEN {ret} > 0 THEN 1.0 ELSE 0.0 END)        AS mkt_wr,
       median({ret})                                         AS mkt_med_ret
FROM casebook_outcome_day
WHERE {valid}
GROUP BY date
""")

        stats = conn.execute(f"""
SELECT (SELECT count(*) FROM casebook_outcome_day)                       AS outcome_rows,
       (SELECT count(*) FROM casebook_outcome_day WHERE {valid})         AS valid_rows,
       (SELECT count(*) FROM casebook_outcome_day WHERE censored_{h_base}) AS censored_rows,
       (SELECT count(*) FROM casebook_stock_baseline)                    AS stocks,
       (SELECT count(*) FROM casebook_market_day)                        AS trading_days
""").fetchone()
    finally:
        conn.close()

    return {
        "horizons": list(win.horizons),
        "baseline_horizon": h_base,
        "outcome_rows": stats[0],
        "valid_rows": stats[1],
        "censored_rows": stats[2],
        "stocks": stats[3],
        "trading_days": stats[4],
    }
