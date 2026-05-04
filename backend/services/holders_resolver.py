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
DataFrame + 用了哪个 source.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cm-api")

# tdxhub.holders 通过同级 checkout 或 pip install -e ../tdxhub 引入.
STOCK_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))


@dataclass
class ResolverResult:
    """resolver 返回: 已规整成 fact_top10_holder_period 行的 DataFrame + 元数据.

    holders_df + periods_df 是所有 source 都返回的核心数据.
    controlling / plans / trades 仅 tdxhub 有 (来自段 1/2/3 解析); fallback
    源为 None / 空 DataFrame.
    """

    holders_df: pd.DataFrame
    periods_df: pd.DataFrame
    raw_text: Optional[str]               # tdxhub 才有原文; miaoxiang/akshare 为 None
    raw_hash: Optional[str]
    page_update_date: Optional[str]
    server_or_endpoint: Optional[str]
    source: str                            # 'tdx_f10' | 'miaoxiang' | 'akshare'
    source_tier: int                       # 1 / 2 / 3
    fetched_at: str
    # 仅 tdxhub 解析填充; 其他源为 None / 空
    controlling: Optional[dict] = None
    plans_df: Optional[pd.DataFrame] = None
    trades_df: Optional[pd.DataFrame] = None

    def has_data(self) -> bool:
        return not self.holders_df.empty


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
            holders_df=pd.DataFrame.from_records(res.get("holders") or []),
            periods_df=pd.DataFrame.from_records(res.get("periods") or []),
            raw_text=text,
            raw_hash=_hash(text),
            page_update_date=page.get("page_update_date"),
            server_or_endpoint=str(self._fetcher.stats().get("active_server")),
            source=self.name,
            source_tier=self.source_tier,
            fetched_at=datetime.utcnow().isoformat(timespec="seconds"),
            controlling=res.get("controlling"),
            plans_df=pd.DataFrame.from_records(res.get("plans") or []),
            trades_df=pd.DataFrame.from_records(res.get("trades") or []),
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
        df = pd.DataFrame(rows)
        df = self._normalize(df, symbol)
        if df.empty:
            return None
        # period 元信息: miaoxiang 没给累计统计, 留空
        periods = pd.DataFrame([{
            "stock_code": symbol, "report_date": rd,
            "holder_set": "free", "source": self.name,
        } for rd in df["report_date"].unique()])
        return ResolverResult(
            holders_df=df,
            periods_df=periods,
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
    def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """把妙想列名映射到 fact_top10_holder_period schema."""

        out = pd.DataFrame()
        out["stock_code"] = df.get("SECURITY_CODE") or symbol
        out["stock_name"] = df.get("SECURITY_NAME_ABBR")
        out["report_date"] = df.get("REPORT_DATE", "").str.replace("-", "").str.slice(0, 8)
        out["notice_date"] = df.get("NOTICE_DATE", "").str.replace("-", "").str.slice(0, 8)
        out["holder_rank"] = df.get("HOLDER_RANK")
        out["row_seq"] = 1
        out["holder_name"] = df.get("HOLDER_NAME")
        out["holder_name_norm"] = df.get("HOLDER_NAME")
        out["share_class"] = "_"  # 妙想不区分; 占位
        out["is_secondary_class"] = False
        out["is_exit_row"] = False
        out["shares_approx"] = (df.get("FREE_HOLDNUM") or df.get("HOLD_NUM")).astype("Int64")
        out["hold_amount"] = out["shares_approx"].astype("float64")
        out["hold_ratio_float"] = df.get("FREE_HOLDNUM_RATIO")
        out["holder_type"] = df.get("HOLDER_NEWTYPE")
        out["change_shares_approx"] = df.get("HOLD_NUM_CHANGE")
        out["change_status"] = "未知"  # 妙想给的是数值, 上层下游应基于 lag 自己推
        out["holder_set"] = "free"
        out["source"] = MiaoxiangHolderSource.name
        out["source_tier"] = 2
        return out

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
