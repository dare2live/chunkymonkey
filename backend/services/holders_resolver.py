"""HolderResolver — 多源 fallback 链.

按 source_tier 1→2→3 顺序尝试; 上一层抛异常或返回 None 才落到下一层.
对应 fact_top10_holder_period.source / source_tier 字段.

设计纪律 (顶层设计 §3, 用户重申):
- 117 台 TDX HQ 服务器轮询是 tdxhub.holders.HolderFetcher 自己的事.
  HolderFetcher 内部已 cooldown + auto-resync HQ_HOSTS, 不会"碰一次失败就放弃".
- 当 TdxhubHolderSource 抛异常 (即 117 台都试过+冷却+重抓还失败), 才进入
  MiaoxiangHolderSource fallback.
- miaoxiang 也失败时, 才进入下一层 (akshare 暂为占位; 99.6% 覆盖下用不到).

源 tier:
  tier=1 tdxhub   ──> 主源, F10 「股东研究」 (Format A + Format B)
  tier=2 miaoxiang ──> 备源, 妙想 RPT_F10_EH_FREEHOLDERS (字段会变, 仅 fallback)
  tier=3 akshare  ──> 兜底, 批量按报告期 (per-stock 拿不动, 暂占位)

返回值统一 ResolverResult, 包含已规整成 fact_top10_holder_period schema 的
records + 用了哪个 source.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from services.tdx_source import ensure_workspace_tdxhub_path

logger = logging.getLogger("cm-api")

ensure_workspace_tdxhub_path()


@dataclass
class ResolverResult:
    """resolver 返回: 已规整成 fact_top10_holder_period 行的 records + 元数据.

    holders + periods 是所有 source 都返回的核心数据.
    controlling / plans / trades 仅 tdxhub 有 (来自段 1/2/3 解析); fallback
    源为 None / 空列表.
    """

    holders: list[dict]
    periods: list[dict]
    raw_text: Optional[str]               # tdxhub 才有原文; miaoxiang/akshare 为 None
    raw_hash: Optional[str]
    page_update_date: Optional[str]
    server_or_endpoint: Optional[str]
    source: str                            # 'tdx_f10' | 'miaoxiang' | 'akshare'
    source_tier: int                       # 1 / 2 / 3
    fetched_at: str
    # 仅 tdxhub 解析填充; 其他源为 None / 空
    controlling: Optional[dict] = None
    plans: Optional[list[dict]] = None
    trades: Optional[list[dict]] = None

    def has_data(self) -> bool:
        return bool(self.holders)


class SourceExhausted(RuntimeError):
    """所有源都失败时抛."""

    def __init__(self, symbol: str, errors: list[tuple[str, Exception]]):
        self.symbol = symbol
        self.errors = errors
        msg = f"all holder sources exhausted for {symbol}: " + "; ".join(
            f"{name}={type(e).__name__}: {e}" for name, e in errors
        )
        super().__init__(msg)


class HolderSource(ABC):
    """单一源 fetcher 接口."""

    name: str
    source_tier: int

    @abstractmethod
    def fetch(self, symbol: str, *, stock_name: str = "") -> Optional[ResolverResult]:
        """成功返回 ResolverResult; 该源不覆盖时返回 None; 真错抛异常."""
        ...

    def close(self) -> None:  # 可选
        pass


# ─────────────────────────────────────────────────────────────────────
# Tier 1: tdxhub
# ─────────────────────────────────────────────────────────────────────

class TdxhubHolderSource(HolderSource):
    """主源 — 走 tdxhub.holders.HolderFetcher (内部 117 服务器轮询)."""

    name = "tdx_f10"
    source_tier = 1

    def __init__(self, *, fetcher=None, timeout: int = 15,
                 max_attempts_per_call: int = 6) -> None:
        if fetcher is None:
            from tdxhub.holders import HolderFetcher  # noqa: WPS433
            fetcher = HolderFetcher(timeout=timeout, max_attempts_per_call=max_attempts_per_call)
        self._fetcher = fetcher

    def fetch(self, symbol: str, *, stock_name: str = "") -> Optional[ResolverResult]:
        from tdxhub.holders import parse_research_records, _hash  # noqa: WPS433

        text = self._fetcher.fetch_text(symbol)
        if not text:
            # 北交所 / 无 F10 → tdxhub 不覆盖, 返回 None 让 resolver 走 fallback
            return None
        res = parse_research_records(text, symbol=symbol, stock_name=stock_name)
        page = res["page"]
        return ResolverResult(
            holders=[dict(row) for row in (res.get("holders") or [])],
            periods=[dict(row) for row in (res.get("periods") or [])],
            raw_text=text,
            raw_hash=_hash(text),
            page_update_date=page.get("page_update_date"),
            server_or_endpoint=str(self._fetcher.stats().get("active_server")),
            source=self.name,
            source_tier=self.source_tier,
            fetched_at=datetime.utcnow().isoformat(timespec="seconds"),
            controlling=res.get("controlling"),
            plans=[dict(row) for row in (res.get("plans") or [])],
            trades=[dict(row) for row in (res.get("trades") or [])],
        )

    def close(self) -> None:
        try:
            self._fetcher.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Tier 2: miaoxiang (aif10 RPT_F10_EH_FREEHOLDERS)
# ─────────────────────────────────────────────────────────────────────

# 妙想 RPT_F10_EH_FREEHOLDERS → fact_top10_holder_period 字段映射.
# 字段名以 aif10 docs 为准, 妙想可能不定时改字段名 — 改时只需改这张表.
_MIAOXIANG_FIELD_MAP = {
    "SECUCODE": "stock_code_secucode",       # 600519.SH 形式
    "SECURITY_CODE": "stock_code",
    "SECURITY_NAME_ABBR": "stock_name",
    "REPORT_DATE": "report_date",
    "NOTICE_DATE": "notice_date",
    "HOLDER_RANK": "holder_rank",
    "HOLDER_NAME": "holder_name",
    "HOLD_NUM": "shares_approx",             # 注意: 妙想给的是总持股, 非 free
    "FREE_HOLDNUM": "shares_approx_free",    # 流通持股 (优先用)
    "FREE_HOLDNUM_RATIO": "hold_ratio_float",
    "HOLD_NUM_CHANGE": "change_shares_approx",
    "HOLDER_NEWTYPE": "holder_type",
    "IS_HOLDORG": "is_org",
}


def _safe_text(value) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> Optional[int]:
    number = _safe_float(value)
    if number is None:
        return None
    return int(round(number))


def _compact_date(value) -> Optional[str]:
    text = _safe_text(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _first_value(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


class MiaoxiangHolderSource(HolderSource):
    """备源 — 仅 tdxhub 完全失败时启用. 妙想 RPT_F10_EH_FREEHOLDERS.

    注意: 妙想字段名会变, 它不应该作为主路径. 当前实现是 fallback 占位 +
    field-map 模板, 等真正用到时再细化.
    """

    name = "miaoxiang"
    source_tier = 2

    def __init__(self, *, client=None) -> None:
        self._client = client

    def _ensure_client(self):
        if self._client is None:
            try:
                from aif10_scraper import default_client  # noqa: WPS433
                self._client = default_client()
            except ImportError as e:
                raise RuntimeError(
                    "miaoxiang aif10_scraper 未安装; "
                    "fallback 不可用. 请 pip install -e ../miaoxiang."
                ) from e
        return self._client

    def fetch(self, symbol: str, *, stock_name: str = "") -> Optional[ResolverResult]:
        # secucode 形式: 600519.SH / 000001.SZ
        secu = self._make_secucode(symbol)
        client = self._ensure_client()
        from aif10_scraper import fetch_all_pages  # noqa: WPS433

        rows = fetch_all_pages(
            "RPT_F10_EH_FREEHOLDERS",
            secucode=secu,
            page_size=500,
            max_pages=0,
            client=client,
        )
        if not rows:
            return None

        # 规整到 fact_top10_holder_period schema
        holders = self._normalize(rows, symbol)
        if not holders:
            return None
        # period 元信息: miaoxiang 没给累计统计, 留空
        periods = [{
            "stock_code": symbol, "report_date": report_date,
            "holder_set": "free", "source": self.name,
        } for report_date in sorted({row["report_date"] for row in holders})]
        return ResolverResult(
            holders=holders,
            periods=periods,
            raw_text=None,
            raw_hash=None,
            page_update_date=None,
            server_or_endpoint="datacenter.eastmoney.com (via aif10)",
            source=self.name,
            source_tier=self.source_tier,
            fetched_at=datetime.utcnow().isoformat(timespec="seconds"),
        )

    @staticmethod
    def _make_secucode(symbol: str) -> str:
        if "." in symbol:
            return symbol
        if symbol.startswith(("60", "68", "5", "11")):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    @staticmethod
    def _normalize(rows: list[dict], symbol: str) -> list[dict]:
        """把妙想列名映射到 fact_top10_holder_period schema."""

        normalized = []
        fetched_at = datetime.utcnow().isoformat(timespec="seconds")
        for idx, row in enumerate(rows, start=1):
            stock_code = _safe_text(row.get("SECURITY_CODE")) or symbol
            report_date = _compact_date(row.get("REPORT_DATE"))
            holder_name = _safe_text(row.get("HOLDER_NAME"))
            if not report_date or not holder_name:
                continue
            shares = _safe_int(_first_value(row, "FREE_HOLDNUM", "HOLD_NUM"))
            ratio = _safe_float(row.get("FREE_HOLDNUM_RATIO"))
            change_shares = _safe_int(row.get("HOLD_NUM_CHANGE"))
            holder_type = _safe_text(row.get("HOLDER_NEWTYPE"))
            normalized.append({
                "stock_code": stock_code,
                "stock_name": _safe_text(row.get("SECURITY_NAME_ABBR")) or "",
                "market": "",
                "report_date": report_date,
                "notice_date": _compact_date(row.get("NOTICE_DATE")),
                "holder_rank": _safe_int(row.get("HOLDER_RANK")) or idx,
                "row_seq": 1,
                "holder_name": holder_name,
                "holder_name_norm": holder_name,
                "share_class": "_",
                "is_secondary_class": False,
                "is_exit_row": False,
                "shares_text": None,
                "shares_approx": shares,
                "shares_precision": None,
                "hold_amount": float(shares) if shares is not None else None,
                "hold_ratio_float": ratio,
                "hold_ratio_total": None,
                "hold_ratio": ratio,
                "hold_market_cap": None,
                "holder_type": holder_type,
                "holder_type_or_nature": holder_type,
                "share_nature": holder_type,
                "change_status": "未知",
                "change_shares_text": None,
                "change_shares_approx": change_shares,
                "hold_change": "",
                "hold_change_num": float(change_shares) if change_shares is not None else None,
                "effective_date": None,
                "page_update_date": None,
                "source": MiaoxiangHolderSource.name,
                "source_tier": 2,
                "raw_hash": None,
                "fetched_at": fetched_at,
                "created_at": fetched_at,
            })
        return normalized

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
# Tier 3: akshare (panic-mode, batch-style; 占位)
# ─────────────────────────────────────────────────────────────────────

class AkshareHolderSource(HolderSource):
    """兜底 — akshare stock_gdfx_free_holding_detail_em.

    注意: akshare 接口是按报告期批量下载, 不是按股票. 单股 fallback 成本很
    高 (要下载整个季度的全市场十大流通股东). 当前实现为占位; 99.6% 覆盖
    下基本用不到, 真用到再写.
    """

    name = "akshare"
    source_tier = 3

    def fetch(self, symbol: str, *, stock_name: str = "") -> Optional[ResolverResult]:
        raise NotImplementedError(
            "akshare per-stock holder fallback 未实现. "
            "akshare stock_gdfx_free_holding_detail_em 是按报告期批量, "
            "需先决定调度策略 (是否预下载整季并缓存). "
            "tdxhub + miaoxiang 双层覆盖足以应对当前 99.6% 全市场场景."
        )


# ─────────────────────────────────────────────────────────────────────
# Resolver
# ─────────────────────────────────────────────────────────────────────

class HolderResolver:
    """按 source_tier 顺序 fallback 的解析器入口.

    用法::

        resolver = HolderResolver(sources=[
            TdxhubHolderSource(),
            MiaoxiangHolderSource(),
            # AkshareHolderSource() 暂不挂
        ])
        result = resolver.fetch("600519")
        # result.source_tier in {1, 2, 3}, 决定写入 fact_top10_holder_period 时的 tier
    """

    def __init__(self, sources: list[HolderSource]) -> None:
        if not sources:
            raise ValueError("HolderResolver 至少需要一个 source")
        self.sources = sorted(sources, key=lambda s: s.source_tier)
        self._stats: dict[str, int] = {
            f"{s.name}_attempts": 0 for s in self.sources
        }
        for s in self.sources:
            self._stats[f"{s.name}_success"] = 0
            self._stats[f"{s.name}_no_data"] = 0
            self._stats[f"{s.name}_error"] = 0

    def fetch(self, symbol: str, *, stock_name: str = "") -> Optional[ResolverResult]:
        errors: list[tuple[str, Exception]] = []
        for source in self.sources:
            self._stats[f"{source.name}_attempts"] += 1
            try:
                result = source.fetch(symbol, stock_name=stock_name)
                if result is not None and result.has_data():
                    self._stats[f"{source.name}_success"] += 1
                    if source.source_tier > 1:
                        # 触发 fallback 一定要记录, 这是 tdxhub 不覆盖的明确信号
                        logger.warning(
                            "[holder_resolver] %s 用了 %s (tier=%d) fallback",
                            symbol, source.name, source.source_tier,
                        )
                    return result
                else:
                    self._stats[f"{source.name}_no_data"] += 1
                    logger.info(
                        "[holder_resolver] %s tier=%d (%s) 无数据, 尝试下一层",
                        symbol, source.source_tier, source.name,
                    )
            except Exception as e:
                self._stats[f"{source.name}_error"] += 1
                errors.append((source.name, e))
                logger.warning(
                    "[holder_resolver] %s tier=%d (%s) ERROR %s: %s, 尝试下一层",
                    symbol, source.source_tier, source.name, type(e).__name__, e,
                )
        # 所有源都没出数据
        if errors:
            raise SourceExhausted(symbol, errors)
        return None

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def close(self) -> None:
        for s in self.sources:
            s.close()


def default_resolver() -> HolderResolver:
    """生产默认 resolver: tdxhub + miaoxiang. akshare 暂不挂."""

    return HolderResolver(sources=[
        TdxhubHolderSource(),
        MiaoxiangHolderSource(),
    ])
