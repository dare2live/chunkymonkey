"""判例引擎第三层: `casebook_pair_stats(S, X)` —— 唯一算「格 (S,X,无情形)」的地方。

**两个档案是这一张表的两个投影, 不是两套聚合。**
  策略档案 S = SELECT ... WHERE strategy_id = ?    (这条公式在每只股上怎么样)
  股票档案 X = SELECT ... WHERE code = ?           (这只股在每条公式的买点上怎么样)
两套聚合 = 两处算 `wr − base(X)`, 迟早一处改了 H 而另一处没改 (2026-06-27「第二真相源」教训)。

## 两条超额, 没有第三种基线

    超额_自比 = wr − base(X)                 剥掉「挑了哪些股」
    超额_同日 = wr − mean(mkt(t)) over 格     剥掉「挑了哪些股」**和**「挑了哪些天」
    两者相减 = 择日成分, 不另算。

`(S, 全市场, 无情形)` 的池化胜率**不是基线**, 它是被比较者 —— 正是那个让人看到
50.0% 却看不到同期市场 49.0% 的数字。

## 有效样本量 (口径全部读 casebook.yaml.effective_n, 本文件不设常量)

    A1 信号侧 n_eff  贪心不重叠: 按 bar 序, 前一个入选之后 > H 根的才入选。
    A2 基线侧 n_eff  floor((n_days − 1) / (H + 1)) + 1  (A1 在「每天都是信号」时的闭式)
    A3 n_pair        1 / (1/n_eff_sig + 1/n_eff_base)
    A5 分层          按 95% 区间**半宽**: <=6.9pp 可比较 / <=16.8 可参考 / <=26.3 样本薄 / 其余先例不足

为什么用半宽而不是原始 n: 同一个 n 在不同 p 下分辨率不同, 而人要问的是「这个数够不够准」。
**后果要说清楚**: base(X) 自己的 n_eff 上限是 168 (最长 1,847 天 / 间隔 >10),
所以本股层的「可比较」对**任何**策略都不可达 —— 这不是某条公式不行, 是这个层级的分辨率上限。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from services.casebook.outcome import load_window
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

_CONFIG = Path(__file__).resolve().parents[3] / "backend" / "config" / "casebook.yaml"

_LEGAL_RULE = {"greedy_nonoverlap"}
_LEGAL_PAIR = {"harmonic"}
_Z = 1.959963984540054  # 95%


def load_effective_n(path: Path | None = None) -> dict[str, Any]:
    """读有效样本量口径, fail closed。

    换算法 = 换全部判定, 所以未知 rule / pair 一律抛, 不静默回退到原始 n ——
    原始 n 在 1,845 日密集抽样下把方差低估 2.4x, 静默回退等于把所有格子判宽一档。
    """
    raw = yaml.safe_load((path or _CONFIG).read_text(encoding="utf-8"))
    cfg = (raw or {}).get("effective_n")
    if not isinstance(cfg, dict):
        raise ValueError("casebook.yaml 缺 effective_n 段")
    if cfg.get("rule") not in _LEGAL_RULE:
        raise ValueError(f"effective_n.rule={cfg.get('rule')!r} 不在 {sorted(_LEGAL_RULE)}")
    if cfg.get("pair") not in _LEGAL_PAIR:
        raise ValueError(f"effective_n.pair={cfg.get('pair')!r} 不在 {sorted(_LEGAL_PAIR)}")
    tiers = cfg.get("tiers_pp")
    if (
        not isinstance(tiers, list)
        or len(tiers) != 3
        or not all(isinstance(t, (int, float)) and t > 0 for t in tiers)
        or not (tiers[0] < tiers[1] < tiers[2])
    ):
        raise ValueError(f"effective_n.tiers_pp 必须是三个递增正数, 实得 {tiers!r}")
    return cfg


def load_sample_tiers(path: Path | None = None) -> tuple[float, float, float]:
    """读 (样本薄, 可参考, 可比较) 三个 n_pair 门槛。数不变(10/30/200), 变的是作用对象。"""
    raw = yaml.safe_load((path or _CONFIG).read_text(encoding="utf-8"))
    st = (raw or {}).get("sample_tiers")
    if not isinstance(st, dict):
        raise ValueError("casebook.yaml 缺 sample_tiers 段")
    try:
        thin = float(st["insufficient"])
        ref = float(st["referable"])
        cmp_ = float(st["comparable"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"sample_tiers 缺键或非数: {st!r}") from exc
    if not (0 < thin < ref < cmp_):
        raise ValueError(f"sample_tiers 必须 0 < insufficient < referable < comparable, 实得 {st!r}")
    return (thin, ref, cmp_)


def greedy_nonoverlap(idx: np.ndarray, h: int) -> int:
    """A1: 互不重叠的持有窗个数。`idx` 是同一 (S,X) 的信号 bar 序, 需已排序。

    规则一行: last = −inf; for i: if i > last + h → 计数+1, last = i。
    它是**下界** —— 不重叠窗在无长记忆的日收益过程下独立, 所以只会偏保守, 不会低估方差。
    """
    n = 0
    last = -(10**18)
    for i in idx:
        if i > last + h:
            n += 1
            last = int(i)
    return n


def baseline_n_eff(n_days: int, h: int) -> int:
    """A2: A1 在「每天都是信号」时的闭式 = floor((n_days − 1) / (h + 1)) + 1。"""
    if n_days <= 0:
        return 0
    return (n_days - 1) // (h + 1) + 1


def n_pair(n_eff_sig: int, n_eff_base: int) -> float:
    """A3: 1 / (1/n_sig + 1/n_base)。任一为 0 ⇒ 0 (没有可判定的分辨率)。"""
    if n_eff_sig <= 0 or n_eff_base <= 0:
        return 0.0
    return 1.0 / (1.0 / n_eff_sig + 1.0 / n_eff_base)


def wilson(p: float, n: int, z: float = _Z) -> tuple[float, float]:
    """胜率的 Wilson 95% 区间 @ n_eff。n<=0 ⇒ (nan, nan), **不返回 (0,1) 冒充"无信息"**。"""
    if n <= 0:
        return (math.nan, math.nan)
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - hw, c + hw)


def excess_halfwidth(p_s: float, n_s: int, p_b: float, n_b: int, z: float = _Z) -> float:
    """超额的 95% 半宽: z·sqrt(p_s(1−p_s)/n_s + p_b(1−p_b)/n_b)。

    信号日 ⊂ 全部日 ⇒ 两者正相关 ⇒ 独立假设下的这个式子**偏保守**(区间偏宽)。
    信号占比 f 作为出处字段一并存下, 让读的人知道保守了多少。
    """
    if n_s <= 0 or n_b <= 0:
        return math.nan
    return z * math.sqrt(p_s * (1 - p_s) / n_s + p_b * (1 - p_b) / n_b)


def tier_of(np_: float, tiers_n: tuple[float, float, float]) -> str:
    """A5 本股格: 按 **n_pair** 分层, 不按半宽。

    规格原文: 「本股格用 A3 的 n_pair, 全市场格与策略整体用 A4 的半宽」。
    半宽只是 n_pair 门槛在 **p≈0.5** 时的 Wilson 等价说法, 两者在退化 p 下分道扬镳:

    2026-09-04 实测踩过 —— 第一版按半宽判, 结果 3 个格子被判成「可比较」, 逐个查全是
    只有 9-19 天数据的股票, 每天全赢或全输 (p=0 或 1)。正态近似的方差 p(1−p) 在那里
    塌成 0, 半宽跟着塌成 0, 于是**一个只有 1 个信号的格子拿到了最高精度档**。
    信息最少的地方被判成最确定 —— 与判据想表达的东西正好相反。
    n_pair 不塌: 那 3 个的 n_pair 分别是 0.5 / 0.67 / 1.0, 一眼就是先例不足。

    半宽仍然存下来 (halfwidth_pp), 但只作出处字段, 不做判定。
    """
    if np_ is None or math.isnan(np_):
        return "insufficient"
    thin, referable, comparable = tiers_n
    if np_ >= comparable:
        return "comparable"
    if np_ >= referable:
        return "referable"
    if np_ >= thin:
        return "thin"
    return "insufficient"


def build(*, horizon: int | None = None) -> dict[str, Any]:
    """物化 casebook_pair_stats。行数 = 策略数 x 有信号的股票数。"""
    win = load_window()
    cfg = load_effective_n()
    h = horizon if horizon is not None else win.baseline_horizon
    if h not in win.horizons:
        raise ValueError(f"horizon={h} 不在 horizons={win.horizons}")
    tiers_n = load_sample_tiers()   # 本股格按 n_pair 判, 不按半宽 (见 tier_of)

    manifest = get_database_manifest()
    conn = duck_connect(str(manifest.path_for("feature_store")), read_only=False)
    try:
        # 一次取回「每个信号的结果 + 当天市场环境」, 分组统计在 SQL 里做,
        # 只有 n_eff 的贪心扫描必须逐行 (它是序贯的, 没有窗口函数等价形式)。
        agg = conn.raw.execute(f"""
