"""on_demand 窗口门 (2026-08-30 修复: 从未生效过的 by_ts_code 交集条件).

背景: sync_runner 两处「on_demand 域必须显式给 --start/--end」检查 (run_domain 内 +
CLI arg 校验 `_preflight_cli_request_shape` 内), 原条件都是:

    spec.get("batch_mode") == "by_ts_code" and spec.get("sync_policy") == "on_demand"

实测 registry 里这两个条件的交集为空 —— by_ts_code 只有 income/balancesheet/
fina_indicator 三域且均非 on_demand; on_demand 域全是 by_trade_date (daily/stock_st/
margin/扶摇四域) 或 full_refresh (trade_cal/baostock_trade_cal)。所以这道门对任何域
都从未触发过 —— 不是"覆盖不全"是"从未生效"。后果: 裸跑
`chunkyctl sync --domain fuyao_limit_up_pool` 不报错、直接从 data_start 拉全史 (约
1454 个交易日), 而 registry 注释声称 on_demand「只许 --start/--end、禁 mass
backfill」——声明与实际背离。

修复: 去掉 batch_mode == "by_ts_code" 这一项, 改为「sync_policy == on_demand 且
batch_mode != full_refresh 且缺 start 或 end 就拒绝」。full_refresh 域 (trade_cal/
baostock_trade_cal) 结构上本来就不接受 start/end (另有 `batch_mode == "full_refresh"
and (start or end)` 的门直接拒绝带 bounds 的调用) —— 放宽后若不豁免 full_refresh,
会陷入「既不许给 start/end、又因为没给而被拒」的自相矛盾, 必须显式豁免。

本文件用合成域锁定"门从未生效"→"门生效"的红→绿证据 (test_*_is_rejected_by_run_domain
/ test_*_widened_gate_now_rejects_cli_preflight 两条改代码前必须失败), 并用合成域 +
真实 registry 双重回归三类不可误伤路径: full_refresh on_demand 域 (self-contradiction
风险) / --drain 日更路径 / by_ts_code 三个真实域。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

D = "20260701"

ENABLED_EXECUTION_POLICY = {"mode": "enabled", "reason": "active"}

# 镜像扶摇四域的形状: by_trade_date + on_demand, 无 fixed start/end —— 这是真实
# 世界里那道门"从未生效"的形状 (fuyao_limit_up_pool 就是这个形状)。
SYNTHETIC_BY_TRADE_DATE_ON_DEMAND = {
    "source": "tushare",
    "api": "synthetic_ondemand_daily",
    "target_table": "raw_tushare_synthetic_ondemand_test",
    "grain": ["trade_date", "ts_code"],
    "batch_mode": "by_trade_date",
    "sync_policy": "on_demand",
    "data_start": D,
}

# 镜像 trade_cal / baostock_trade_cal 的形状: full_refresh + on_demand, 结构上本来
# 就不接受 start/end —— 必须豁免这道门, 否则自相矛盾 (放宽后既不许给又因未给被拒)。
SYNTHETIC_FULL_REFRESH_ON_DEMAND = {
    "source": "baostock",
    "api": "synthetic_ondemand_full_refresh",
    "target_table": "raw_baostock_synthetic_ondemand_fr_test",
    "grain": ["cal_date"],
    "batch_mode": "full_refresh",
    "sync_policy": "on_demand",
    "freshness_date_column": "cal_date",
}

REG = {
    "defaults": {
        "target_db": "tushare_raw",
        "fetch_timeout_seconds": 120,
        "execution_policy": ENABLED_EXECUTION_POLICY,
    },
    "domains": {
        "synthetic_on_demand_by_trade_date": SYNTHETIC_BY_TRADE_DATE_ON_DEMAND,
        "synthetic_on_demand_full_refresh": SYNTHETIC_FULL_REFRESH_ON_DEMAND,
    },
}


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1) run_domain 内的门 (约 3266 行, spec.get("batch_mode") == "by_ts_code" 那处)
#    以下两条都不需要 mock DB/adapter —— 门在 eligibility/batch 逻辑之前就该拦下,
#    根本碰不到 I/O; 如果门没生效, 代码会往下跑并因为缺 trading_days mock 等而以
#    另一种方式出错/继续 —— 用 pytest.raises(ValueError, match="on_demand") 精确锁定
#    "是这道门拦的", 而不是随便什么异常。
# ---------------------------------------------------------------------------

def test_by_trade_date_on_demand_without_bounds_is_rejected_by_run_domain():
    """核心红测试 (location 1): by_trade_date + on_demand 域不给 start/end 必须被拒.

    2026-08-30 之前该门条件是 batch_mode=="by_ts_code" and sync_policy=="on_demand",
    对 by_trade_date 域永远是 False —— 这条测试在改代码前必须失败 (门不生效), 改代码
    后必须通过。
    """
    with pytest.raises(ValueError, match="on_demand"):
        sr.run_domain("synthetic_on_demand_by_trade_date", registry=REG)


def test_full_refresh_on_demand_domain_not_blocked_by_run_domain_gate(monkeypatch):
    """full_refresh 豁免 (location 1): 放宽后不能把 full_refresh 域反而拒了 (自相矛盾)。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr, "_fetch_logical_batch", lambda *_args: [{"cal_date": "20261231"}]
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)

    result = sr.run_domain("synthetic_on_demand_full_refresh", registry=REG)

    assert result["ok"] is True
    c.close()


# ---------------------------------------------------------------------------
# 2) CLI arg 校验的门 (约 4166 行, _preflight_cli_request_shape 内同款条件)
#    纯函数, 不碰 DB/网络 —— 直接构造 argparse.Namespace 单测, 不跑整条 CLI。
# ---------------------------------------------------------------------------

