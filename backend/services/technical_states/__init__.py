"""technical_states — Tier1 股票状态/形态模块（历史编号 B2）。

设计契约: docs/MASTER_TOPLEVEL_DESIGN.md §7;
审查证据库: analysis/technical_states_audit_20260702.json (14 confirmed / 40 keeps)。

骨架 (契约 §3, 16 文件 → 7 + config):
  features.py  日线 12+3 维特征 + resample 只闭合 bar (H1/H8/medium)
  axes.py      5 正交轴独立分类 (几何均 + 联合 max_score 归一 + 轴内 softmax; H2/H7)
  labeler.py   cell→人话标签 + context 两遍 + 多 TF as-of（无方向语义）
  breakout.py  突破 event-in-context 检测器（overlay 事件非态，供 Tier3 GT 研究）
  limits.py    涨跌停/一字板 flags (stk_limit 真相源; H3 双侧 + H4 可成交透传)
  candles.py   单日 K 构件 (prior_trend 消歧; 沿用)
  patterns.py  命名形态 3 模板 (零参数, 完成 bar 命中不回贴; 沿用)
  config/technical_states.yaml  轴判据/cell映射/突破参数/涨跌停 proxy — 理论锚定取整值 (H6)

产物表 (契约 §5): smartmoney.fact_stock_form_daily (Type A 确定性 PIT 重排, 每日 process 步跑)。
上游: market.price_kline_qfq_tushare (当前派生分析输入；非名义成交真相) +
  tushare_raw.raw_tushare_daily ∪ canonical_nominal_ohlcv_daily / raw_tushare_stk_limit
  (原始价空间触板判定; formal daily 不写 legacy raw 时走 accepted canonical;
  S5 ``from_accepted=True`` / ``chunkyctl derive form --from-accepted`` 跳过 legacy raw fill) +
  tushare_raw.raw_tushare_index_daily (RS 基准) +
  reference.dim_trading_calendar (周期闭合真相源, H1) + smartmoney.dim_stock_segment_daily
  (Tier1 context: rv_pctile/vol_regime 列 — 先跑 segments 再跑本模块)。
入口: rebuild_all (全量) / build_latest (幂等增量) / chunkyctl derive form。
形态 = 结构层非 alpha (F1 裁决), 无买卖暗示。

当前边界: 本模块只发布版本化描述状态；未来标签、收益概率和买卖含义必须留在 Tier3 research。
"""
from __future__ import annotations

import logging
from bisect import bisect_left
from pathlib import Path
from typing import Any

import yaml

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.segments import vol_regime_threshold
from services.technical_states import axes, breakout, candles, features, labeler, limits, patterns  # noqa: F401
from services.technical_states.features import FEATURE_KEYS, compute, resample  # noqa: F401 (re-export)
from services.technical_states.labeler import Labeler

logger = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "technical_states.yaml"

TABLE = "fact_stock_form_daily"
# 列语义澄清 (审计 20260703 finding): axis_*_memb 实存各轴**几何均归一 score** [0,1]
# (classify_stock 写入 ax[轴]["score"]), **非** softmax membership 概率 —— axes.py classify()
# 同时算出的 membership (轴内各取值和=1) 未入库, 本列跨取值不求和为 1。
# 命名沿革: 语义上更该叫 axis_*_score, 但 score/label 等词撞 Type A 纯度门 _TYPE_A_LEAK_RE
# (2026-07-02 反例) 改名 memb。勿据列名回改语义/回改列名；Tier3 消费方按归一 score 解读。
_DDL = f"""
CREATE TABLE {TABLE} (
    stock_code VARCHAR NOT NULL,
    trade_date VARCHAR NOT NULL,
    axis_pos VARCHAR, axis_trend VARCHAR, axis_purity VARCHAR, axis_vol VARCHAR, axis_volregime VARCHAR,
    axis_pos_memb DOUBLE, axis_trend_memb DOUBLE, axis_purity_memb DOUBLE, axis_vol_memb DOUBLE,
    axis_volregime_memb DOUBLE,
    form_name VARCHAR, form_sub VARCHAR,
    weekly_name VARCHAR, monthly_name VARCHAR,
    is_breakout_event BOOLEAN, base_days INTEGER,
    buyable BOOLEAN, sellable BOOLEAN, is_one_word BOOLEAN,
    built_at TIMESTAMP
)"""
_INSERT = f"INSERT INTO {TABLE} VALUES ({','.join(['?'] * 21)}, CURRENT_TIMESTAMP)"
_CODE_CHUNK = 200   # 批处理大小 (内存边界常数, 非业务阈值): 每批 ~30 万行源数据


