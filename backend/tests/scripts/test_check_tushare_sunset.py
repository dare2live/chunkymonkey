"""check_tushare_sunset.py 的机械锁 (TuShare 授权到期风险门).

**必须自带 fixture, 不许依赖当前仓库的真实配置** — 每个用例在 ``tmp_path`` 里
建一个全新的 sunset.yaml + registry.yaml，从不读真文件。

唯一例外: ``TestRealConfigInvariants`` 直接读仓库里的真实 tushare_sunset.yaml，
但只断言**不变量**(如 "所有 decision 都在合法集里")，不断言会随工作推进漂移的
运行时测量值(如 "warns 数量 == 19") —— 项目教训
[[feedback-test-must-carry-its-own-fixture]]: 断言不变量,不断言状态。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts import check_tushare_sunset as gate


def _write_sunset(
    tmp_path: Path,
    *,
    expires: str = "2026-09-10",
    domains: dict | None = None,
    undecided: list[str] | None = None,
) -> Path:
    """编写 tushare_sunset.yaml 临时文件。"""
    lines = [
        "version: 1",
        f"authorization_expires: {expires!r}",
        "renew: false",
        "domains:",
    ]

    if domains:
        for domain_name, entry in domains.items():
            lines.append(f"  {domain_name}:")
            for key, value in entry.items():
                if isinstance(value, str):
                    lines.append(f"    {key}: {value!r}")
                else:
                    lines.append(f"    {key}: {value!r}")

    if undecided:
        lines.append("  undecided_domains:")
        for d in sorted(undecided):
            lines.append(f"    - {d!r}")
    else:
        lines.append("  undecided_domains: []")

    sunset_path = tmp_path / "tushare_sunset.yaml"
    sunset_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sunset_path


def _write_registry(
    tmp_path: Path,
    *,
    tushare_domains: list[str] | None = None,
    other_domains: dict | None = None,
) -> Path:
    """编写 sync_registry.yaml 临时文件。"""
    lines = [
        "version: 1",
    ]

    # 构建 domains 部分
    domains = {}
    if tushare_domains:
        for domain_name in sorted(tushare_domains):
            domains[domain_name] = {"source": "tushare"}

    if other_domains:
        for domain_name, source in other_domains.items():
            domains[domain_name] = {"source": source}

    # 用 YAML dump 整个结构确保格式正确
    import yaml as yaml_module
    data = {"version": 1, "domains": domains}
    registry_path = tmp_path / "sync_registry.yaml"
    registry_path.write_text(yaml_module.dump(data, default_flow_style=False), encoding="utf-8")
    return registry_path


class TestCoverageComplete:
    """覆盖完整性检查。"""

    def test_all_tushare_domains_declared(self, tmp_path: Path) -> None:
        """registry 的全部 tushare 域都在 sunset 里 → PASS。
        两个域都标 status: done，把检查 4(裁决执行度) 的干扰隔离掉——本用例只测检查 1。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
                "stock_st": {"decision": "replace", "replacement": "baostock", "status": "done"},
            },
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily", "stock_st"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert warns == []

    def test_registry_domain_missing_from_sunset(self, tmp_path: Path) -> None:
        """registry 有 tushare 域但 sunset 缺 → FAIL。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
            },
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily", "stock_st"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert len(fails) == 1
        assert "stock_st" in fails[0]
        assert "registry" in fails[0]


class TestReverseCleanup:
    """反向自清检查。"""

    def test_sunset_domain_no_longer_tushare_warns(self, tmp_path: Path) -> None:
        """sunset 的域在 registry 中已非 tushare 源 → WARN。
        daily 标 status: done，隔离掉检查 4(裁决执行度) 的干扰——本用例只测检查 2。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
                "old_domain": {"decision": "replace", "replacement": "fuyao"},
            },
        )
        registry_path = _write_registry(
            tmp_path,
            tushare_domains=["daily"],
            other_domains={"old_domain": "fuyao"},  # 已非 tushare
        )

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert len(warns) == 1
        assert "old_domain" in warns[0]
        assert "already" in warns[0] or "不是" in warns[0]

    def test_sunset_done_domain_no_warn(self, tmp_path: Path) -> None:
        """sunset 的域已标 status: done 即使不是 tushare 源 → 不报 WARN。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
                "old_domain": {
                    "decision": "replace",
                    "replacement": "fuyao",
                    "status": "done",
                },
            },
        )
        registry_path = _write_registry(
            tmp_path,
            tushare_domains=["daily"],
            other_domains={"old_domain": "fuyao"},
        )

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert warns == []


class TestExpirationCountdown:
    """到期倒计时检查。"""

    def test_all_decided_no_warn(self, tmp_path: Path) -> None:
        """全部已裁决，无未裁决域 → PASS，warns=0。
        daily 标 status: done，隔离掉检查 4(裁决执行度) 的干扰——本用例只测检查 3。"""
        sunset_path = _write_sunset(
            tmp_path,
            expires="2026-09-10",
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
            },
            undecided=[],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert warns == []

    def test_undecided_30_days_left_warns_with_names(self, tmp_path: Path) -> None:
        """距到期 30 天且有未裁决域 → WARN 且消息里出现全部域名。"""
        expires = (date(2026, 8, 31) + timedelta(days=30)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
            },
            undecided=["domain_a", "domain_b", "domain_c"],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert len(warns) == 1
        # 验证所有未裁决域名都在输出中
        assert "domain_a" in warns[0]
        assert "domain_b" in warns[0]
        assert "domain_c" in warns[0]

    def test_undecided_5_days_left_warns_highlighted(self, tmp_path: Path) -> None:
        """距到期 5 天且有未裁决域 → WARN 但不 FAIL，消息醒目。"""
        expires = (date(2026, 8, 31) + timedelta(days=5)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={},
            undecided=["domain_urgent"],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []  # 不 FAIL
        assert len(warns) == 1
        # 应包含醒目提示（如 emoji 或关键词）
        assert "domain_urgent" in warns[0]
        # 验证没有 FAIL 条件
        rc = gate.main(
            [
                "--sunset", str((tmp_path / "tushare_sunset.yaml")),
                "--registry", str((tmp_path / "sync_registry.yaml")),
                "--today", "20260831",
            ]
        )
        assert rc == 0  # warn-only 不阻断

    def test_expired_with_undecided_fails(self, tmp_path: Path) -> None:
        """已过期且有未裁决域 → FAIL 退出码非 0。"""
        # 设到期日为昨天
        expires = (date(2026, 8, 31) - timedelta(days=1)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={},
            undecided=["domain_critical"],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert len(fails) == 1
        assert "已于" in fails[0] or "过期" in fails[0]
        assert "domain_critical" in fails[0]

        # 测试退出码
        rc = gate.main(
            [
                "--sunset", str(sunset_path),
                "--registry", str(registry_path),
                "--today", "20260831",
            ]
        )
        assert rc != 0

    def test_expired_all_decided_passes(self, tmp_path: Path) -> None:
        """已过期但全部已裁决 → PASS。
        daily 标 status: done，隔离掉检查 4(裁决执行度) 的干扰——本用例只测检查 3。"""
        # 设到期日为昨天
        expires = (date(2026, 8, 31) - timedelta(days=1)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
            },
            undecided=[],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert warns == []


class TestPolicyErrors:
    """政策文件缺失/不合法的处理。"""

    def test_missing_sunset_raises_policy_error(self, tmp_path: Path) -> None:
        """sunset.yaml 缺失 → PolicyError。"""
        missing_sunset = tmp_path / "missing.yaml"
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        with pytest.raises(gate.PolicyError, match="missing.*sunset"):
            gate.run(missing_sunset, registry_path, today=date(2026, 8, 31))

    def test_malformed_sunset_raises_policy_error(self, tmp_path: Path) -> None:
        """sunset.yaml 格式不合法 → PolicyError。"""
        sunset_path = tmp_path / "bad_sunset.yaml"
        sunset_path.write_text("{ invalid yaml", encoding="utf-8")
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        with pytest.raises(gate.PolicyError, match="unreadable.*sunset"):
            gate.run(sunset_path, registry_path, today=date(2026, 8, 31))

    def test_missing_authorization_expires_raises_policy_error(self, tmp_path: Path) -> None:
        """sunset.yaml 缺 authorization_expires → PolicyError。"""
        lines = ["version: 1", "domains: {}"]
        sunset_path = tmp_path / "bad_sunset.yaml"
        sunset_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        with pytest.raises(gate.PolicyError, match="authorization_expires"):
            gate.run(sunset_path, registry_path, today=date(2026, 8, 31))


class TestDecisionValidation:
    """检查 0: decision 值域校验 (task A)。

    真实台账实测: 42 个域里存在 unknown/retire/accept_outage 三个 legend 未定义的值，
    而旧代码对此零校验。这里锁住新行为: unknown 不合法、retire/accept_outage 合法、
    缺字段与写了非法值必须报不同文案。
    """

    def test_illegal_decision_value_fails(self, tmp_path: Path) -> None:
        """decision 写了一个完全不存在的值 → FAIL，消息含域名+非法值+合法集。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={"weird_domain": {"decision": "bogus_value"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, _warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        hits = [f for f in fails if "weird_domain" in f]
        assert len(hits) == 1
        assert "bogus_value" in hits[0]
        for legal in gate.LEGAL_DECISIONS:
            assert legal in hits[0]

    def test_unknown_is_illegal(self, tmp_path: Path) -> None:
        """历史遗留字面量 'unknown' 不在合法集里 → FAIL (它不是合法裁决状态)。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={"legacy_domain": {"decision": "unknown"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, _warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert any("legacy_domain" in f and "unknown" in f for f in fails)

    def test_retire_and_accept_outage_are_legal(self, tmp_path: Path) -> None:
        """retire / accept_outage 台账里实际在用且语义清晰 → 必须判合法，不报 FAIL。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "retired_domain": {"decision": "retire"},
                "outage_domain": {"decision": "accept_outage"},
            },
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, _warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        decision_fails = [f for f in fails if "retired_domain" in f or "outage_domain" in f]
        assert decision_fails == []

    def test_missing_decision_field_differs_from_unknown_message(self, tmp_path: Path) -> None:
        """decision 字段缺失 与 decision: unknown 是两种不同的配置错误 (该去补字段 vs
        该去改字面量)，旧代码 entry.get("decision", "unknown") 把两者压成同一个值，
        这里必须能区分——两条报错文案不能相同，且缺失那条要点名"缺失"。
        """
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "missing_domain": {"evidence": "no decision field at all"},
                "legacy_domain": {"decision": "unknown"},
            },
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, _warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        missing_msgs = [f for f in fails if "missing_domain" in f]
        legacy_msgs = [f for f in fails if "legacy_domain" in f]
        assert len(missing_msgs) == 1
        assert len(legacy_msgs) == 1
        assert missing_msgs[0] != legacy_msgs[0]
        assert "缺失" in missing_msgs[0]
        assert "缺失" not in legacy_msgs[0]

    @pytest.mark.parametrize("legal_value", sorted(gate.LEGAL_DECISIONS))
    def test_all_legal_values_pass_validate_decisions(self, legal_value: str) -> None:
        """LEGAL_DECISIONS 里的每个值单独喂给 validate_decisions 都不应产生 fail。"""
        sunset = {"domains": {"d1": {"decision": legal_value}}}
        assert gate.validate_decisions(sunset) == []


