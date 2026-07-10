"""rally_gt.py — D1 主升浪 GT v2 builder (五层漏斗正样本 + hard-negative + strata, feature_store 落库)。

owner=analysis/d1_gt_v2_design_20260702.md (实施定稿) + d1_gt_archaeology_20260702.md §3 (定义) / §4.2 (修正清单)。
方法论锚: goal.md D1 + MASTER §5 结果倒推 (episode-first: 标出每股每次主升浪, 底=起涨点=PIT 决策锚)。

定义 (v1.5 规则本体照搬, 双证据验证 — 考古 §4.1; 阈值全在 backend/config/rally_gt.yaml, 修正#10):
  L0 底→顶 swing: 波段底 (前后 pivot_low_window 最低) → max_forward_days 根内峰,
     峰/底-1 >= gain_min (60%), 峰距 >= min_duration_days (排单日尖峰); 同股 covered 去重。
  L1 universe: 前缀白名单 (services.universe) + 身份真相源 (raw_tushare_stock_basic, 排 K线里
     非个股码) + 非退市 (末K线距数据末 <= DELISTED_NO_TRADE_DAYS 自然日) + episode 内非 ST
     (PIT ST 日历 raw_tushare_stock_st, 每 st_sample_step_bars 根抽样)。
  L2 多头排列: 拉升期 [bottom..peak] 内∃某日 MA5>MA10>MA20>MA30>MA60 (bull_align_mode=any_day, P1 拍板)。
  L3 长底: 底前 base_lookback_days 内 >= base_min_days 日收盘落在 底low*[band_low, band_high]。
  L4 平滑: 拉升路径 closes[bottom..peak] max_dd > dd_floor (-30%)。
  E  右删失 embargo (v2 新增, 修正#1/#8): bottom + max_forward_days 交易日 > data_end 的 episode
     剔除 (与负样本 fwd_complete 判定共用 rally_detect.forward_complete, 正负对称)。

holdout 接线 (修正#1): rebuild 入口第一行 assert_holdout_untouched(data_end);
  data_end 默认 = holdout_policy.yaml holdout_start (20250601, 唯一真相源不复制); K线只读 <= data_end。
负样本 (考古 §3.3): 同结构 pivot-low + 长底 (同 PIT setup) + forward 窗完整 + 未涨 (<gain_min);
  purge 同股正样本 bottom ±max_forward_days 根; 同股间隔 >= min_gap_bars;
  ST 留消费侧 PIT 硬门 (is_st_on — ST 是时变量不可一刀切删股, v1.5 同)。
strata (考古 §3.4): 申万 L1/L2 as-of (raw_tushare_index_member_all, in_date<=底<=out_date, 含
  is_new='N' 历史区间 = 真 PIT) + 长底桶 + B1 sm.dim_stock_segment_daily ASOF bottom
  (mktcap_seg/turnover_seg/vol_regime, 修正#7 单一计算点) + B2 sm.fact_stock_form_daily
  bottom 日精确对照 (axis_pos/axis_purity)。B2 全局 2020-01-10 起 + per-stock warmup →
  更早 bottom 结构性 NULL; vol_regime 源端 rv warmup 期 NULL 如实透传 (实测 9%)。

列契约: backend/config/rally_gt_columns.yaml + services.gt_label_contract (修正#4 第一天重立);
  landing 断言核契约-表列同步 + gain/base 压线 + universe 干净 + 0 bottom > data_end。

写: feature_store.fact_rally_ground_truth / fact_rally_negative / fact_rally_strata
  (设计 P3: Type B 含前瞻, edge 隔离 — smartmoney 只放 Type A; DROP 重建, wipeable)。
读: market.price_kline_qfq_tushare (K线真相源, ATTACH mk) + reference.dim_trading_calendar
  (ATTACH ref) + smartmoney B1/B2 特征面 (ATTACH sm, READ_ONLY) + tushare_raw (独立只读连接
  raw_conn — universe.load_st_calendar 需默认 catalog 直连, 不能跨 ATTACH catalog 解析未限定
  表名; stock_basic / index_member_all 同连接读)。
用法: PYTHONPATH=backend .venv/bin/python -c "from services.rally_gt import rebuild; print(rebuild())"

状态 (2026-07-10 全栈审计F项收口): B1/B2 strata 列已接线 (原 TODO 占位 NULL 清偿);
  敏感性留档 (修正#9) 已在设计 doc 附录 (2026-07-02 实测, sandbox/d1_sensitivity)。

收编清单 (主会话 review 收编用, side-agent 禁改控制面):
  新文件: backend/services/rally_detect.py (共享原语) / backend/services/rally_gt.py (本文件) /
    backend/services/gt_label_contract.py (契约执法) / backend/config/rally_gt.yaml (阈值) /
    backend/config/rally_gt_columns.yaml (列契约) / backend/tests/test_rally_gt.py (证伪门)。
  待主会话: PROJECT_INDEX.md 活索引 + data_layers.yaml 三表声明 (若本 session 未加) + moth 断言 +
    goal.md/SESSION_HANDOFF 同步 + B1/B2 strata 列回填 + 敏感性分析 + safe_commit。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from services import rally_detect as rd
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.gt_label_contract import (
    entry_anchor,
    label_column,
    meta_columns,
    outcome_columns,
    pit_feature_columns,
)
from services.holdout_guard import assert_holdout_untouched, load_policy
from services.universe import (
    DELISTED_NO_TRADE_DAYS,
    assert_universe_clean,
    is_active_a_share,
    is_st_on,
    load_st_calendar,
)

logger = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "rally_gt.yaml"
CONTRACT = "rally_gt_columns.yaml"

GT_TABLE = "fact_rally_ground_truth"
NEG_TABLE = "fact_rally_negative"
STRATA_TABLE = "fact_rally_strata"


class RallyGTLandingError(RuntimeError):
    """落库断言失败 (设计 §3 第5条): 数据不满足定义硬约束, 拒绝交付。"""


def load_config() -> dict:
    return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))


def _to_iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip().replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise RallyGTLandingError(msg)


# ── 检测 (纯函数, 单测证伪门在此; 原语全走 services.rally_detect 单一计算点) ──────────────


def detect_episodes(code: str, dates, highs, lows, closes, st_cal: dict, cfg: dict) -> list[dict]:
    """对单股扫结构型主升浪 (L0/L2/L3/L4 判定 + st_in_episode 标记), 返回 dict 列表。

    L1 universe 与 E embargo 在 rebuild 漏斗层应用 (需要全局身份/日历上下文)。
    """
    ep = cfg["episode"]
    lowwin, maxfwd = int(ep["pivot_low_window"]), int(ep["max_forward_days"])
    gain_min, mindur = float(ep["gain_min"]), int(ep["min_duration_days"])
    lookback = int(ep["base_lookback_days"])
    band_lo, band_hi = float(ep["base_band_low"]), float(ep["base_band_high"])
    st_step = int(ep["st_sample_step_bars"])
    mode = str(ep["bull_align_mode"])
    if mode != "any_day":
        raise ValueError(f"bull_align_mode={mode!r} 未实现 — v2 首版只支持 any_day (P1 拍板, 改读法须换 taxonomy_version)")

    n = len(closes)
    if n < lookback:
        return []
    m5, m10, m20, m30, m60 = (pd.Series(closes).rolling(w).mean().to_numpy() for w in (5, 10, 20, 30, 60))
    aligned = (m5 > m10) & (m10 > m20) & (m20 > m30) & (m30 > m60)

    out: list[dict] = []
    covered = -1
    i = max(lowwin, int(ep["warmup_bars"]))
    while i < n - mindur:
        if i <= covered or not rd.is_pivot_low(lows, i, lowwin):
            i += 1
            continue
        peak = rd.forward_peak(highs, lows, i, maxfwd)
        if peak is not None:
            gain, po = peak
            if gain >= gain_min and po >= mindur:
                pk_idx = i + po
                path = closes[i: pk_idx + 1]
                dd = float(np.min(path / np.maximum.accumulate(path) - 1)) if len(path) else 0.0
                base = rd.base_days_count(closes, i, float(lows[i]), lookback, band_lo, band_hi)
                bull = bool(np.any(aligned[i: pk_idx + 1]))
                st_in = any(
                    is_st_on(code, str(dates[j]).replace("-", ""), st_cal)
                    for j in range(i, min(pk_idx + 1, n), st_step)
                )
                out.append(dict(
                    stock_code=code, bottom_date=str(dates[i]), peak_date=str(dates[pk_idx]),
                    gain_to_peak_pct=round(gain, 4), peak_offset_days=int(po),
                    base_days=int(base), bull_aligned=bull, path_max_dd_pct=round(dd, 4),
                    st_in_episode=st_in,
                ))
                covered = pk_idx
        i += 1
    return out


def detect_negatives(code: str, dates, highs, lows, closes, pos_idx: list[int],
                     trading_days: list[str], last_data_date: str, cfg: dict) -> list[tuple]:
    """单股 hard-negative pivot: 长底 + forward 完整 + 未涨, purge 正样本 ±max_forward_days 根。"""
    ep, ng = cfg["episode"], cfg["negative"]
    lowwin, maxfwd = int(ep["pivot_low_window"]), int(ep["max_forward_days"])
    gain_min, basemin = float(ep["gain_min"]), int(ep["base_min_days"])
    lookback = int(ep["base_lookback_days"])
    band_lo, band_hi = float(ep["base_band_low"]), float(ep["base_band_high"])
    n = len(closes)
    out: list[tuple] = []
    i = max(lowwin, int(ep["warmup_bars"]))
    tail = maxfwd // int(ng["tail_skip_divisor"])  # 末端 forward 太短跳过 (与正样本 MINDUR 区相称, v1.5 同)
    while i < n - tail:
        if not rd.is_pivot_low(lows, i, lowwin):
            i += 1
            continue
        if any(abs(i - j) < maxfwd for j in pos_idx):   # purge: forward 窗与正样本重叠 = 污染
            i += 1
            continue
        base = rd.base_days_count(closes, i, float(lows[i]), lookback, band_lo, band_hi)
        if base < basemin:
            i += 1
            continue
        if not rd.forward_complete(str(dates[i]), trading_days, last_data_date, maxfwd):
            i += 1
            continue
        gain = rd.forward_max_gain(highs, lows, i, maxfwd)
        if gain is None or gain >= gain_min:            # 涨了 = 正样本域; 无前瞻 = 不可判
            i += 1
            continue
        out.append((code, str(dates[i]), int(base)))
        i += int(ng["min_gap_bars"])                    # 同股负样本间隔, 防贴邻重复 pivot
    return out


# ── 数据装载 ──────────────────────────────────────────────────────────────────────


def _fetch_numpy(cur, cols: list[str]) -> dict[str, np.ndarray]:
    """DuckCursor → {col: ndarray}; 优先底层 fetchnumpy (8.3M 行级), 无则 fetchall 回退 (测试小数据)。"""
    raw = getattr(cur, "_cur", None)
    if raw is not None and hasattr(raw, "fetchnumpy"):
        d = raw.fetchnumpy()
        return {k: np.asarray(d[k]) for k in cols}
    rows = cur.fetchall()
    return {k: np.array([r[idx] for r in rows]) for idx, k in enumerate(cols)}


def _load_kline(conn, scan_start: str, data_end_iso: str) -> dict[str, np.ndarray]:
    cur = conn.execute(
        "SELECT code, date, high, low, close FROM mk.price_kline_qfq_tushare "
        "WHERE date >= ? AND date <= ? AND close > 0 ORDER BY code, date",
        (scan_start, data_end_iso),
    )
    return _fetch_numpy(cur, ["code", "date", "high", "low", "close"])


def _iter_stocks(arr: dict[str, np.ndarray]):
    """按 code 连续分组 (ORDER BY code,date 前提), yield (code, dates, highs, lows, closes)。"""
    codes = arr["code"]
    if not len(codes):
        return
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        yield (str(uniq[ci]), arr["date"][s:e].astype(str),
               arr["high"][s:e].astype(float), arr["low"][s:e].astype(float),
               arr["close"][s:e].astype(float))


# ── 落库 ──────────────────────────────────────────────────────────────────────────


def _write_gt(conn, rows: list[dict], maxfwd: int, taxonomy: str, built_at) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {GT_TABLE}")
    conn.execute(f"""
        CREATE TABLE {GT_TABLE} (
            stock_code        VARCHAR NOT NULL,
            bottom_date       DATE    NOT NULL,
            peak_date         DATE    NOT NULL,
            gain_to_peak_pct  DOUBLE  NOT NULL,
            peak_offset_days  INTEGER NOT NULL,
            base_days         INTEGER NOT NULL,
            bull_aligned      BOOLEAN NOT NULL,
            path_max_dd_pct   DOUBLE  NOT NULL,
            is_true_rally     BOOLEAN NOT NULL,
            fwd_window_len    INTEGER NOT NULL,
            taxonomy_version  VARCHAR NOT NULL,
            built_at          TIMESTAMP NOT NULL,
            PRIMARY KEY (stock_code, bottom_date)
        )""")
    conn.executemany(
        f"INSERT INTO {GT_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["stock_code"], r["bottom_date"], r["peak_date"], r["gain_to_peak_pct"],
          r["peak_offset_days"], r["base_days"], r["bull_aligned"], r["path_max_dd_pct"],
          True, maxfwd, taxonomy, built_at) for r in rows])
    conn.execute(f"CREATE INDEX idx_rally_gt_bottom ON {GT_TABLE}(bottom_date)")


def _write_negatives(conn, rows: list[tuple], maxfwd: int, taxonomy: str, built_at) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {NEG_TABLE}")
    conn.execute(f"""
        CREATE TABLE {NEG_TABLE} (
            stock_code        VARCHAR NOT NULL,
            entry_signal_date DATE    NOT NULL,
            base_days         INTEGER NOT NULL,
            fwd_complete      BOOLEAN NOT NULL,
            is_true_rally     BOOLEAN NOT NULL,
            fwd_window_len    INTEGER NOT NULL,
            taxonomy_version  VARCHAR NOT NULL,
            built_at          TIMESTAMP NOT NULL,
            PRIMARY KEY (stock_code, entry_signal_date)
        )""")
    conn.executemany(
        f"INSERT INTO {NEG_TABLE} VALUES (?,?,?,?,?,?,?,?)",
        [(c, d, b, True, False, maxfwd, taxonomy, built_at) for (c, d, b) in rows])
    conn.execute(f"CREATE INDEX idx_rally_neg_date ON {NEG_TABLE}(entry_signal_date)")


def _base_bucket_case(cfg: dict) -> str:
    """strata.base_buckets (yaml) → SQL CASE (下闭上开; 末段 null 上界 = 开区间到 +inf)。"""
    items = sorted(cfg["strata"]["base_buckets"].items(), key=lambda kv: kv[1][0])
    parts = []
    for name, (lo, hi) in items:
        cond = f"g.base_days >= {int(lo)}" + ("" if hi is None else f" AND g.base_days < {int(hi)}")
        parts.append(f"WHEN {cond} THEN '{name}'")
    return "CASE " + " ".join(parts) + " END"


def _write_strata(conn, raw_conn, cfg: dict, built_at) -> None:
    """申万 L1/L2 as-of (真 PIT, 含 is_new='N' 历史区间) + 长底桶 + B1/B2 特征面对照。

    B1 (修正#7 单一计算点): sm.dim_stock_segment_daily ASOF <= bottom (段值由当日及以前
    K线/basic 派生, 日粒度 PIT 干净; bottom 日必有 K线 bar → 预期精确命中当日行)。
    B2: sm.fact_stock_form_daily bottom 日精确对照 (form 特征因果 rolling, PIT 干净);
    2020-01-10 前 + per-stock warmup 期结构性 NULL (form_base_days 已删, 2026-07-03 审计修4)。
    """
    member_rows = [tuple(r[k] for k in range(7)) for r in raw_conn.execute(
        "SELECT l1_code, l1_name, l2_code, l2_name, SUBSTR(ts_code,1,6), "
        "CAST(in_date AS VARCHAR), CAST(out_date AS VARCHAR) FROM raw_tushare_index_member_all"
    ).fetchall()]
    conn.execute("CREATE OR REPLACE TEMP TABLE _sw_member ("
                 "l1_code VARCHAR, l1_name VARCHAR, l2_code VARCHAR, l2_name VARCHAR, "
                 "stock_code VARCHAR, in_date VARCHAR, out_date VARCHAR)")
    conn.executemany("INSERT INTO _sw_member VALUES (?,?,?,?,?,?,?)", member_rows)

    conn.execute(f"DROP TABLE IF EXISTS {STRATA_TABLE}")
    conn.execute(f"""
        CREATE TABLE {STRATA_TABLE} (
            stock_code    VARCHAR NOT NULL,
            bottom_date   DATE    NOT NULL,
            sw_l1_code    VARCHAR, sw_l1_name VARCHAR,
            sw_l2_code    VARCHAR, sw_l2_name VARCHAR,
            base_days     INTEGER NOT NULL,
            base_bucket   VARCHAR NOT NULL,
            mktcap_seg    VARCHAR,   -- B1 sm.dim_stock_segment_daily ASOF bottom (源端 0 NULL)
            turnover_seg  VARCHAR,   -- B1 同上 (源端 0 NULL)
            vol_regime    VARCHAR,   -- B1 同上 (源端 rv warmup 期 NULL 如实透传, 实测 9%)
            axis_pos      VARCHAR,   -- B2 sm.fact_stock_form_daily bottom 日精确对照 (warmup 前结构性 NULL)
            axis_purity   VARCHAR,   -- B2 同上
            -- form_base_days 已删 (2026-07-03 审计修4): 源 base_days 仅 breakout 事件日落值,
            -- bottom 日几乎从不是 breakout 日 → 5636/5636 全 NULL, B2 base 对照不可行 (奥卡姆)。
            built_at      TIMESTAMP NOT NULL,
            PRIMARY KEY (stock_code, bottom_date)
        )""")
    conn.execute(f"""
        INSERT INTO {STRATA_TABLE}
        SELECT g.stock_code, g.bottom_date,
               sec.l1_code, sec.l1_name, sec.l2_code, sec.l2_name,
               g.base_days, {_base_bucket_case(cfg)},
               seg.mktcap_seg, seg.turnover_seg, seg.vol_regime,
               f.axis_pos, f.axis_purity,
               ?
        FROM {GT_TABLE} g
        LEFT JOIN LATERAL (
            SELECT m.l1_code, m.l1_name, m.l2_code, m.l2_name FROM _sw_member m
            WHERE m.stock_code = g.stock_code
              AND m.in_date <= strftime(g.bottom_date, '%Y%m%d')
              AND (m.out_date IS NULL OR m.out_date >= strftime(g.bottom_date, '%Y%m%d'))
            ORDER BY m.in_date DESC LIMIT 1) sec ON TRUE
        ASOF LEFT JOIN sm.dim_stock_segment_daily seg
            ON g.stock_code = seg.stock_code
           AND seg.trade_date <= strftime(g.bottom_date, '%Y%m%d')
        LEFT JOIN sm.fact_stock_form_daily f
            ON f.stock_code = g.stock_code
           AND f.trade_date = strftime(g.bottom_date, '%Y%m%d')""", (built_at,))
    conn.execute("DROP TABLE _sw_member")


def _landing_assertions(conn, cfg: dict, data_end_iso: str,
                        trading_days: list[str], last_data_date: str) -> None:
    """设计 §3 第5条 落库断言 + 契约-表同步核 (全部 raise RallyGTLandingError, 不静默)。"""
    ep = cfg["episode"]
    gain_min, basemin, maxfwd = float(ep["gain_min"]), int(ep["base_min_days"]), int(ep["max_forward_days"])

    n_gt = conn.execute(f"SELECT count(*) FROM {GT_TABLE}").fetchone()[0]
    _check(n_gt > 0, "0 主升浪检出 — 检测/数据异常, 拒绝交付")
    g = conn.execute(
        f"SELECT min(gain_to_peak_pct), min(base_days), CAST(max(bottom_date) AS VARCHAR), "
        f"CAST(max(peak_date) AS VARCHAR) FROM {GT_TABLE}").fetchone()
    _check(g[0] >= gain_min, f"gain 压线违规: min(gain)={g[0]} < {gain_min}")
    _check(g[1] >= basemin, f"base_days 压线违规: min(base_days)={g[1]} < {basemin}")
    _check(g[2] <= data_end_iso, f"holdout 越界: max(bottom_date)={g[2]} > data_end={data_end_iso}")
    _check(g[3] <= data_end_iso, f"holdout 越界: max(peak_date)={g[3]} > data_end={data_end_iso}")

    # 0 排除股 (真相源 = services.universe, 不内联前缀)
    codes = [r[0] for r in conn.execute(f"SELECT DISTINCT stock_code FROM {GT_TABLE}").fetchall()]
    assert_universe_clean(codes, context=f"{GT_TABLE}.landing")

    # 右删失 embargo: 全部 episode forward 窗完整 (与负样本同一 forward_complete)
    bad = [b for (b,) in conn.execute(
        f"SELECT DISTINCT CAST(bottom_date AS VARCHAR) FROM {GT_TABLE}").fetchall()
        if not rd.forward_complete(b, trading_days, last_data_date, maxfwd)]
    _check(not bad, f"右删失 episode 混入 train: {len(bad)} 个 bottom (如 {bad[:3]})")

    # 契约-表列同步 (修正#4: 契约第一天生效, 防表改列契约漂移)
    table_cols = {r[0] for r in conn.execute(f"DESCRIBE {GT_TABLE}").fetchall()}
    contract_cols = ({entry_anchor(CONTRACT), label_column(CONTRACT), "stock_code"}
                     | set(pit_feature_columns(CONTRACT)) | set(outcome_columns(CONTRACT))
                     | set(meta_columns(CONTRACT)))
    _check(table_cols == contract_cols,
           f"列契约与表漂移: 表有契约无 {sorted(table_cols - contract_cols)}, "
           f"契约有表无 {sorted(contract_cols - table_cols)}")

    # 负样本: 同 setup 下限 + 全 fwd_complete + train 窗内
    n_neg = conn.execute(f"SELECT count(*) FROM {NEG_TABLE}").fetchone()[0]
    if n_neg:
        nb = conn.execute(
            f"SELECT min(base_days), bool_and(fwd_complete), CAST(max(entry_signal_date) AS VARCHAR) "
            f"FROM {NEG_TABLE}").fetchone()
        _check(nb[0] >= basemin, f"负样本 base_days 压线违规: min={nb[0]} < {basemin}")
        _check(bool(nb[1]), "负样本存在 fwd_complete=False (应全完整)")
        _check(nb[2] <= data_end_iso, f"负样本越界: max(entry)={nb[2]} > data_end={data_end_iso}")
        neg_codes = [r[0] for r in conn.execute(f"SELECT DISTINCT stock_code FROM {NEG_TABLE}").fetchall()]
        assert_universe_clean(neg_codes, context=f"{NEG_TABLE}.landing")

    # strata 与 GT 一一对应
    n_strata = conn.execute(f"SELECT count(*) FROM {STRATA_TABLE}").fetchone()[0]
    _check(n_strata == n_gt, f"strata 行数 {n_strata} != GT {n_gt} (应 1:1)")

    # B1/B2 非结构性 NULL = join 失灵硬门 (mktcap_seg/axis_pos 源端实测 0 NULL → 源有行
    # 而 strata 为 NULL 只可能是 join 断)。EXISTS 探针用 strptime→DATE 方向比较, 与写入
    # join 的 strftime→VARCHAR 方向相反 — 单向格式 bug 无法同时骗过两侧 (防 99.978%
    # fallback 类静默全 NULL)。vol_regime 源端 warmup NULL 如实透传, 不设门。
    b1_bad = conn.execute(f"""
        SELECT count(*) FROM {STRATA_TABLE} t
        WHERE t.mktcap_seg IS NULL AND EXISTS (
            SELECT 1 FROM sm.dim_stock_segment_daily s
            WHERE s.stock_code = t.stock_code
              AND CAST(strptime(s.trade_date, '%Y%m%d') AS DATE) <= t.bottom_date)""").fetchone()[0]
    _check(b1_bad == 0, f"B1 join 失灵: {b1_bad} 行源段表有 as-of 行但 mktcap_seg NULL")
    b2_bad = conn.execute(f"""
        SELECT count(*) FROM {STRATA_TABLE} t
        WHERE t.axis_pos IS NULL AND EXISTS (
            SELECT 1 FROM sm.fact_stock_form_daily f
            WHERE f.stock_code = t.stock_code
              AND CAST(strptime(f.trade_date, '%Y%m%d') AS DATE) = t.bottom_date)""").fetchone()[0]
    _check(b2_bad == 0, f"B2 join 失灵: {b2_bad} 行源形态表有 bottom 日行但 axis_pos NULL")


# ── 入口 ──────────────────────────────────────────────────────────────────────────


def rebuild(conn=None, data_end=None, raw_conn=None) -> dict:
    """全量重建 D1 主升浪 GT v2 三表 (feature_store, DROP 重建)。返回统计 dict。

    conn: feature_store 写连接 (须已 ATTACH mk=market / ref=reference / sm=smartmoney;
          None=自管+ATTACH)。
    data_end: train 窗右边界 (YYYYMMDD/YYYY-MM-DD; None=holdout_policy.holdout_start)。
    raw_conn: tushare_raw 只读连接 (默认 catalog 须含 raw_tushare_* 表; None=自管)。
    """
    # 1) holdout 门 — 入口第一行, 任何数据读取之前 (修正#1)
    data_end = str(data_end) if data_end is not None else str(load_policy()["holdout_start"])
    assert_holdout_untouched(data_end)
    data_end_iso = _to_iso(data_end)

    cfg = load_config()
    ep = cfg["episode"]
    maxfwd, basemin = int(ep["max_forward_days"]), int(ep["base_min_days"])
    taxonomy = str(cfg["taxonomy_version"])

    own_conn, own_raw = conn is None, raw_conn is None
    mf = get_database_manifest() if (own_conn or own_raw) else None
    if own_conn:
        conn = duck_connect(str(mf.path_for("feature_store")), read_only=False)
        conn.execute(f"ATTACH IF NOT EXISTS '{mf.path_for('market')}' AS mk (READ_ONLY)")
        conn.execute(f"ATTACH IF NOT EXISTS '{mf.path_for('reference')}' AS ref (READ_ONLY)")
        conn.execute(f"ATTACH IF NOT EXISTS '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
        conn.execute(f"ATTACH IF NOT EXISTS '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
        # 第三道门 (2026-07-03 用户定调"审查器像交易日历一样强制"): 消费侧连续性硬门 —
        # 触板判定消费的日频 raw 域有未豁免中间缺口 = raise (缺口喂进 GT = 错误标注)
        from services.continuity_guard import assert_domains_continuous
        assert_domains_continuous(["daily", "stk_limit"], conn)
    if own_raw:
        raw_conn = duck_connect(str(mf.path_for("tushare_raw")), read_only=True)
    try:
        # 2) 真相源装载 (全部 <= data_end; K线截断 = holdout 数据物理不进内存)
        st_cal = load_st_calendar(raw_conn)                     # PIT ST 日历 (单一计算点)
        identity = {str(r[0]) for r in raw_conn.execute(
            "SELECT DISTINCT symbol FROM raw_tushare_stock_basic").fetchall()}  # 身份真相源 (排非个股码)
        trading_days = [str(r[0]) for r in conn.execute(
            "SELECT trade_date FROM ref.dim_trading_calendar WHERE is_trading=1 AND trade_date <= ? "
            "ORDER BY trade_date", (data_end_iso,)).fetchall()]
        arr = _load_kline(conn, str(cfg["scan_start"]), data_end_iso)
        _check(len(arr["code"]) > 0, f"K线 0 行 (scan_start~{data_end_iso}) — 数据异常")
        last_data_date = str(arr["date"].max())   # ISO 字符串字典序 = 日期序

        # 3) 逐股五层漏斗 + embargo + 负样本
        funnel = dict(L0_swing=0, L1_universe=0, L2_bull=0, L3_base=0, L4_smooth=0, E_embargo_censored=0)
        keep: list[dict] = []
        negatives: list[tuple] = []
        n_stocks = 0
        for code, dts, highs, lows, closes in _iter_stocks(arr):
            if code not in identity or not is_active_a_share(code):
                continue
            if len(dts) < int(ep["base_lookback_days"]):
                continue
            n_stocks += 1
            delisted = (pd.to_datetime(last_data_date) - pd.to_datetime(str(dts[-1]))).days > int(DELISTED_NO_TRADE_DAYS)
            eps = detect_episodes(code, dts, highs, lows, closes, st_cal, cfg)
            purge_bottoms: set[str] = set()
            for e in eps:
                funnel["L0_swing"] += 1
                if delisted or e["st_in_episode"]:
                    continue
                funnel["L1_universe"] += 1
                if not e["bull_aligned"]:
                    continue
                funnel["L2_bull"] += 1
                if e["base_days"] < basemin:
                    continue
                funnel["L3_base"] += 1
                if e["path_max_dd_pct"] <= float(ep["dd_floor"]):
                    continue
                funnel["L4_smooth"] += 1
                # purge 集语义扩展 (2026-07-03 审计修3): 在 embargo 之前收集 — 被右删失剔出
                # train 的 L4-真 bottom 也必须进负样本 purge 集, 否则其 ±maxfwd 根内 pivot
                # 会成为紧邻(删失)主升浪底的污染负样本 (data_end 尾部, 如 2024H1 后段)。
                purge_bottoms.add(e["bottom_date"])
                if not rd.forward_complete(e["bottom_date"], trading_days, last_data_date, maxfwd):
                    funnel["E_embargo_censored"] += 1   # v2 右删失 embargo (修正#1/#8)
                    continue
                keep.append(e)
            pos_idx = [k for k, d in enumerate(dts) if str(d) in purge_bottoms]
            negatives.extend(detect_negatives(code, dts, highs, lows, closes, pos_idx,
                                              trading_days, last_data_date, cfg))

        # 4) universe 硬门 (交易日历级真相源; 排除股进 GT = raise, 非 warning)
        assert_universe_clean(sorted({e["stock_code"] for e in keep}), context=GT_TABLE)
        assert_universe_clean(sorted({c for c, _, _ in negatives}), context=NEG_TABLE)

        # 5) 落库 (feature_store, DROP 重建) + 断言
        built_at = datetime.now(timezone.utc)
        _write_gt(conn, keep, maxfwd, taxonomy, built_at)
        _write_negatives(conn, negatives, maxfwd, taxonomy, built_at)
        _write_strata(conn, raw_conn, cfg, built_at)
        _landing_assertions(conn, cfg, data_end_iso, trading_days, last_data_date)
        conn.execute("CHECKPOINT")

        by_year = {r[0]: r[1] for r in conn.execute(
            f"SELECT strftime(bottom_date, '%Y'), count(*) FROM {GT_TABLE} GROUP BY 1 ORDER BY 1").fetchall()}
        stats = dict(funnel=funnel, n_pos=len(keep), n_neg=len(negatives),
                     n_stocks_scanned=n_stocks, by_year=by_year,
                     data_end=data_end_iso, last_data_date=last_data_date, taxonomy_version=taxonomy)
        logger.info("[rally_gt] rebuild: %s", stats)
        return stats
    finally:
        if own_raw:
            raw_conn.close()
        if own_conn:
            conn.close()