def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or _CFG_PATH).read_text(encoding="utf-8"))


def _db(alias: str) -> str:
    return str(get_database_manifest().path_for(alias))


def _attach(con) -> None:
    con.execute(f"ATTACH IF NOT EXISTS '{_db('market')}' AS mkt (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH IF NOT EXISTS '{_db('reference')}' AS ref (READ_ONLY)")


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _compact(iso: str) -> str:
    return iso.replace("-", "")


def _assert_b1_ready(con) -> None:
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'dim_stock_segment_daily'"
    ).fetchall()}
    if not cols:
        raise RuntimeError("dim_stock_segment_daily 不存在 — 先跑 Tier1 context segments.rebuild_all()")
    if "rv_pctile" not in cols or "vol_regime" not in cols:
        raise RuntimeError("dim_stock_segment_daily 缺 rv_pctile/vol_regime 列 — 先跑 Tier1 context segments.rebuild_all()")


def _trading_days(con) -> list[str]:
    days = [str(r[0])[:10] for r in con.execute(
        "SELECT trade_date FROM ref.dim_trading_calendar WHERE is_trading = 1 ORDER BY trade_date").fetchall()]
    if not days:
        raise RuntimeError("ref.dim_trading_calendar 无交易日 — 周期闭合真相源缺失 (H1)")
    return days


def _bench_close(con, cfg: dict) -> dict:
    rows = con.execute(
        "SELECT trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code = ? ORDER BY 1",
        [str(cfg["RS"]["基准"])]).fetchall()
    return {_iso(str(r[0])): float(r[1]) for r in rows if r[1] is not None}


