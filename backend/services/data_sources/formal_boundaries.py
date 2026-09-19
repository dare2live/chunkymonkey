"""Formal adapter → landing → canonical writer boundaries.

Transport axis only.  Business tiers must not own these seams.

Each domain here names its own ``adapter``; there is no single repo-wide live
provider.  ``LIVE_ADAPTER`` is merely the *default* (tushare) that domains not
yet migrated still point at — 2026-09-01 the tushare authorization expires on
2026-09-10 and is not being renewed, so this default is being drained domain by
domain (trade_cal -> calendar_rule, daily -> tdxhub).  Disclosure paths such as
miaoxiang/aif10 write facts outside this inventory and are NONCONFORMING until
E0 formalization — see ``disclosure_boundaries`` for the E0 strangler inventory.
Accepted truth for formal domains is always the landing/canonical pair, never
the adapter response.

Domains registered here must never fall through to legacy ``_write_batch`` raw
replace/merge.  ``runtime_state`` values (including former ``writers_pending``
and current ``*_canary_pending``) are explicit migration debt tracked in
goal/ledger — not silent permanent exemptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RuntimeState = Literal[
    "retired_readonly",
    "accepted_runtime_ready_canary_pending",
    "writers_pending",
]


@dataclass(frozen=True)
class FormalDomainBoundary:
    domain: str
    adapter: str
    landing_writer: str
    canonical_writer: str
    dataset_id: str
    runtime_state: RuntimeState
    legacy_raw_write: Literal["forbidden"] = "forbidden"


# Default adapter for formal domains not yet migrated off tushare (授权 2026-09-10 到期
# 不续期)。**不是**"唯一"——每个域自己声明 adapter, trade_cal 已走 calendar_rule。
LIVE_ADAPTER = "tushare"

_FORMAL_BOUNDARIES: dict[str, FormalDomainBoundary] = {
    "margin": FormalDomainBoundary(
        domain="margin",
        adapter=LIVE_ADAPTER,
        landing_writer="services.data_sources.margin_acceptance.land_margin_batch",
        canonical_writer="services.data_sources.margin_acceptance.accept_margin_batch",
        dataset_id="tier0.market_data.margin_exchange_daily",
        runtime_state="retired_readonly",
    ),
    "trade_cal": FormalDomainBoundary(
        domain="trade_cal",
        # 2026-08-31 授权换源 (业主已明确授权): baostock -> calendar_rule。日历不再向任何供应商取数: 交易日 = 周一~周五 − 法定节假日
        # (backend/config/market_holidays.yaml), 实测 1990-2026 共 13,162 天逐字段零差异。
        # 其余三个 formal 域继续用 LIVE_ADAPTER (tushare) 不动。
        adapter="calendar_rule",
        landing_writer="services.data_sources.calendar_landing.land_calendar_batch",
        canonical_writer="services.data_sources.calendar_acceptance.accept_calendar_batch",
        dataset_id="tier0.reference.sse_trading_calendar_generation",
        runtime_state="accepted_runtime_ready_canary_pending",
    ),
    "daily": FormalDomainBoundary(
        # 2026-09-01 授权换源 tushare -> tdxhub (通达信)。全市场 5208 只 x 9 字段与 canonical
        # 逐项零差异 (实测 46872/46872 全对), 且覆盖北交所 (tushare 侧 canonical 有 BJ 339 只,
        # 通达信按 ts_code 直取可得)。写字面量不用 LIVE_ADAPTER: 后者是"尚未迁移"的默认值。
        # 2026-09-16 刀2 再授权换源 tdxhub -> fuyao (tdxhub K 线族服务端已停供, 两台主机任何
        # 参数恒返 2 字节协议错误帧); OHLCV 七列来自 fuyao dump, pre_close 来自 baostock
        # 交易所口径查询, 北交所结构上无 baostock 覆盖 (稳态 unknown, 详见
        # sources/fuyao_daily_k.py)。
        domain="daily",
        adapter="fuyao",
        landing_writer="services.data_sources.nominal_ohlcv_acceptance.land_nominal_ohlcv_batch",
        canonical_writer="services.data_sources.nominal_ohlcv_acceptance.accept_nominal_ohlcv_batch",
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        runtime_state="accepted_runtime_ready_canary_pending",
    ),
    "stock_st": FormalDomainBoundary(
        # 2026-09-01 授权换源 tushare -> stock_st_derive (本地派生, 无供应商)。
        # 2026-09-18 契约 v2 (ST 历史补数刀3): 双水库派生, 自己仍不触网——快照当天
        # 读 raw_tushare_stock_basic 简称前缀 (覆盖沪深京+停牌), 其它日期读
        # raw_baostock_daily_k 的 isST (daily 适配器顺手灌的水库, 只覆盖沪深)。
        # 原注释"baostock 被拉黑"已失效 (封禁当时即恢复, 见 project memory) 且不再
        # 是决策前提——现在 baostock 是本域第二条本地读路径的证据来源, 不是候选源。
        domain="stock_st",
        adapter="stock_st_derive",
        landing_writer="services.data_sources.stock_st_acceptance.land_stock_st_batch",
        canonical_writer="services.data_sources.stock_st_acceptance.accept_stock_st_batch",
        dataset_id="tier0.security_identity.stock_st_daily",
        runtime_state="accepted_runtime_ready_canary_pending",
    ),
}


class FormalBoundaryError(RuntimeError):
    """A formal transport boundary was violated before side effects."""

    def __init__(self, domain: str, *, reason: str, detail: str):
        self.domain = domain
        self.reason = reason
        self.detail = detail
        super().__init__(f"domain={domain} reason={reason} {detail}")


def formal_boundary(domain: str) -> FormalDomainBoundary | None:
    return _FORMAL_BOUNDARIES.get(domain)


def formal_domains() -> tuple[str, ...]:
    return tuple(sorted(_FORMAL_BOUNDARIES))


def require_live_adapter(adapter_name: str, *, domain: str) -> str:
    name = str(adapter_name or "").strip()

    if domain == "*":
        # Wildcard callers (e.g. a source-name-only adapter factory) don't
        # have a single domain in hand. Allow any adapter declared by *any*
        # registered formal domain.
        allowed = {item.adapter for item in _FORMAL_BOUNDARIES.values()}
    else:
        boundary = _FORMAL_BOUNDARIES.get(domain)
        if boundary is not None:
            allowed = {boundary.adapter}
        else:
            # Unregistered domain: fall back to the historical single
            # live-adapter behavior.
            allowed = {LIVE_ADAPTER}

    if name not in allowed:
        expected = ", ".join(sorted(allowed)) or LIVE_ADAPTER
        raise FormalBoundaryError(
            domain,
            reason="unsupported_live_adapter",
            detail=(
                f"domain={domain!r} only allows adapter in {{{expected}}}; "
                f"got {name!r}"
            ),
        )
    return name


def refuse_legacy_raw_write_for_formal_domain(domain: str) -> None:
    """Hard wall for domains whose formal writers exist or are canary-ready."""

    boundary = formal_boundary(domain)
    if boundary is None:
        return
    if boundary.runtime_state == "writers_pending":
        return
    if boundary.legacy_raw_write != "forbidden":
        raise FormalBoundaryError(
            domain,
            reason="invalid_boundary_declaration",
            detail="legacy_raw_write must be forbidden for formal domains",
        )
    raise FormalBoundaryError(
        domain,
        reason="formal_legacy_raw_write_forbidden",
        detail=(
            f"domain={domain} has formal boundary "
            f"landing={boundary.landing_writer} "
            f"canonical={boundary.canonical_writer} "
            f"runtime_state={boundary.runtime_state}; "
            "legacy _write_batch/raw replace is forbidden"
        ),
    )


def boundary_inventory() -> tuple[dict[str, str], ...]:
    """Static inventory for audits/unit tests; not a doctor readiness certificate."""

    return tuple(
        {
            "domain": item.domain,
            "adapter": item.adapter,
            "landing_writer": item.landing_writer,
            "canonical_writer": item.canonical_writer,
            "dataset_id": item.dataset_id,
            "runtime_state": item.runtime_state,
            "legacy_raw_write": item.legacy_raw_write,
        }
        for item in (_FORMAL_BOUNDARIES[name] for name in formal_domains())
    )


__all__ = [
    "LIVE_ADAPTER",
    "FormalBoundaryError",
    "FormalDomainBoundary",
    "boundary_inventory",
    "formal_boundary",
    "formal_domains",
    "refuse_legacy_raw_write_for_formal_domain",
    "require_live_adapter",
]