SELECT c.strategy_id, c.code,
       count(*)                                                     AS n_raw,
       count(*) FILTER (WHERE ok)                                   AS n_valid,
       avg(CASE WHEN ok AND ret > 0 THEN 1.0 WHEN ok THEN 0.0 END)  AS wr,
       avg(CASE WHEN ok THEN m.mkt_wr END)                          AS mkt_mean,
       min(c.date)                                                  AS first_signal,
       max(c.date)                                                  AS last_signal,
       count(*) FILTER (WHERE NOT ok)                               AS censored_tail
FROM (
  SELECT c.strategy_id, c.code, c.date, c.i,
         o.exit_{h} / o.entry_open - 1 AS ret,
         (o.exit_{h} IS NOT NULL AND o.entry_open IS NOT NULL AND o.entry_open > 0) AS ok
  FROM casebook_case c
  JOIN casebook_outcome_day o USING (code, date)
) c
LEFT JOIN casebook_market_day m ON m.date = c.date
GROUP BY c.strategy_id, c.code
""").df()

        # A1 贪心: 只对**有效**信号算 (censored 的窗口出不来, 不占一个不重叠窗)
        sig = conn.raw.execute(f"""
SELECT c.strategy_id, c.code, c.i
FROM casebook_case c
JOIN casebook_outcome_day o USING (code, date)
WHERE o.exit_{h} IS NOT NULL AND o.entry_open IS NOT NULL AND o.entry_open > 0
ORDER BY c.strategy_id, c.code, c.i
""").df()
        neff: dict[tuple[str, str], int] = {}
        if len(sig):
            s_arr = sig["strategy_id"].to_numpy()
            c_arr = sig["code"].to_numpy()
            i_arr = sig["i"].to_numpy()
            key = np.char.add(np.char.add(s_arr.astype(str), "\x00"), c_arr.astype(str))
            change = np.flatnonzero(key[1:] != key[:-1]) + 1
            starts = np.concatenate(([0], change))
            ends = np.concatenate((change, [key.size]))
            for st, en in zip(starts, ends):
                neff[(str(s_arr[st]), str(c_arr[st]))] = greedy_nonoverlap(i_arr[st:en], h)

        base = conn.raw.execute(
            "SELECT code, base_n_days, base_wr FROM casebook_stock_baseline"
        ).df()
        base_map = {
            str(r.code): (int(r.base_n_days), float(r.base_wr))
            for r in base.itertuples()
        }

        rows: list[tuple[Any, ...]] = []
        for r in agg.itertuples():
            sid, code = str(r.strategy_id), str(r.code)
            n_valid = int(r.n_valid)
            n_es = neff.get((sid, code), 0)
            bd, bw = base_map.get(code, (0, math.nan))
            n_eb = baseline_n_eff(bd, h)
            np_val = n_pair(n_es, n_eb)
            wr = float(r.wr) if r.wr is not None and not (isinstance(r.wr, float) and math.isnan(r.wr)) else math.nan
            mkt = float(r.mkt_mean) if r.mkt_mean is not None else math.nan
            lo, hi = wilson(wr, n_es) if n_es > 0 and not math.isnan(wr) else (math.nan, math.nan)
            hw = excess_halfwidth(wr, n_es, bw, n_eb) if not math.isnan(wr) and not math.isnan(bw) else math.nan
            hw_pp = hw * 100 if not math.isnan(hw) else math.nan
            ex_own = wr - bw if not math.isnan(wr) and not math.isnan(bw) else math.nan
            ex_mkt = wr - mkt if not math.isnan(wr) and not math.isnan(mkt) else math.nan
            rows.append((
                sid, code, h,
                int(r.n_raw), n_valid, n_es, n_eb, np_val,
                wr, lo, hi,
                bw, bd, mkt,
                ex_own, ex_mkt,
                ex_own - hw if not math.isnan(ex_own) and not math.isnan(hw) else math.nan,
                ex_own + hw if not math.isnan(ex_own) and not math.isnan(hw) else math.nan,
                hw_pp, tier_of(np_val, tiers_n),
                (n_valid / bd) if bd > 0 else math.nan,   # signal_share f: 出处字段
                str(r.first_signal), str(r.last_signal), int(r.censored_tail),
            ))

        conn.execute("""