# ---------------------------------------------------------------- 单股分类
def classify_stock(dates, o, h, l, c, v, *, trading_days, cfg: dict | None = None,
                   lab: Labeler | None = None, bench_close: dict | None = None,
                   raw_close=None, up_limit=None, down_limit=None,
                   seg_by_date: dict | None = None, seg_threshold: float | None = None,
                   start_index: int | None = None) -> dict:
    """单股 OHLCV → {ISO日期: 产物行 dict} (契约 §5 全字段, 含 E 轴 B1 消费值)。

    - trading_days: 交易日历 (周期闭合真相源, H1);
    - raw_close/up_limit/down_limit: 原始价空间触板输入 (缺 → buyable/sellable/is_one_word=None,
      不知道 != False, stk_limit data_start=2022 前的日期即此语义);
    - seg_by_date: {ISO日期: {rv_pctile, vol_regime}} (B1 E 轴消费; 缺 → axis_volregime=None);
    - start_index: 增量优化 — 只保证 >= start_index 的 bar 输出 (内部回溯 前序需求 根 bar
      供 context/突破底盘, 输出值与全量逐 bit 一致)。
    """
    cfg = cfg or load_config()
    lab = lab or Labeler(cfg)
    tf = cfg["timeframes"]
    prior_need = max(int(cfg["上下文"]["前序窗口"]), int(cfg["突破"]["底盘统计上限"]))
    daily_start = None if start_index is None else max(0, int(start_index) - prior_need)

    daily = features.compute(dates, o, h, l, c, v, windows=tf["daily"]["windows"],
                             warmup=tf["daily"]["warmup"], bench_close=bench_close,
                             start_index=daily_start)
    if raw_close is not None:
        flags = limits.compute_limit_flags(dates, o, h, l, c, raw_close, up_limit, down_limit,
                                           price_tol=float(cfg["涨跌停"]["价格容差"]))
        limits.enrich_features(daily, flags, cfg)
    else:
        flags = {}

    rows = lab.classify_frame(daily)
    lab.apply_context(rows, daily)
    events = breakout.detect(dates, h, c, daily, rows, flags, cfg)

    wk = features.compute(dates, o, h, l, c, v, windows=tf["weekly"]["windows"],
                          warmup=tf["weekly"]["warmup"], resample_rule=tf["weekly"]["resample"],
                          trading_days=trading_days)
    mo = features.compute(dates, o, h, l, c, v, windows=tf["monthly"]["windows"],
                          warmup=tf["monthly"]["warmup"], resample_rule=tf["monthly"]["resample"],
                          trading_days=trading_days)
    wk_rows = lab.classify_frame(wk)
    mo_rows = lab.classify_frame(mo)
    wk_keys = sorted(wk_rows)
    mo_keys = sorted(mo_rows)

    seg_by_date = seg_by_date or {}
    out = {}
    for k in sorted(rows):
        r = rows[k]
        ax = r["axes"]
        lim = flags.get(k) or {}
        seg = seg_by_date.get(k) or {}
        vr, rvp = seg.get("vol_regime"), seg.get("rv_pctile")
        if vr is not None and rvp is not None and seg_threshold is not None:
            # E 轴分 = 分位对阈值的边距归一 [0,1] (纯派生, 无第二阈值 — 阈值 owner=segments.yaml)
            vscore = abs(float(rvp) - seg_threshold) / max(seg_threshold, 1.0 - seg_threshold)
        else:
            vscore = None
        ev = events.get(k)
        up, down, one = lim.get("is_up_limit"), lim.get("is_down_limit"), lim.get("is_one_word")
        out[k] = {
            # *_memb 列 = ax[轴]["score"] (几何均归一 score, 非 softmax membership; 见 _DDL 上方头注)
            "axis_pos": ax["位置"]["value"], "axis_pos_memb": ax["位置"]["score"],
            "axis_trend": ax["趋势方向"]["value"], "axis_trend_memb": ax["趋势方向"]["score"],
            "axis_purity": ax["趋势纯度"]["value"], "axis_purity_memb": ax["趋势纯度"]["score"],
            "axis_vol": ax["量能"]["value"], "axis_vol_memb": ax["量能"]["score"],
            "axis_volregime": vr, "axis_volregime_memb": vscore,
            "form_name": r["label"], "form_sub": r["sub"],
            "weekly_name": lab.asof_label(wk_rows, wk_keys, k),
            "monthly_name": lab.asof_label(mo_rows, mo_keys, k),
            "is_breakout_event": ev is not None,
            "base_days": (ev["base_days"] if ev else None),
            "buyable": (None if up is None else not up),
            "sellable": (None if down is None else not down),
            "is_one_word": (None if one is None else bool(one)),
        }
    return out


def _row_tuple(code: str, k: str, r: dict) -> tuple:
    return (code, _compact(k),
            r["axis_pos"], r["axis_trend"], r["axis_purity"], r["axis_vol"], r["axis_volregime"],
            r["axis_pos_memb"], r["axis_trend_memb"], r["axis_purity_memb"], r["axis_vol_memb"],
            r["axis_volregime_memb"],
            r["form_name"], r["form_sub"], r["weekly_name"], r["monthly_name"],
            r["is_breakout_event"], r["base_days"], r["buyable"], r["sellable"], r["is_one_word"])


# ---------------------------------------------------------------- 构建
# Default: accepted canonical preferred; legacy raw fills nominal close gaps.
_SRC_TEMP_SQL = """
CREATE OR REPLACE TEMP TABLE _b2_src AS
SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume,
       COALESCE(rd.close, can.close) AS raw_close, sl.up_limit, sl.down_limit,
       seg.rv_pctile, seg.vol_regime
FROM mkt.price_kline_qfq_tushare k
LEFT JOIN tr.raw_tushare_daily rd
  ON substr(rd.ts_code, 1, 6) = k.code AND rd.trade_date = replace(k.date, '-', '')
LEFT JOIN tr.canonical_nominal_ohlcv_daily can
  ON substr(can.ts_code, 1, 6) = k.code AND can.trade_date = CAST(k.date AS DATE)
LEFT JOIN tr.raw_tushare_stk_limit sl
  ON substr(sl.ts_code, 1, 6) = k.code AND sl.trade_date = replace(k.date, '-', '')
LEFT JOIN dim_stock_segment_daily seg
  ON seg.stock_code = k.code AND seg.trade_date = replace(k.date, '-', '')
WHERE k.date >= ?
"""

