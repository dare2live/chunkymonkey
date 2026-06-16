"""build_segment_panel — F0 形态/分层面板物化 (MASTER §5 L1-L3 segment 层)。

产出: feature_store.duckdb `fact_segment_panel` — 每股每日 PIT 形态 + 分层轴。
  列 (全部 <= 当日 i, PIT 干净, 无任何 forward/outcome 列):
    stock_code, date,
    stage           复用 services.formula_engine.classify_technical_stage (Weinstein 5 态)
    range_pos       与分类器同定义的 60 周区间位置 (高位/低位轴)
    dif/dea/macd_hist/macd_above_zero   MACD 零轴态 (分层轴)
    board           板块 (代码前缀确定, 无泄漏)

源: market.duckdb `price_kline_qfq_tushare` (tushare 前复权, 2019+/5755 股)。
  *不* 读 v_price_kline_qfq —— 其底层是 price_kline_tdxhub, 只 2022+ 且有复权 glitch
  (坑库: tdxhub 2022-12-30 比亚迪 +210% 复权炸)。tushare 主源更干净 + 覆盖 2020+ 全宇宙。

forward 收益不入表: profiling 时即时 join K线算, 杜绝 outcome-as-feature 泄漏 (坑库 S3 AUC0.779 REJECT)。

用法:
  PYTHONPATH=backend python backend/scripts/build_segment_panel.py \
      --compute-start 2019-01-02 --write-start 2020-01-01
  smoke (50 股快验): 追加 --max-stocks 50
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import duckdb  # DB-boundary: 本脚本走 manifest 路径 (无硬编码 .duckdb 字面量), 与 build_stage_formula_fitness 同款只读市场 + 写 L2
import numpy as np
import pandas as pd
import yaml

from services.database_manifest import get_database_manifest
from services.formula_engine.technical_stage import classify_technical_stage, RANGE_LOOKBACK


log = logging.getLogger("build_segment_panel")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "segment_panel.yaml"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_segment_panel (
    stock_code      TEXT NOT NULL,
    date            TEXT NOT NULL,
    stage           TEXT NOT NULL,
    range_pos       DOUBLE,
    dif             DOUBLE,
    dea             DOUBLE,
    macd_hist       DOUBLE,
    macd_above_zero BOOLEAN,
    board           TEXT,
    built_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
)
"""


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """标准 EMA (alpha = 2/(span+1))。只用历史 → PIT 干净。"""
    alpha = 2.0 / (span + 1.0)
    out = np.empty(len(values), dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _macd(closes: np.ndarray, fast: int, slow: int, signal: int):
    dif = _ema(closes, fast) - _ema(closes, slow)
    dea = _ema(dif, signal)
    return dif, dea, dif - dea


def _range_pos(closes: np.ndarray, lookback: int) -> np.ndarray:
    """与 classify_technical_stage 同定义: (close - lo) / (hi - lo) over closes[i-lookback:i]。"""
    n = len(closes)
    out = np.full(n, np.nan)
    for i in range(lookback, n):
        window = closes[i - lookback:i]
        lo, hi = window.min(), window.max()
        if hi > lo:
            out[i] = (closes[i] - lo) / (hi - lo)
    return out


def _board(code: str) -> str:
    if code.startswith("68"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("60"):
        return "沪主板"
    if code.startswith("00"):
        return "深主板"
    if code.startswith(("8", "4", "9")):
        return "北交所"
    return "其他"


def build(compute_start: str, write_start: str, end: str | None, max_stocks: int) -> int:
    cfg = _load_config()
    m = cfg["macd"]
    fast, slow, signal = int(m["fast"]), int(m["slow"]), int(m["signal"])

    manifest = get_database_manifest()
    market_path = str(manifest.path_for("market"))
    fs_path = str(manifest.path_for("feature_store"))

    mkt = duckdb.connect(market_path, read_only=True)  # rule-compliance: ok evidence=build脚本需fetchnumpy批量读; manifest路径; duckdb_connect_policy allowlist
    if end is None:
        end = str(mkt.execute("SELECT MAX(date) FROM price_kline_qfq_tushare").fetchone()[0])
    log.info("窗口 compute_start=%s write_start=%s end=%s", compute_start, write_start, end)

    t0 = time.time()
    arr = mkt.execute(
        """
        SELECT code, date, close, volume
          FROM price_kline_qfq_tushare
         WHERE date >= ? AND date <= ?
         ORDER BY code, date
        """,
        [compute_start, end],
    ).fetchnumpy()
    mkt.close()
    codes = arr["code"]
    log.info("K线 %s 行, SQL %.1fs", f"{len(codes):,}", time.time() - t0)

    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    if max_stocks > 0:
        uniq, first, last = uniq[:max_stocks], first[:max_stocks], last[:max_stocks]
        log.info("smoke: 只跑前 %d 股", max_stocks)

    rows: list[tuple] = []
    t1 = time.time()
    for ci, code in enumerate(uniq):
        s, e = int(first[ci]), int(last[ci])
        closes = arr["close"][s:e].astype(float)
        volumes = arr["volume"][s:e].astype(float)
        dates = arr["date"][s:e]
        n = len(closes)
        if n == 0:
            continue
        stages = classify_technical_stage(closes, volumes)
        rpos = _range_pos(closes, RANGE_LOOKBACK)
        dif, dea, hist = _macd(closes, fast, slow, signal)
        board = _board(str(code))
        code_s = str(code)
        for di in range(n):
            d = str(dates[di])
            if stages[di] == "unknown" or not (write_start <= d <= end):
                continue
            rp = rpos[di]
            rows.append((
                code_s, d, str(stages[di]),
                None if np.isnan(rp) else float(rp),
                float(dif[di]), float(dea[di]), float(hist[di]),
                bool(dif[di] >= 0.0), board,
            ))
        if (ci + 1) % 1000 == 0:
            log.info("  classify %s/%s 股, 累计 %s 行", ci + 1, len(uniq), f"{len(rows):,}")
    log.info("分类完成 %.1fs, 有效行 %s", time.time() - t1, f"{len(rows):,}")

    if not rows:
        raise RuntimeError("0 行产出, 拒绝写空面板; 检查 compute_start 是否给够预热历史")

    # 列式批量插入 (DuckDB 注册 DataFrame → INSERT SELECT), 比 executemany 快百倍
    cols = ["stock_code", "date", "stage", "range_pos", "dif", "dea", "macd_hist", "macd_above_zero", "board"]
    df = pd.DataFrame(rows, columns=cols)

    fs = duckdb.connect(fs_path, read_only=False)  # rule-compliance: ok evidence=build脚本需Arrow批插+事务; manifest路径; duckdb_connect_policy allowlist
    try:
        fs.execute(_SCHEMA)
        t2 = time.time()
        fs.register("staging_segment_panel", df)
        fs.execute("BEGIN TRANSACTION")
        try:
            fs.execute("DELETE FROM fact_segment_panel WHERE date >= ? AND date <= ?", [write_start, end])
            fs.execute(
                f"INSERT INTO fact_segment_panel ({', '.join(cols)}) "
                "SELECT * FROM staging_segment_panel"
            )
            fs.execute("COMMIT")
        except BaseException:
            fs.execute("ROLLBACK")
            raise
        log.info("写入 fact_segment_panel %s 行 (%.1fs)", f"{len(rows):,}", time.time() - t2)
    finally:
        fs.close()
    return len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--compute-start", default="2019-01-02", help="滚动指标预热起点 (给 MA250/range300 喂历史)")  # rule-compliance: ok evidence=CLI默认=tushare K线最早可得日(实测MIN), 非钉死规避bug, 可命令行覆盖
    p.add_argument("--write-start", default="2020-01-01", help="写入起点 (方法论训练窗起点)")  # rule-compliance: ok evidence=CLI默认=MASTER§5方法论训练窗起点(用户"数据2020起"), 可命令行覆盖
    p.add_argument("--end", default=None, help="默认 = K线最新日")
    p.add_argument("--max-stocks", type=int, default=0, help=">0 = smoke 只跑前 N 股")
    args = p.parse_args()
    build(args.compute_start, args.write_start, args.end, args.max_stocks)


if __name__ == "__main__":
    main()