def _args(*argv: str):
    return sr._parse_cli_args(list(argv))


def test_cli_preflight_widened_gate_now_rejects_by_trade_date_on_demand():
    """核心红测试 (location 2): 同一条门, CLI arg 校验层也从未生效过。

    改代码前: batch_mode=="by_ts_code" 条件对 by_trade_date 域恒 False, 这条测试必须
    失败 (不抛错)。改代码后: 必须抛 SyncWindowError。
    """
    args = _args("--domain", "synthetic_on_demand_by_trade_date")
    with pytest.raises(sr.SyncWindowError, match="on_demand"):
        sr._preflight_cli_request_shape(args, REG, ["synthetic_on_demand_by_trade_date"])


def test_cli_preflight_full_refresh_on_demand_domain_not_blocked():
    """full_refresh 豁免 (location 2): 同一个自相矛盾风险在 CLI 校验层也要防住。"""
    args = _args("--domain", "synthetic_on_demand_full_refresh")
    # 不应抛错 —— full_refresh 域不接受 start/end, 也不该被要求给 start/end。
    sr._preflight_cli_request_shape(args, REG, ["synthetic_on_demand_full_refresh"])


def test_cli_preflight_drain_path_unaffected_for_on_demand_domain():
    """--drain 前置条件已存在 (改代码前已读代码确认): 日更式 drain 调用不受这道门影响。"""
    args = _args("--domain", "synthetic_on_demand_by_trade_date", "--drain")
    # 不应抛错 —— args.drain=True 时 `not args.drain` 短路整个门, 与是否放宽无关。
    sr._preflight_cli_request_shape(args, REG, ["synthetic_on_demand_by_trade_date"])


# ---------------------------------------------------------------------------
# 3) 真实 registry 冒烟 (不实际拉数据, 只跑参数校验函数)
# ---------------------------------------------------------------------------

REAL_REG = sr.load_registry()

FUYAO_ON_DEMAND_DOMAINS = [
    "fuyao_limit_up_pool",
    "fuyao_limit_down_pool",
    "fuyao_limit_break_pool",
    "fuyao_auction_benchmark",
]


@pytest.mark.parametrize("domain", FUYAO_ON_DEMAND_DOMAINS)
def test_real_registry_fuyao_domains_reject_bare_invocation(domain):
    """真实 bug 复现 (改代码前必须失败): 裸跑扶摇域不给 --start/--end 必须被拒,
    而不是像修复前那样直接从 data_start 拉全史 (~1454 个交易日)。
    """
    spec = sr.domain_spec(REAL_REG, domain)
    assert spec.get("batch_mode") == "by_trade_date"
    assert spec.get("sync_policy") == "on_demand"

    args = _args("--domain", domain)
    with pytest.raises(sr.SyncWindowError, match="on_demand"):
        sr._preflight_cli_request_shape(args, REAL_REG, [domain])

    # location 1 (run_domain 内的门) 同样必须拦下 —— 门在任何 I/O 之前就raise,
    # 直接单测安全 (不会真的发起网络/DB 调用)。
    with pytest.raises(ValueError, match="on_demand"):
        sr.run_domain(domain, registry=REAL_REG)


@pytest.mark.parametrize("domain", ["trade_cal", "baostock_trade_cal"])
def test_real_registry_full_refresh_on_demand_domains_not_blocked(domain):
    """真实 full_refresh + on_demand 域 (trade_cal/baostock_trade_cal) 的自相矛盾回归:
    放宽门之后, 裸跑 `chunkyctl sync --domain trade_cal` (它们唯一合法的调用方式,
    本来就不带 start/end) 绝不能被这道新放宽的门反而挡住。
    """
    spec = sr.domain_spec(REAL_REG, domain)
    assert spec.get("batch_mode") == "full_refresh"
    assert spec.get("sync_policy") == "on_demand"

    args = _args("--domain", domain)
    sr._preflight_cli_request_shape(args, REAL_REG, [domain])  # 不应抛错


@pytest.mark.parametrize("domain", ["daily", "stock_st", "margin"])
def test_real_registry_daily_stock_st_margin_all_due_drain_unaffected(domain):
    """daily / stock_st / margin: --all-due 排除 on_demand 域 (自动链本就不选它们),
    且单域 --drain 直呼也受 `not args.drain` 保护, 两层都不受这道门放宽影响。
    """
    spec = sr.domain_spec(REAL_REG, domain)
    assert spec.get("sync_policy") == "on_demand"
    assert domain not in sr.automatic_domains(REAL_REG), (
        f"{domain} 是 on_demand 域, 不应出现在 --all-due 自动链选中集合里"
    )

    args = _args("--domain", domain, "--drain")
    sr._preflight_cli_request_shape(args, REAL_REG, [domain])  # 不应抛错


@pytest.mark.parametrize("domain", ["income", "balancesheet", "fina_indicator"])
def test_real_registry_by_ts_code_domains_behavior_unchanged(domain):
    """by_ts_code 三域行为不变: 均非 on_demand, 放宽这道门 (去掉 by_ts_code 限定) 后
    它们仍然不受这道 on_demand 窗口门约束 (与改代码前完全一致)。
    """
    spec = sr.domain_spec(REAL_REG, domain)
    assert spec.get("batch_mode") == "by_ts_code"
    assert spec.get("sync_policy") != "on_demand"

    args = _args("--domain", domain)
    sr._preflight_cli_request_shape(args, REAL_REG, [domain])  # 不应抛错 (无 start/end 也一样)