# S5: accepted-only nominal close — no legacy raw_tushare_daily fill.
# stk_limit stays a separate domain input (parallel to qfq adj_factor).
_SRC_TEMP_SQL_FROM_ACCEPTED = """
CREATE OR REPLACE TEMP TABLE _b2_src AS
SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume,
       can.close AS raw_close, sl.up_limit, sl.down_limit,
       seg.rv_pctile, seg.vol_regime
FROM mkt.price_kline_qfq_tushare k
LEFT JOIN tr.canonical_nominal_ohlcv_daily can
  ON substr(can.ts_code, 1, 6) = k.code AND can.trade_date = CAST(k.date AS DATE)
LEFT JOIN tr.raw_tushare_stk_limit sl
  ON substr(sl.ts_code, 1, 6) = k.code AND sl.trade_date = replace(k.date, '-', '')
LEFT JOIN dim_stock_segment_daily seg
  ON seg.stock_code = k.code AND seg.trade_date = replace(k.date, '-', '')
WHERE k.date >= ?
"""


def src_temp_sql(*, from_accepted: bool = False) -> str:
    """Return form source SQL; ``from_accepted`` skips legacy raw daily fill.

    Library default stays fill-compatible for pipeline/tests; S7
    ``chunkyctl derive form`` defaults to ``from_accepted=True`` via derive_runtime.
    """

    if from_accepted:
        return _SRC_TEMP_SQL_FROM_ACCEPTED
    return _SRC_TEMP_SQL



def _process_codes(con, codes: list[str], cal: list[str], bench: dict, cfg: dict, lab: Labeler,
                   seg_thr: float, wanted_by_code: dict | None) -> int:
    """从 _b2_src 分批取源数据 → 分类 → 插入。wanted_by_code=None 时全量输出。"""
    total = 0
    for lo in range(0, len(codes), _CODE_CHUNK):
        chunk = codes[lo:lo + _CODE_CHUNK]
        ph = ",".join("?" for _ in chunk)
        rows = con.execute(
            f"SELECT * FROM _b2_src WHERE code IN ({ph}) ORDER BY code, date", chunk).fetchall()
        by_code: dict[str, list] = {}
        for r in rows:
            by_code.setdefault(r[0], []).append(r)
        batch = []
        for code, rs in by_code.items():
            dates = [str(r[1])[:10] for r in rs]
            o = [r[2] for r in rs]; h = [r[3] for r in rs]
            l = [r[4] for r in rs]; c = [r[5] for r in rs]; v = [r[6] for r in rs]
            raw_c = [r[7] for r in rs]; ul = [r[8] for r in rs]; dl = [r[9] for r in rs]
            seg_by_date = {dates[i]: {"rv_pctile": rs[i][10], "vol_regime": rs[i][11]}
                           for i in range(len(rs)) if rs[i][11] is not None}
            wanted = None
            start_index = None
            if wanted_by_code is not None:
                wanted = wanted_by_code.get(code)
                if not wanted:
                    continue
                idxs = [i for i, d in enumerate(dates) if d in wanted]
                if not idxs:
                    continue
                start_index = min(idxs)
            out = classify_stock(dates, o, h, l, c, v, trading_days=cal, cfg=cfg, lab=lab,
                                 bench_close=bench, raw_close=raw_c, up_limit=ul, down_limit=dl,
                                 seg_by_date=seg_by_date, seg_threshold=seg_thr,
                                 start_index=start_index)
            for k, r in out.items():
                if wanted is not None and k not in wanted:
                    continue
                batch.append(_row_tuple(code, k, r))
        if batch:
            con.executemany(_INSERT, batch)
            total += len(batch)
        logger.info("[technical_states] processed %d/%d codes (+%d rows)",
                    min(lo + _CODE_CHUNK, len(codes)), len(codes), len(batch))
    return total


