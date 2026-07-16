"""SERVE 读侧统一层 — owner=docs/MASTER_TOPLEVEL_DESIGN.md §3.1/§5.1。

唯一取数 + PIT 执行 + 口径清洗点。消费者 (dossier/feature_panel/选股/实验) **全走 DataAccess.get**,
禁内联 FROM raw_* (check_serve_read_layer D1) / 禁自写 asof (check_serve_read_layer D2)。

四不变量落地: #1 统一主键+PIT (asof/cleaner) · #2 读写边界 (resolver read_only) ·
#4 单概念单真相源 (data_access.yaml 唯一声明)。血缘: get() 返回带 provenance 信封。

薄分发器 (本体零业务): 读 spec → 选 driver → fetch → 包 DataResult。加 entity = 加 yaml 条目
(+ 复杂的加 driver), 本体不改 = 防 god-module (撞 db.py 反例)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .drivers import get_driver
from .spec import AccessRegistry, load_registry


@dataclass
class DataResult:
    """取数结果 + 血缘携带面 (provenance per-entity, 非 per-row)。

    rows: 归一后的 dict 行 (code 6位, asof_col ISO)。
    provenance: {source_entity, source_table, layer, vendor, as_of, asof_anchor, available_after,
                 compute_fn, taxonomy_version} — 追溯"这数据从哪来"的声明链一环。
    """
    rows: list[dict[str, Any]]
    provenance: dict[str, Any]

    def by_code(self) -> dict[str, list[dict]]:
        """按 code 分组 (消费侧常用形态)。"""
        out: dict[str, list[dict]] = {}
        for r in self.rows:
            out.setdefault(r.get("code") or r.get("ts_code"), []).append(r)
        return out


class DataAccess:
    """读层入口。get(entity, codes, start, as_of) -> DataResult。"""

    def __init__(self, registry: AccessRegistry | None = None, registry_path: str | Path | None = None):
        self._reg = registry or load_registry(registry_path)

    def entity_names(self) -> list[str]:
        return sorted(self._reg.entities)

    def distinct_codes(self, entity: str, limit: int = 0, conn=None) -> list[str]:
        """entity 的去重 code 清单 (扫描/选股枚举用; 按 code 序; ts_from_plain 归一 6 位)。"""
        from .keys import ts_code_to_code
        from . import resolver
        spec = self._reg.entity(entity)
        own = conn is None
        c = conn or resolver.connect_ro(spec.db)
        try:
            sql = f"SELECT DISTINCT {spec.code_col} FROM {spec.table} ORDER BY {spec.code_col}"
            if limit:
                sql += f" LIMIT {int(limit)}"   # rule-compliance: ok evidence=扫描股数上限(运行时传入, 非策略阈值)
            rows = c.execute(sql).fetchall()
        finally:
            if own:
                c.close()
        if spec.code_mode == "ts_from_plain":
            return [ts_code_to_code(r[0]) for r in rows]
        return [r[0] for r in rows]

    def coverage_start(self, entity: str, conn=None) -> str | None:
        """entity 数据覆盖最早 asof 锚日期 (MIN(asof_col)) — 元数据原语, 非 PIT 行读。
        供消费侧判 '事件早于数据覆盖起点则跳过' (coverage gate, 非行级 PIT)。
        无 asof_col / 空表 → None。保 SERVE 单一读路 (不变量4): 消费侧不再内联 MIN FROM raw_。"""
        from . import resolver
        spec = self._reg.entity(entity)
        if not getattr(spec, "asof_col", None):
            return None
        own = conn is None
        c = conn or resolver.connect_ro(spec.db)
        try:
            row = c.execute(f"SELECT MIN({spec.asof_col}) FROM {spec.table}").fetchone()
        finally:
            if own:
                c.close()
        return row[0] if row and row[0] is not None else None

    def get(self, entity: str, codes=None, start: str | None = None,
            as_of: str | None = None, conn=None) -> DataResult:
        """取一个 entity 的数据 (PIT asof≤t, 口径已清洗, 带血缘)。

        codes: 6 位 code 列表 (None=全市场); start/as_of: ISO 决策日 (asof≤t PIT 强制)。
        conn: 可注入 (测试 :memory:); 缺省读层 read_only 开对应库。
        """
        spec = self._reg.entity(entity)
        # PIT 默认锚 (2026-06-22 P0-3 闭合 conformance 审计抓出的 fail-silent 漏洞): 时序 entity
        # (有 asof_col) as_of=None 时旧行为 = 不加任何 cutoff 静默返全史 (backtest 误用→未来行泄漏)。
        # 改: 默认最新完成交易日 (交易日历真相源, 非 wall-clock), 即"as of 最新完成交易日"明确 PIT 点。
        # conn is None = 生产读路 (dossier/serving); 注入 conn (测试/特殊上下文) 由调用方自控 as_of。
        if as_of is None and conn is None and getattr(spec, "asof_col", None):
            from services.calendar import latest_closed_or_raise
            as_of = latest_closed_or_raise()
        driver = get_driver(spec)
        rows = driver.fetch(spec, codes=codes, start=start, as_of=as_of, conn=conn)
        return DataResult(rows=rows, provenance=spec.provenance(as_of))


_DEFAULT: DataAccess | None = None


def get_data_access() -> DataAccess:
    """进程级单例 (registry 载入一次)。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DataAccess()
    return _DEFAULT


__all__ = ["DataAccess", "DataResult", "get_data_access"]
