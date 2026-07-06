"""watermark 推进与失败记录解耦单测 (2026-07-06 全面数据审计根因根治).

背景: _record_outcome 此前用同一个 `ok` 字段同时决定"要不要推进 watermark"和"要不要记
failure_queue"——range 内任一批失败 (哪怕只是一个历史日的 suspicious_empty/below_min_rows),
整个域的 watermark 时间戳就冻结不动, 即便 last_date 已经正确前移到本轮真正成功写到的
最新日期。实测 stk_factor_pro 冻结 17 天 / block_trade 曾冻结 9.5 个月, 根因都是这个:
只要该域某个(通常是历史)批次持续失败, 后续每次跑批哪怕新日子都写成功了, watermark 也
永远推不动——冻结的是"监控信号"本身。

本门锁定: (1) 部分失败但 last_date 有前移时, watermark 必须照常推进; (2) 同时仍要记录
这轮的失败 (不能假装全清, 不 resolve 掉历史失败记录); (3) 完全失败 (last_date=None) 时
watermark 不该凭空产生一个 None 日期。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


def _watermark_row(conn, domain_key):
    row = conn.execute(
        "SELECT last_data_date, row_count FROM mart_data_source_watermark WHERE data_domain = ?",
        [domain_key],
    ).fetchone()
    return row


def test_record_outcome_advances_watermark_on_partial_failure(monkeypatch):
    """核心红线: ok=False (本轮存在失败批) 但 last_date 已前移时, watermark 必须推进到
    last_date, 不能因为另一个不相关批次失败就整体冻结。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "stk_factor_pro_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date="20260706", rows=5200,
                        error='{"still_failed": ["20260101"]}')

    row = _watermark_row(c, "sync:stk_factor_pro_probe")
    assert row is not None, "watermark 必须被推进, 不能因 ok=False 就整体跳过"
    assert row[0] == "20260706", f"watermark 必须前移到本轮真实写到的最新日期, 实得 {row}"
    c.close()


def test_record_outcome_does_not_resolve_failures_on_partial_success(monkeypatch):
    """部分成功时不应清除历史失败记录 (真失败还在, 不能假装解决了)——failure_queue
    里这个域应仍有未 resolve 的记录。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "block_trade_probe", "source": "tushare"}
    # 先记一次失败 (模拟历史失败记录已存在)
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")
    # 本轮: 部分成功 (last_date 前移了, 但仍有失败批)
    sr._record_outcome(spec, ok=False, last_date="20260706", rows=100, error='{"still_failed": ["20250917"]}')

    row = _watermark_row(c, "sync:block_trade_probe")
    assert row[0] == "20260706", "watermark 应推进到本轮真实前移到的日期"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain = ? AND status != 'resolved'",
        ["sync:block_trade_probe"],
    ).fetchone()[0]
    assert open_failures > 0, "仍有失败批时不应把历史失败记录 resolve 掉"
    c.close()


def test_record_outcome_full_success_advances_and_resolves(monkeypatch):
    """全清 (ok=True) 时: watermark 推进 + 历史失败记录被 resolve (回归防护, 不能因为
    本次修复反而破坏了原本干净的路径)。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "daily_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")
    sr._record_outcome(spec, ok=True, last_date="20260706", rows=5200, error=None)

    row = _watermark_row(c, "sync:daily_probe")
    assert row[0] == "20260706"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain = ? AND status != 'resolved'",
        ["sync:daily_probe"],
    ).fetchone()[0]
    assert open_failures == 0, "全清后历史失败记录必须被 resolve"
    c.close()


def test_record_outcome_total_failure_no_last_date_does_not_fabricate_watermark(monkeypatch):
    """完全失败 (last_date=None, 一行都没写成) 时不应产生一条 last_data_date=None 的
    watermark 记录——没有真实前移就不该有 upsert 动作。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "moneyflow_hsgt_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")

    row = _watermark_row(c, "sync:moneyflow_hsgt_probe")
    assert row is None, "完全失败时不该凭空产生 watermark 记录"
    c.close()
