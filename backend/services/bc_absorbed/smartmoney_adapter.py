"""Wave B: SmartMoney Data Adapter — 给 22 个 bank 公式喂外部数据.

独立模块. 从 smartmoney.duckdb 提取 per-stock-date 数据, 对齐到 OHLCV 日期序列,
返回 numpy array 供 bank 公式消费. 公式不直接查 DB.

数据覆盖审计 (2026-05-26):
  AVAILABLE: fact_lhb_event(53K), fact_executive_trade_event(68K),
    fact_hsgt_daily(2.7K), fact_sector_momentum_daily(10K),
    raw_capital_dividend_detail(12K), v_stock_sector_momentum_daily(4.5M),
    mart_market_perception_*(168-910 rows, 短历史 2026-04 起)
  MISSING: earnings_surprise, block_trade, index_inclusion
  PIT WARNING: LHB/DZJY 用 T+2 保守延迟; Perception 仅 2026-04+ 可用

用法:
    adapter = SmartMoneyAdapter(smart_conn)
    features = adapter.load_stock_features('600036', ohlcv_dates)
    # features = {'lhb_inst_seats': np.array([...]), 'sector_ret': np.array([...]), ...}
"""
from __future__ import annotations

from typing import Any

import numpy as np


class SmartMoneyAdapter:
    def __init__(self, conn):
        self.conn = conn

    def load_stock_features(
        self,
        stock_code: str,
        ohlcv_dates: np.ndarray,
        required: list[str] | None = None,
    ) -> dict[str, np.ndarray]:
        n = len(ohlcv_dates)
        date_strs = [str(d)[:10] for d in ohlcv_dates]
        date_to_idx = {d: i for i, d in enumerate(date_strs)}
        features: dict[str, np.ndarray] = {}
        loaders = {
            "lhb_inst_seats": self._load_lhb,
            "insider_buy_count": self._load_exec_trade,
            "hsgt_net": self._load_hsgt,
            "ex_dividend_flag": self._load_dividend,
            "sector_ret": self._load_sector_momentum,
            "diffusion_score": self._load_perception_leader_follower,
            "under_reaction_score": self._load_perception_under_reaction,
            "context_score": self._load_perception_stock_context,
        }
        targets = required or list(loaders.keys())
        for feat_name in targets:
            loader = loaders.get(feat_name)
            if loader is None:
                features[feat_name] = np.zeros(n, dtype=np.float64)
                continue
            try:
                features[feat_name] = loader(stock_code, date_strs, date_to_idx, n)
            except Exception:
                features[feat_name] = np.zeros(n, dtype=np.float64)
        return features

    def _load_lhb(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        # PIT: T+2 conservative delay (LHB announced T+1 evening)
        out = np.zeros(n, dtype=np.float64)
        rows = self.conn.execute(
            "SELECT trade_date, n_rank_reasons FROM fact_lhb_event WHERE stock_code=? ORDER BY trade_date",
            [code],
        ).fetchall()
        for trade_date, count in rows:
            d = str(trade_date)[:10]
            idx = d2i.get(d)
            if idx is not None and idx + 2 < n:
                out[idx + 2] = float(count)  # T+2 PIT delay
        return out

    def _load_exec_trade(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        rows = self.conn.execute(
            "SELECT notice_date, n_shareholders, total_change_pct_total "
            "FROM fact_executive_trade_event WHERE stock_code=? AND direction='buy' ORDER BY notice_date",
            [code],
        ).fetchall()
        for notice_date, n_sh, pct in rows:
            d = str(notice_date)[:10]
            idx = d2i.get(d)
            if idx is not None and idx + 1 < n:
                out[idx + 1] = float(n_sh or 1)  # T+1 PIT delay
        return out

    def _load_hsgt(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        rows = self.conn.execute(
            "SELECT snapshot_date, hold_pct_of_float FROM fact_hsgt_daily WHERE stock_code=? ORDER BY snapshot_date",
            [code],
        ).fetchall()
        prev_pct = 0.0
        for snap_date, pct in rows:
            d = str(snap_date)[:10]
            idx = d2i.get(d)
            if idx is not None:
                out[idx] = float(pct or 0) - prev_pct
                prev_pct = float(pct or 0)
        return out

    def _load_dividend(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        rows = self.conn.execute(
            "SELECT notice_date FROM raw_capital_dividend_detail WHERE stock_code=? AND progress LIKE '%实施%'",
            [code],
        ).fetchall()
        for (notice_date,) in rows:
            d = str(notice_date)[:10]
            idx = d2i.get(d)
            if idx is not None:
                out[idx] = 1.0
        return out

    def _load_sector_momentum(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        rows = self.conn.execute(
            "SELECT trade_date, ret_60d FROM v_stock_sector_momentum_daily WHERE stock_code=? ORDER BY trade_date",
            [code],
        ).fetchall()
        for trade_date, ret in rows:
            d = str(trade_date)[:10]
            idx = d2i.get(d)
            if idx is not None:
                out[idx] = float(ret or 0)
        return out

    def _load_perception_leader_follower(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        try:
            rows = self.conn.execute(
                "SELECT snapshot_date, diffusion_score FROM mart_market_perception_leader_follower_daily "
                "WHERE stock_code=? ORDER BY snapshot_date",
                [code],
            ).fetchall()
            for snap_date, score in rows:
                d = str(snap_date)[:10]
                idx = d2i.get(d)
                if idx is not None:
                    out[idx] = float(score or 0)
        except Exception:
            pass
        return out

    def _load_perception_under_reaction(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        try:
            rows = self.conn.execute(
                "SELECT snapshot_date, under_reaction_score FROM mart_market_perception_under_reaction_daily "
                "WHERE stock_code=? ORDER BY snapshot_date",
                [code],
            ).fetchall()
            for snap_date, score in rows:
                d = str(snap_date)[:10]
                idx = d2i.get(d)
                if idx is not None:
                    out[idx] = float(score or 0)
        except Exception:
            pass
        return out

    def _load_perception_stock_context(self, code: str, dates: list[str], d2i: dict, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        try:
            rows = self.conn.execute(
                "SELECT snapshot_date, context_quality_score FROM mart_market_perception_stock_context_daily "
                "WHERE stock_code=? ORDER BY snapshot_date",
                [code],
            ).fetchall()
            for snap_date, score in rows:
                d = str(snap_date)[:10]
                idx = d2i.get(d)
                if idx is not None:
                    out[idx] = float(score or 0)
        except Exception:
            pass
        return out

    def coverage_report(self) -> dict[str, dict]:
        """Report data coverage per feature source."""
        checks = {}
        queries = {
            "lhb": "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM fact_lhb_event",
            "exec_trade": "SELECT COUNT(*), MIN(notice_date), MAX(notice_date) FROM fact_executive_trade_event",
            "hsgt": "SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) FROM fact_hsgt_daily",
            "dividend": "SELECT COUNT(*), MIN(notice_date), MAX(notice_date) FROM raw_capital_dividend_detail",
            "sector_momentum": "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM v_stock_sector_momentum_daily",
            "perception_lf": "SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) FROM mart_market_perception_leader_follower_daily",
            "perception_ur": "SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) FROM mart_market_perception_under_reaction_daily",
            "perception_ctx": "SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) FROM mart_market_perception_stock_context_daily",
        }
        for name, sql in queries.items():
            try:
                r = self.conn.execute(sql).fetchone()
                checks[name] = {"rows": r[0], "min_date": str(r[1]), "max_date": str(r[2]), "status": "OK" if r[0] > 100 else "LOW"}
            except Exception as e:
                checks[name] = {"rows": 0, "status": f"ERROR: {e}"}
        return checks
