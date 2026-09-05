"""判例引擎信号层: 把「一条人手写的公式」变成「哪些股票日是它的触发点」。

**策略在这层唯一的贡献就是这张表。** 收益、基线、市场环境全部在 outcome.py 那层算好了,
与策略无关 —— 所以加一条新策略不需要重算任何基线, 也不可能算出一个和别人不同口径的收益。

产物 (feature_store, writer 唯一 = 本模块):
  casebook_case(strategy_id, code, date, i)   一行 = 一次触发

公式求值走 `services.formula_challenge.load_formula_engine()`, 它先校验 bestchoice
冻结包的 sha256 再加载 —— 不直接 import bestchoice。公式源码 hash 的单一真相源在那里,
不在 casebook.yaml 里重复登记。

不做的事 (goal.md 边界段): 不建通达信文本求值器 —— 公式在对话里转换成 Python 后再进系统。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.formula_challenge import load_formula_engine

_CONFIG = Path(__file__).resolve().parents[3] / "backend" / "config" / "casebook.yaml"

_LEGAL_KIND = {"frozen_formula"}
_LEGAL_KEYS = {"kind", "formula_id", "params", "source_ref"}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    kind: str
    formula_id: str
    params: dict[str, Any] | None
    source_ref: str


def load_strategies(path: Path | None = None) -> tuple[StrategySpec, ...]:
    """读策略登记, fail closed。

    未知 kind / 未知键 / 缺 formula_id 一律抛 —— 一条登记错了, 它产出的判例全是错的,
    而错的判例长得和对的一模一样。
    """
    src = path or _CONFIG
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    entries = (raw or {}).get("strategies")
    if not isinstance(entries, dict):
        raise ValueError(f"{src}: strategies 必须是 mapping")

    out: list[StrategySpec] = []
    for sid, spec in entries.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{src}: strategies.{sid} 必须是 mapping")
        unknown = set(spec) - _LEGAL_KEYS
        if unknown:
            raise ValueError(
                f"{src}: strategies.{sid} 未知键 {sorted(unknown)} —— 闭合键集, 加键要先改 loader"
            )
        kind = spec.get("kind")
        if kind not in _LEGAL_KIND:
            raise ValueError(f"{src}: strategies.{sid}.kind={kind!r} 不在 {sorted(_LEGAL_KIND)}")
        fid = spec.get("formula_id")
        if not isinstance(fid, str) or not fid:
            raise ValueError(f"{src}: strategies.{sid} 缺 formula_id")
        params = spec.get("params")
        if params is not None and not isinstance(params, dict):
            raise ValueError(f"{src}: strategies.{sid}.params 必须是 mapping 或 null")
        out.append(
            StrategySpec(
                strategy_id=str(sid),
                kind=kind,
                formula_id=fid,
                params=params,
                source_ref=str(spec.get("source_ref") or ""),
            )
        )
    return tuple(out)


_BAR_COLS = ("open", "high", "low", "close", "volume", "amount")


def _load_bars(conn: Any) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """一次取全市场 K 线, 按 code 切段。

    逐股单独查是 5,447 次往返; 一次取回再切段是一次。K 线本来就要全读, 没有省的余地。
    """
    # 走 conn.raw —— duck_adapter 的 DuckCursor 只有 DB-API 取法, 850 万行逐行转 Row
    # 是纯浪费。raw 是 adapter 自己为「需要高级功能的模块」留的口子。
    df = conn.raw.execute(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM mkt.v_price_kline_qfq ORDER BY code, date"
    ).df()
    codes = df["code"].to_numpy()
    dates = df["date"].to_numpy()
    cols = {c: df[c].to_numpy(dtype=float) for c in _BAR_COLS}
    return codes, dates, cols


def _segments(codes: np.ndarray) -> list[tuple[str, int, int]]:
    """codes 已按 code 排序 ⇒ 变化点即分段边界。返回 [(code, start, end_exclusive)]。"""
    if codes.size == 0:
        return []
    change = np.flatnonzero(codes[1:] != codes[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [codes.size]))
    return [(str(codes[s]), int(s), int(e)) for s, e in zip(starts, ends)]


def build(*, strategies: tuple[StrategySpec, ...] | None = None) -> dict[str, Any]:
    """全量重建 casebook_case。

    每条策略独立: 新增一条策略只需算它自己那份, 基线表一行不动 (§自动纳入)。
    """
    specs = strategies if strategies is not None else load_strategies()
    if not specs:
        raise ValueError("casebook.yaml.strategies 为空 —— 没有策略就没有判例可查")

    engine = load_formula_engine()
    manifest = get_database_manifest()
    conn = duck_connect(str(manifest.path_for("feature_store")), read_only=False)
    per_strategy: dict[str, int] = {}
    try:
        conn.execute(f"ATTACH '{manifest.path_for('market')}' AS mkt (READ_ONLY)")
        codes, dates, cols = _load_bars(conn)
        segs = _segments(codes)

        conn.execute("""
CREATE OR REPLACE TABLE casebook_case (
  strategy_id VARCHAR NOT NULL,
  code        VARCHAR NOT NULL,
  date        VARCHAR NOT NULL,
  i           BIGINT  NOT NULL
)
""")
        for spec in specs:
            rows: list[tuple[str, str, str, int]] = []
            for code, s, e in segs:
                res = engine.compute_formula_signals(
                    spec.formula_id,
                    open_=cols["open"][s:e],
                    high=cols["high"][s:e],
                    low=cols["low"][s:e],
                    close=cols["close"][s:e],
                    volume=cols["volume"][s:e],
                    amount=cols["amount"][s:e],
                    params=spec.params,
                )
                hits = np.flatnonzero(np.asarray(res["entry"], dtype=bool))
                for j in hits:
                    rows.append((spec.strategy_id, code, str(dates[s + j]), int(j) + 1))
            if rows:
                conn.executemany(
                    "INSERT INTO casebook_case VALUES (?, ?, ?, ?)", rows
                )
            per_strategy[spec.strategy_id] = len(rows)

        total = conn.execute("SELECT count(*) FROM casebook_case").fetchone()[0]
    finally:
        conn.close()

    return {"strategies": per_strategy, "total_cases": total, "stocks_scanned": len(segs)}
