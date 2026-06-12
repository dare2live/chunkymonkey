"""ChunkyMonkey modal 计算面 — 重计算上云 (用户决议 2026-06-12: 本地只留秒级判决).

首发函数: cyq_replay_batch — 全市场本地 CYQ 复算 (spec §2.2 三角分布+换手衰减)。
消费方: C0 PASS 后的 T3 筹码实验共享缓存 (A组C2 出货预警 / C4 底部重构 都要逐日
winner_rate 本地口径); 本地单核 5300 股×8 年估计数小时, modal 10 并发 ~20 分钟。

数据管道: 本地导出 raw 域 parquet (scripts/modal_data_push.py) → modal Volume
`chunky-data` → 函数读 volume 算 → 结果 parquet 写回 volume → 本地 pull。
不上传 DuckDB 大库 (smartmoney 19G 不现实); 只传任务需要的列级 parquet (几百 MB)。

成本纪律: 函数无 GPU (纯 numpy), CPU 容器分钟级计费; 冒烟 = 单股秒级 (分钱级)。
用法:
  modal run backend/compute/modal_app.py::smoke          # 冒烟 (合成数据, 验证管道)
  modal run backend/compute/modal_app.py::cyq_replay_all # 全市场 (需先 data_push)
"""
from __future__ import annotations

import modal

app = modal.App("chunkymonkey-compute")

IMAGE = modal.Image.debian_slim(python_version="3.12").pip_install(
    "duckdb==1.5.2", "numpy", "pandas", "pyarrow"
)
VOL = modal.Volume.from_name("chunky-data", create_if_missing=True)
DATA = "/data"

# CYQ 参考算法 (docs/chip_distribution_cyq_spec.md §2.2 原样; 与本地
# experiment_c0_cyq_audit.py._daily_winner_rates 同源 — 口径必须一字不差)
TICK = 0.01


def _daily_winner_rates(hq, lq, cq, vol_shares, float_shares, vwap_q):
    import numpy as np

    price_min = float(np.nanmin(lq)) * 0.90
    price_max = float(np.nanmax(hq)) * 1.10
    prices = np.arange(price_min, price_max + TICK, TICK)
    chips = np.zeros(len(prices))
    n = len(prices)

    def idx(p):
        return int(round((p - price_min) / TICK))

    out = np.full(len(cq), np.nan)
    for i in range(len(cq)):
        if vol_shares[i] <= 0 or not np.isfinite(vwap_q[i]):
            if chips.sum() > 0:
                out[i] = chips[: max(0, min(n, idx(cq[i])))].sum() / chips.sum() * 100
            continue
        turnover = min(vol_shares[i] / float_shares[i], 1.0)
        chips *= (1.0 - turnover)
        i_lo, i_hi = max(0, idx(lq[i])), min(n - 1, idx(hq[i]))
        if i_lo >= i_hi:
            chips[max(0, min(n - 1, idx(vwap_q[i])))] += turnover
        else:
            import numpy as _np
            i_vw = max(i_lo, min(i_hi, idx(vwap_q[i])))
            j = _np.arange(i_lo, i_hi + 1)
            left = (j - i_lo) / max(1, i_vw - i_lo)
            right = (i_hi - j) / max(1, i_hi - i_vw)
            dist = _np.where(j <= i_vw, left, right)
            s = dist.sum()
            if s > 0:
                chips[i_lo:i_hi + 1] += dist / s * turnover
        total = chips.sum()
        if total > 0:
            out[i] = chips[: max(0, min(n, idx(cq[i])))].sum() / total * 100
    return out


@app.function(image=IMAGE, volumes={DATA: VOL}, timeout=1800, cpu=2.0)
def cyq_replay_batch(codes: list[str], input_rel: str = "kline_qfq.parquet",
                     out_rel: str = "cyq_local") -> str:
    """一批股票的逐日 winner_rate 复算; 输入/输出都走 volume parquet.

    input_rel/out_rel: DATA 下相对路径 — smoke 走 smoke/ 隔离前缀, 防合成数据
    覆写真输入 / 污染真输出 (2026-06-13 同路径覆写隐患修复)。
    """
    import duckdb
    import numpy as np
    import pandas as pd

    con = duckdb.connect()  # rule-compliance: ok evidence=modal-remote-container-no-project-adapter-20260612
    results = []
    for code in codes:
        df = con.execute(
            f"SELECT * FROM '{DATA}/{input_rel}' WHERE ts_code = ? ORDER BY trade_date",
            [code],
        ).df()
        if len(df) < 300:
            continue
        w = _daily_winner_rates(
            df["high_q"].values, df["low_q"].values, df["close_q"].values,
            df["vol_shares"].values, df["float_shares"].values, df["vwap_q"].values,
        )
        results.append(pd.DataFrame({
            "ts_code": code, "trade_date": df["trade_date"], "winner_rate_local": w,
        }))
    if not results:
        return "empty"
    out = pd.concat(results, ignore_index=True)
    path = f"{DATA}/{out_rel}/{codes[0]}_{len(codes)}.parquet"
    import os
    os.makedirs(f"{DATA}/{out_rel}", exist_ok=True)
    out.to_parquet(path)
    VOL.commit()
    return f"{path}: {len(out)} rows / {len(results)} codes"


@app.function(image=IMAGE, volumes={DATA: VOL}, timeout=7200, cpu=2.0)
def cyq_replay_all(batch_size: int = 100) -> list[str]:
    """全市场调度: 按批 spawn cyq_replay_batch (10 并发由 modal 自动调度)."""
    import duckdb

    con = duckdb.connect()  # rule-compliance: ok evidence=modal-remote-container-no-project-adapter-20260612
    codes = [r[0] for r in con.execute(
        f"SELECT DISTINCT ts_code FROM '{DATA}/kline_qfq.parquet' ORDER BY 1").fetchall()]
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    return list(cyq_replay_batch.map(batches))


@app.function(image=IMAGE, volumes={DATA: VOL}, timeout=300)
def smoke() -> str:
    """管道冒烟: 合成 1 股数据走全链 (不依赖真数据上传, 分钱级)."""
    import numpy as np
    import pandas as pd

    n = 300
    rng = np.random.default_rng(42)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    df = pd.DataFrame({
        "ts_code": "TEST.SM", "trade_date": [f"2025{i:04d}" for i in range(n)],
        "high_q": close * 1.02, "low_q": close * 0.98, "close_q": close,
        "vol_shares": rng.uniform(1e6, 5e6, n), "float_shares": 1e9,
        "vwap_q": close * 1.001,
    })
    import os
    # smoke 全程走 smoke/ 隔离前缀 — 绝不写共享 kline_qfq.parquet (真数据输入),
    # 反例: 旧版 smoke 直接覆写真输入路径, 真数据上传后跑一次 smoke = 输入变 1 只合成股
    os.makedirs(f"{DATA}/smoke", exist_ok=True)
    df.to_parquet(f"{DATA}/smoke/kline_qfq.parquet")
    VOL.commit()
    r = cyq_replay_batch.remote(["TEST.SM"], input_rel="smoke/kline_qfq.parquet",
                                out_rel="smoke/cyq_local")
    w = _daily_winner_rates(df["high_q"].values, df["low_q"].values, df["close_q"].values,
                            df["vol_shares"].values, df["float_shares"].values, df["vwap_q"].values)
    import numpy as _np
    return f"pipeline OK: {r} | local-check winner_rate[-1]={_np.round(w[-1],2)}"
