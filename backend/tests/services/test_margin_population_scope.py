"""Knife 1a: margin accepted population scope is SSE+SZSE external_aggregate."""
from __future__ import annotations

import pytest

from services.data_sources.margin_population_scope import (
    MARGIN_ACCEPTED_VENUE_IDS,
    MarginPopulationScopeError,
    assert_margin_accepted_population_scope,
    assert_margin_transport_matches_accepted_scope,
)
from services.data_sources.sync_runner import domain_spec, load_registry


def _corrected_scope(**updates: object) -> dict:
    scope = {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE"],
        "population_label": "sse_szse_venue_reported_margin",
        "method": "tushare_margin_exchange_summary_sse_szse",
        "unit": "provider_declared_fields",
    }
    scope.update(updates)
    return scope


def test_live_registry_accepted_scope_is_sse_szse_only():
    """守: margin 的 accepted population_scope 永远是 SSE+SZSE external_aggregate,
    且它声明的 transport(split_by/batch_completeness)与这个 accepted scope 一致
    —— 不管 execution_policy 当前是 enabled 还是 disabled。

    2026-09-07 tushare_sunset freeze 把 margin 的 mode 从 enabled 转 disabled,
    原字面断言 `mode == "enabled"` 钉的是 Knife 1b 落地那一刻的运行状态, 不是
    这条测试名字("accepted_scope_is_sse_szse_only")要守的东西, freeze 后必然
    假红。

    `assert_margin_transport_matches_accepted_scope` 在 mode != enabled 时按
    设计提前 return(margin_population_scope.py:96-100, 冻结域允许 transport
    暂不对齐, 见该函数 docstring)——所以只对当前 disabled 的活配置调它其实是
    空跑, 没有真的验证 split_by/batch_completeness 数值本身。为了不让这条
    测试因为 mode=disabled 就悄悄失去覆盖, 额外在一份"假装它 enabled"的复本上
    再跑一次同一校验——这才是真正验证活配置里 split_by/batch_completeness 的
    数值仍然对齐, 而不是被 mode 短路绕过去。这比原断言覆盖更强, 不是降级。
    """
    spec = domain_spec(load_registry(), "margin")
    bound = assert_margin_accepted_population_scope(spec)
    assert bound.venue_ids == MARGIN_ACCEPTED_VENUE_IDS
    assert bound.kind == "external_aggregate"
    assert spec["execution_policy"]["mode"] in ("enabled", "disabled")
    assert spec["split_by"]["values"] == ["SSE", "SZSE"]
    assert_margin_transport_matches_accepted_scope(spec)
    would_be_enabled = {
        **spec,
        "execution_policy": {"mode": "enabled", "reason": "bounded_calendar_catchup"},
    }
    assert_margin_transport_matches_accepted_scope(would_be_enabled)


def test_rejects_bse_in_accepted_venue_ids():
    with pytest.raises(MarginPopulationScopeError, match="exactly"):
        assert_margin_accepted_population_scope(
            {
                "domain": "margin",
                "population_scope": _corrected_scope(
                    venue_ids=["SSE", "SZSE", "BSE"]
                ),
            }
        )


def test_rejects_project_universe_relabel():
    with pytest.raises(MarginPopulationScopeError, match="project_universe_pit"):
        assert_margin_accepted_population_scope(
            {
                "domain": "margin",
                "population_scope": {
                    "kind": "project_universe_pit",
                    "universe_policy_id": "active_a_share_trading_universe",
                    "security_field": "ts_code",
                    "as_of_field": "trade_date",
                    "as_of_role": "observation_time",
                },
            }
        )


@pytest.mark.parametrize(
    "values",
    [
        ["SSE", "SZSE", "BSE", "BSE"],
        ["SSE", "SZSE", "bse"],
        [],
    ],
)
def test_enabled_mode_rejects_malformed_split_by_values(values):
    """新增,不在本轮 10 个待修红测试之内——补一个此次改动顺带挖出来的覆盖缺口。

    test_sync_runner_today_catchup.py::
    test_formal_margin_contract_rejects_invalid_split_groups_while_execution_frozen
    这次改成显式钉死 execution_policy=disabled(见该测试内注释), 不再"顺路"借
    live registry 恰好 enabled 的状态间接测到
    assert_margin_transport_matches_accepted_scope 里"enabled 时 split_by.values
    形状异常(重复/大小写/空)必须拒绝"这一段(margin_population_scope.py:
    129-144)。搜过全仓库, 这条分支此前只被那一条测试间接盖到, 且纯属巧合
    (2026-07-23 Knife 1b 落地后 live registry 恰好是 enabled), 没有任何测试
    直接对它下断言过——不趁手补上, 这次改动就会让它净损失覆盖。

    这里直接对 assert_margin_transport_matches_accepted_scope 下手, 不经过
    run_domain / live registry, 所以不会再被将来任何一次 execution_policy
    冻结/解冻切换牵连。
    """
    spec = {
        "domain": "margin",
        "execution_policy": {"mode": "enabled", "reason": "active"},
        "population_scope": _corrected_scope(),
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {},
        },
        "split_by": {"param": "exchange_id", "values": values},
    }
    with pytest.raises(
        MarginPopulationScopeError, match="split_by.values must be exactly"
    ):
        assert_margin_transport_matches_accepted_scope(spec)


def test_enabled_mode_requires_transport_without_bse():
    spec = {
        "domain": "margin",
        "execution_policy": {"mode": "enabled", "reason": "active"},
        "population_scope": _corrected_scope(),
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {"BSE": "20230213"},
        },
        "split_by": {"param": "exchange_id", "values": ["SSE", "SZSE", "BSE"]},
    }
    with pytest.raises(MarginPopulationScopeError, match="forbids BSE"):
        assert_margin_transport_matches_accepted_scope(spec)


def test_enabled_mode_passes_when_transport_aligned():
    spec = {
        "domain": "margin",
        "execution_policy": {"mode": "enabled", "reason": "active"},
        "population_scope": _corrected_scope(),
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {},
        },
        "split_by": {"param": "exchange_id", "values": ["SSE", "SZSE"]},
    }
    assert_margin_transport_matches_accepted_scope(spec)