def rebuild_all(
    conn=None, cfg: dict | None = None, *, from_accepted: bool = False
) -> dict[str, Any]:
    """全量重建 fact_stock_form_daily (data_start 起)。conn=None 自管连接并 ATTACH mkt/tr/ref;
    注入 conn (测试) 时调用方负责 mkt./tr./ref. 与 dim_stock_segment_daily 可解析。
    ``from_accepted`` (S5/S7 derive path): nominal close from canonical only.
    """
    cfg = cfg or load_config()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach(con)
        _assert_b1_ready(con)
        iso_start = _iso(str(cfg["data_start"]))
        cal = _trading_days(con)
        bench = _bench_close(con, cfg)
        lab = Labeler(cfg)
        seg_thr = vol_regime_threshold()
        con.execute(f"DROP TABLE IF EXISTS {TABLE}")
        con.execute(_DDL)
        con.execute(src_temp_sql(from_accepted=from_accepted), [iso_start])
        codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM _b2_src ORDER BY 1").fetchall()]
        total = _process_codes(con, codes, cal, bench, cfg, lab, seg_thr, wanted_by_code=None)
        con.execute("DROP TABLE IF EXISTS _b2_src")
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_form_code_date ON {TABLE}(stock_code, trade_date)")
        n, days = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM {TABLE}").fetchone()
        if own:
            con.execute("CHECKPOINT")
        out = {
            "rows": n,
            "days": days,
            "codes": len(codes),
            "from_accepted": bool(from_accepted),
        }
        logger.info("[technical_states] rebuild_all: %s", out)
        return out
    finally:
        if own:
            con.close()


def build_latest(
    conn=None, cfg: dict | None = None, *, from_accepted: bool = False
) -> dict[str, Any]:
    """增量: 补 K线已有而 form 表缺的日期 (幂等; pipeline process 步在 segments 之后每日调)。

    切片 = 交易日历回溯 incremental_lookback_days (覆盖月线 warmup + context/突破前序需求),
    特征窗口全在切片内 → 增量行与全量重建逐 bit 一致 (确定性, 单测证伪门)。
    ``from_accepted`` (S5/S7 derive path): nominal close from canonical only.
    """
    cfg = cfg or load_config()
    own = conn is None
    con = conn or duck_connect(_db("smartmoney"), read_only=False)
    try:
        if own:
            _attach(con)
        have = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?", [TABLE]).fetchall()}
        if TABLE not in have:
            return {
                "mode": "rebuild",
                **rebuild_all(conn=con, cfg=cfg, from_accepted=from_accepted),
            }
        _assert_b1_ready(con)
        iso_start = _iso(str(cfg["data_start"]))
        # watermark 语义 (非 NOT IN 全集): warmup 前缀日期在 form 表永远缺失, NOT IN 会把
        # 它们每次都算 missing (单测抓过 251 天噪音)。K 线中间历史回补不走增量, 走 rebuild_all。
        missing = [str(r[0])[:10] for r in con.execute(f"""
            SELECT DISTINCT date FROM mkt.price_kline_qfq_tushare
            WHERE date >= ? AND replace(date, '-', '') > (
                SELECT COALESCE(MAX(trade_date), '') FROM {TABLE})
            ORDER BY 1""", [iso_start]).fetchall()]
        if not missing:
            return {"added_days": 0, "rows": 0, "from_accepted": bool(from_accepted)}
        cal = _trading_days(con)
        lookback = int(cfg["incremental_lookback_days"])
        pos = bisect_left(cal, missing[0])
        cutoff = cal[max(0, pos - lookback)]
        bench = _bench_close(con, cfg)
        lab = Labeler(cfg)
        seg_thr = vol_regime_threshold()
        con.execute(src_temp_sql(from_accepted=from_accepted), [max(cutoff, iso_start)])
        ph = ",".join("?" for _ in missing)
        wanted_rows = con.execute(
            f"SELECT DISTINCT code, date FROM _b2_src WHERE date IN ({ph})", missing).fetchall()
        wanted_by_code: dict[str, set] = {}
        for r in wanted_rows:
            wanted_by_code.setdefault(r[0], set()).add(str(r[1])[:10])
        codes = sorted(wanted_by_code)
        total = _process_codes(con, codes, cal, bench, cfg, lab, seg_thr, wanted_by_code=wanted_by_code)
        con.execute("DROP TABLE IF EXISTS _b2_src")
        con.commit()
        out = {
            "added_days": len(missing),
            "rows": total,
            "from_accepted": bool(from_accepted),
        }
        logger.info("[technical_states] build_latest: %s", out)
        return out
    finally:
        if own:
            con.close()
