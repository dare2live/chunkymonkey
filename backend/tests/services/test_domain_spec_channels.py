"""domain_spec() channels 归一化 — 零行为变化证明 + 通道校验 fail-closed 红绿双向。

2026-09-02 P1 channels 地基 (刀2)。本刀**不声明任何 fallback channel**, 所以判据是:
真 registry 全部域经 domain_spec 解析出的每一个键, 改动前后逐键相等 (除新增的 'channels')。

oracle 是**改动前 domain_spec 函数体的独立副本**, 不是对被测实现的引用 ——
引用被测实现会让这条测试恒绿 (自己和自己比)。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.data_sources import sync_runner as sr

REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "sync_registry.yaml"
)


def _load_real_registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _oracle_domain_spec(registry: dict[str, Any], domain: str) -> dict[str, Any]:
    """改动前 domain_spec 的独立副本 (三层继承: defaults → sources[source] → entry)。

    刻意逐字复制而非 import —— 这是 oracle, 必须独立于被测实现。
    """
    spec = dict(registry.get("defaults") or {})
    entry = registry["domains"].get(domain)
    if entry is None:
        raise KeyError(domain)
    source_cfg = (registry.get("sources") or {}).get(entry.get("source"))
    if source_cfg:
        spec.update(source_cfg)
    spec.update(entry)
    spec["domain"] = domain
    return spec


# ── 零行为变化 ────────────────────────────────────────────────────────────


def test_every_real_domain_is_key_for_key_unchanged():
    """真 registry 全部域: 去掉 channels 后必须与 oracle 逐键相等。

    遍历实际域集, **不断言域的数量** (数量是会漂移的运行时状态, 不是不变量)。
    """
    registry = _load_real_registry()
    domains = sorted(registry["domains"])
    assert domains, "registry 没有域, 这条测试会变成恒绿"

    drifted: list[str] = []
    for name in domains:
        got = sr.domain_spec(copy.deepcopy(registry), name)
        got.pop("channels", None)
        want = _oracle_domain_spec(copy.deepcopy(registry), name)
        if got != want:
            only_new = sorted(set(got) - set(want))
            only_old = sorted(set(want) - set(got))
            changed = sorted(k for k in set(got) & set(want) if got[k] != want[k])
            drifted.append(f"{name}: 多={only_new} 少={only_old} 变={changed}")
    assert not drifted, "domain_spec 相对改动前发生漂移:\n  " + "\n  ".join(drifted)


def test_channels_head_is_the_primary_view_without_recursion():
    registry = _load_real_registry()
    for name in sorted(registry["domains"]):
        spec = sr.domain_spec(copy.deepcopy(registry), name)
        channels = spec["channels"]
        assert channels, f"{name}: channels 不得为空"
        head = channels[0]
        assert "channels" not in head, f"{name}: channels[0] 自引用"
        assert head["source"] == spec["source"]
        assert head["domain"] == name


# ── 通道校验 fail-closed (红方向) ──────────────────────────────────────────


def _registry_with_fallback(fallback: Any, *, identity: Any = None) -> dict[str, Any]:
    """最小合成 registry —— 自带 fixture, 不依赖真 registry 的当前内容。"""
    return {
        "defaults": {"batch_mode": "by_trade_date"},
        "sources": {
            "primary_src": {"kind": "network_vendor", "target_db": "tushare_raw"},
            "backup_src": {"kind": "network_vendor", "target_db": "tushare_raw"},
        },
        "domains": {
            "probe": {
                "source": "primary_src",
                "api": "probe_api",
                **({"identity": identity} if identity is not None else {}),
                "fallback_channels": fallback,
            }
        },
    }


_EXCHANGE_FACT = {"kind": "exchange_fact", "fact": "探针用"}
_GOOD_CHANNEL = {
    "source": "backup_src",
    "api": "probe_backup_api",
    "system_evidence": "2026-09-02 合成 fixture, 非真实验收",
}


def test_vendor_view_may_not_declare_fallback():
    reg = _registry_with_fallback(
        [_GOOD_CHANNEL], identity={"kind": "vendor_view", "judge": "东财"}
    )
    with pytest.raises(ValueError, match="identity.kind"):
        sr.domain_spec(reg, "probe")


def test_missing_identity_blocks_fallback():
    reg = _registry_with_fallback([_GOOD_CHANNEL])  # 无 identity
    with pytest.raises(ValueError, match="identity.kind"):
        sr.domain_spec(reg, "probe")


def test_channel_without_api_is_rejected():
    reg = _registry_with_fallback(
        [{"source": "backup_src", "system_evidence": "x"}], identity=_EXCHANGE_FACT
    )
    with pytest.raises(ValueError, match="缺 source/api"):
        sr.domain_spec(reg, "probe")


def test_channel_with_semantic_axis_key_is_rejected():
    """通道想改 grain = 想换数据不是换水管, 必须拒。"""
    reg = _registry_with_fallback(
        [{**_GOOD_CHANNEL, "grain": ["ts_code"]}], identity=_EXCHANGE_FACT
    )
    with pytest.raises(ValueError, match="非传输轴键"):
        sr.domain_spec(reg, "probe")


def test_channel_without_system_evidence_is_rejected():
    reg = _registry_with_fallback(
        [{**_GOOD_CHANNEL, "system_evidence": "   "}], identity=_EXCHANGE_FACT
    )
    with pytest.raises(ValueError, match="system_evidence"):
        sr.domain_spec(reg, "probe")


def test_unregistered_fallback_source_is_rejected():
    reg = _registry_with_fallback(
        [{**_GOOD_CHANNEL, "source": "ghost_src"}], identity=_EXCHANGE_FACT
    )
    with pytest.raises(ValueError, match="未在 sources 段登记"):
        sr.domain_spec(reg, "probe")


# ── 合法 fallback 必须解析成功 (绿方向 — 证明上面不是逢改必炸) ──────────────


def test_valid_fallback_resolves_in_declared_order():
    reg = _registry_with_fallback([_GOOD_CHANNEL], identity=_EXCHANGE_FACT)
    spec = sr.domain_spec(reg, "probe")
    channels = spec["channels"]
    assert [c["source"] for c in channels] == ["primary_src", "backup_src"]
    assert channels[1]["api"] == "probe_backup_api"
    # 通道视图继承 defaults 与 entry 的语义轴键, 但 source/api 由通道自己覆盖
    assert channels[1]["batch_mode"] == "by_trade_date"
    assert channels[1]["domain"] == "probe"
    # fallback_channels 本身不得漏进视图 (否则递归/自污染)
    assert "fallback_channels" not in channels[1]


# ── 三层继承链单一实现防回归 ──────────────────────────────────────────────


def test_channel_views_is_only_called_from_domain_spec():
    """_channel_views 只准由 domain_spec 调用 —— 否则继承链又有了第二份实现。

    与既有的 test_registry_merge_chain_single_source 同源。
    """
    src = Path(sr.__file__).read_text(encoding="utf-8")
    call_lines = [
        (i, line)
        for i, line in enumerate(src.splitlines(), 1)
        if "_channel_views(" in line and not line.lstrip().startswith("def ")
    ]
    assert len(call_lines) == 1, (
        f"_channel_views 出现 {len(call_lines)} 处调用, 期望 1 处 (只在 domain_spec 内): "
        f"{[n for n, _ in call_lines]}"
    )


# ── identity 契约不变量 (2026-09-02 数据身份三层模型落地) ──────────────────
#
# 三层模型 (业主 2026-09-01 定): 数据身份 (是什么数据 / 谁的判断) / 获取渠道 (哪根水管) /
# 批次来源 (这批谁供的)。identity 是第一层, source 是第二层, ingest_batch.source_name 是第三层。
# 关键区分: "东财板块" 里的**东财**属于第一层 (换成申万就是另一份数据),
# 而从 tushare 还是 akshare 取它属于第二层 (换了数据身份不变)。

_LEGAL_IDENTITY_KINDS = frozenset({
    "exchange_fact",   # 交易所/公告的真实事件 — 事实无主, 不带任何机构名
    "derived_rule",    # 由已注册域 + 公开规则可算 — 同样不带机构名
    "vendor_view",     # 某机构的专有算法/分类判断 — 必须记 judge
    "undecided",       # 尚未裁决 — 因不在 _channel_views 白名单, 天然无法声明 fallback
})


def test_every_domain_declares_a_legal_identity_kind():
    registry = _load_real_registry()
    bad = []
    for name, entry in sorted(registry["domains"].items()):
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("identity") or {}).get("kind")
        if kind not in _LEGAL_IDENTITY_KINDS:
            bad.append(f"{name}: kind={kind!r}")
    assert not bad, (
        "以下域的 identity.kind 缺失或非法 (合法值 "
        f"{sorted(_LEGAL_IDENTITY_KINDS)}):\n  " + "\n  ".join(bad)
    )


def test_vendor_view_must_name_the_judge_and_others_must_not():
    """观点类必须记『谁的判断』; 事实类不许带机构名 (事实无主)。"""
    registry = _load_real_registry()
    missing_judge, stray_judge = [], []
    for name, entry in sorted(registry["domains"].items()):
        if not isinstance(entry, dict):
            continue
        identity = entry.get("identity") or {}
        kind, judge = identity.get("kind"), identity.get("judge")
        if kind == "vendor_view" and not str(judge or "").strip():
            missing_judge.append(name)
        if kind in ("exchange_fact", "derived_rule") and judge:
            stray_judge.append(f"{name} (judge={judge!r})")
    assert not missing_judge, (
        "vendor_view 域缺 judge —— 换 judge 就是换数据, 必须记名: " + ", ".join(missing_judge)
    )
    assert not stray_judge, (
        "事实类域带了 judge —— 事实无主, 机构名属于水管层不属于身份层: "
        + ", ".join(stray_judge)
    )


def test_undecided_identity_cannot_declare_fallback_channels():
    """边界域 (kind: undecided) 必须无法声明 fallback —— fail-closed 而非靠人记得。"""
    reg = _registry_with_fallback(
        [_GOOD_CHANNEL], identity={"kind": "undecided", "blocked_by": "口径未核"}
    )
    with pytest.raises(ValueError, match="identity.kind"):
        sr.domain_spec(reg, "probe")