class TestUndecidedUnion:
    """检查 3 的未裁决域来源 = undecided_domains 列表 ∪ decision == "undecided" 条目 (task B)。

    根因: 真实台账里 undecided_domains 列表恒为空，"未裁决"全部以
    ``decision: undecided`` 条目的形式存在，旧代码只读列表 → 检查 3 的整个分支
    (含 FAIL 分支) 在生产从未执行过。这里锁住"以条目形式存在也必须被捕获"。
    """

    def test_decision_undecided_entry_without_list_membership_still_counted(
        self, tmp_path: Path
    ) -> None:
        """域只以 decision: undecided 条目形式存在、不在 undecided_domains 列表里
        (复现真实台账形态) → 仍必须进入倒计时检查，否则检查 3 又是死代码。

        orphan_undecided 也注册进 registry_tushare (仍是活跃 tushare 域，只是没裁决)，
        daily 标 status: done —— 两者都是为了隔离检查 2/检查 4 的干扰，本用例只测检查 3。
        """
        sunset_path = _write_sunset(
            tmp_path,
            expires="2026-09-10",
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
                "orphan_undecided": {"decision": "undecided"},
            },
            undecided=[],  # 列表故意留空 — 复现生产态
        )
        registry_path = _write_registry(
            tmp_path, tushare_domains=["daily", "orphan_undecided"]
        )

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        countdown_warns = [w for w in warns if "orphan_undecided" in w]
        assert len(countdown_warns) == 1

    def test_union_dedupes_when_domain_appears_both_ways(self, tmp_path: Path) -> None:
        """同一个域既在 undecided_domains 列表又有 decision: undecided 条目
        → 只计入一次，不重复；且列表里独有的域也不能丢 (向后兼容)。
        daily 标 status: done，隔离掉检查 4 的干扰——本用例只测检查 3。"""
        sunset_path = _write_sunset(
            tmp_path,
            expires="2026-09-10",
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
                "dual_listed": {"decision": "undecided"},
            },
            undecided=["dual_listed", "list_only"],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert len(warns) == 1
        assert warns[0].count("dual_listed") == 1
        assert "list_only" in warns[0]

    def test_expired_with_only_entry_form_undecided_fails(self, tmp_path: Path) -> None:
        """已过期 + 唯一的未裁决域是"条目形式"(不在列表里) → 仍必须 FAIL。
        这是检查 3 死代码最危险的表现: 生产台账里真出现这种域, 过期后必须能拦。"""
        expires = (date(2026, 8, 31) - timedelta(days=1)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={"orphan_critical": {"decision": "undecided"}},
            undecided=[],
        )
        registry_path = _write_registry(tmp_path, tushare_domains=[])

        fails, _warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert len(fails) == 1
        assert "orphan_critical" in fails[0]