CREATE OR REPLACE TABLE casebook_pair_stats (
  strategy_id VARCHAR, code VARCHAR, horizon BIGINT,
  n_raw BIGINT, n_valid BIGINT, n_eff_sig BIGINT, n_eff_base BIGINT, n_pair DOUBLE,
  wr DOUBLE, wilson_lo DOUBLE, wilson_hi DOUBLE,
  base_wr DOUBLE, base_n_days BIGINT, mkt_mean DOUBLE,
  excess_own DOUBLE, excess_mkt DOUBLE, excess_lo DOUBLE, excess_hi DOUBLE,
  halfwidth_pp DOUBLE, tier VARCHAR,
  signal_share DOUBLE, first_signal VARCHAR, last_signal VARCHAR, censored_tail BIGINT
)
""")
        if rows:
            conn.executemany(
                "INSERT INTO casebook_pair_stats VALUES ("
                + ",".join("?" * 24) + ")",
                rows,
            )
        summary = conn.raw.execute("""
SELECT count(*) AS pairs, count(DISTINCT strategy_id) AS strategies,
       count(DISTINCT code) AS codes,
       sum(CASE WHEN tier='comparable' THEN 1 ELSE 0 END) AS comparable,
       sum(CASE WHEN tier='referable'  THEN 1 ELSE 0 END) AS referable,
       sum(CASE WHEN tier='thin'       THEN 1 ELSE 0 END) AS thin,
       sum(CASE WHEN tier='insufficient' THEN 1 ELSE 0 END) AS insufficient
FROM casebook_pair_stats
""").fetchone()
    finally:
        conn.close()

    return {
        "horizon": h,
        "pairs": int(summary[0]), "strategies": int(summary[1]), "codes": int(summary[2]),
        "comparable": int(summary[3]), "referable": int(summary[4]),
        "thin": int(summary[5]), "insufficient": int(summary[6]),
    }
