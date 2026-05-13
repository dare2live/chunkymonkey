"""Phase ε++ — fact_signal_context (每日每股触发上下文 5 维特征)。

借鉴 bestchoice MACD Optuna 寻优的设计:
  - 任何形态/公式触发时, 同时记录股票 5 维上下文
  - 后续 backtest 可按这些维度分桶, 找最佳组合 (vol放量+低位+stage_2 等)

5 维特征 (per stock × per day):
  - vol_r20:        当日量 / 20 日均量            (量比, 放量倍数)
  - amt_r20:        当日额 / 20 日均额            (换手率代理)
  - price_pos_60d:  close / 近 60 日最高          (1=新高, <0.7=低位)
  - price_pos_120d: close / 近 120 日最高         (中长期位置)
  - technical_stage: 1/1.5/2/3/4 (Stan Weinstein)  (来自 fact_stock_technical_stage)

为啥分独立表 (不是塞 fact_technical_trigger):
  - 上下文跟具体公式无关 (任何触发都用同一上下文)
  - 全市场每股每日 1 行 (~5500 × 800 ≈ 440 万行), 不能塞 trigger 每行
  - 后续公式回测 SQL JOIN fact_signal_context 即可
"""
from __future__ import annotations


FACT_SIGNAL_CONTEXT_DDL = """
CREATE TABLE IF NOT EXISTS fact_signal_context (
    stock_code        TEXT NOT NULL,
    date              TEXT NOT NULL,
    -- 量能/资金
    vol_r20           REAL,                 -- 量比 (今日量 / 20 日均量)
    amt_r20           REAL,                 -- 额比 (今日额 / 20 日均额)
    amount_20d_avg    REAL,                 -- 20 日均成交额 (流动性绝对量)
    -- 价格位置
    price_pos_60d     REAL,                 -- close / 近 60 日 high
    price_pos_120d    REAL,                 -- close / 近 120 日 high
    drawdown_60d      REAL,                 -- (close - max60) / max60 (≤0)
    -- 阶段
    technical_stage   TEXT,                 -- 1/1.5/2/3/4
    -- 元
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fsc_date  ON fact_signal_context(date);
CREATE INDEX IF NOT EXISTS idx_fsc_stage ON fact_signal_context(technical_stage);
"""


def ensure_signal_context_table(conn) -> None:
    conn.executescript(FACT_SIGNAL_CONTEXT_DDL)
    conn.commit()