class TestDecisionExecutionDrift:
    """检查 4: 裁决执行度 —— 声明要换源却没换 (task C)。业主已解除断流时限压力，
    所以这条检查报 warn 不报 fail: 价值是让"声明与实际"的漂移可见，不是催今天切换。
    """

    def test_declared_replace_not_switched_warns(self, tmp_path: Path) -> None:
        sunset_path = _write_sunset(
            tmp_path,
            domains={"daily": {"decision": "replace", "replacement": "tdxhub"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])  # 仍是 tushare

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert any("daily" in w and "tdxhub" in w for w in warns)

    def test_declared_derive_not_switched_warns(self, tmp_path: Path) -> None:
        sunset_path = _write_sunset(
            tmp_path,
            domains={"stk_limit": {"decision": "derive"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["stk_limit"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert any("stk_limit" in w for w in warns)

    def test_status_done_suppresses_drift_warn(self, tmp_path: Path) -> None:
        """已标 status: done 不算漂移，即便 registry 里仍是 tushare (不该发生，但至少不重复报)。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"},
            },
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert [w for w in warns if "漂移" in w] == []

    def test_decision_accept_is_not_drift(self, tmp_path: Path) -> None:
        """decision: accept (非 replace/derive) 本就不该换源，不算漂移。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={"cyq_perf": {"decision": "accept"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["cyq_perf"])

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert [w for w in warns if "漂移" in w] == []

    def test_already_switched_is_not_drift(self, tmp_path: Path) -> None:
        """registry 里已经不是 tushare 了 (真的切完了) → 不算漂移 (那是检查 2 的地盘)。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={"daily": {"decision": "replace", "replacement": "tdxhub", "status": "done"}},
        )
        registry_path = _write_registry(tmp_path, other_domains={"daily": "tdxhub"})

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        assert fails == []
        assert [w for w in warns if "漂移" in w] == []

    def test_drift_warns_are_single_aggregated_not_per_domain(self, tmp_path: Path) -> None:
        """多个漂移域必须汇总成一条 warn，不能逐域刷屏。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
                "adj_factor": {"decision": "replace", "replacement": "tdxhub"},
                "stk_limit": {"decision": "derive"},
            },
        )
        registry_path = _write_registry(
            tmp_path, tushare_domains=["daily", "adj_factor", "stk_limit"]
        )

        fails, warns = gate.run(sunset_path, registry_path, today=date(2026, 8, 31))
        drift_warns = [w for w in warns if "漂移" in w]
        assert len(drift_warns) == 1
        assert "daily" in drift_warns[0]
        assert "adj_factor" in drift_warns[0]
        assert "stk_limit" in drift_warns[0]

    def test_drift_is_warn_not_fail_exit_code(self, tmp_path: Path) -> None:
        """检查 4 报 warn 不 fail —— 业主已解除时限压力，绝不能卡死提交。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={"daily": {"decision": "replace", "replacement": "tdxhub"}},
        )
        registry_path = _write_registry(tmp_path, tushare_domains=["daily"])

        rc = gate.main(
            [
                "--sunset", str(sunset_path),
                "--registry", str(registry_path),
                "--today", "20260831",
            ]
        )
        assert rc == 0


class TestRealConfigInvariants:
    """直接读仓库里真实的 tushare_sunset.yaml (不用 tmp_path fixture)，
    但只断言不变量，不断言会随工作推进漂移的运行时测量值。
    """

    def test_all_real_decisions_are_legal(self) -> None:
        """真实台账里每个域的 decision 都必须在 LEGAL_DECISIONS 里 —— 这条永远该成立，
        与"还有多少域待裁决"这类工作进度无关: 裁决内容可以变，但绝不能写出合法集之外的值
        (含缺字段)。这是本次整改的直接动机: 42 个域里曾有 4 个字面写着 unknown。"""
        sunset = gate.load_sunset(gate.DEFAULT_SUNSET)
        fails = gate.validate_decisions(sunset)
        assert fails == [], f"真实配置里出现非法 decision 值/缺字段: {fails}"
