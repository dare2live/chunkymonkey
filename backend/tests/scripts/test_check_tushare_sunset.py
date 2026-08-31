"""check_tushare_sunset.py 的机械锁 (TuShare 授权到期风险门).

**必须自带 fixture, 不许依赖当前仓库的真实配置** — 每个用例在 ``tmp_path`` 里
建一个全新的 sunset.yaml + registry.yaml，从不读真文件。
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
        """registry 的全部 tushare 域都在 sunset 里 → PASS。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
                "stock_st": {"decision": "replace", "replacement": "baostock"},
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
        """sunset 的域在 registry 中已非 tushare 源 → WARN。"""
        sunset_path = _write_sunset(
            tmp_path,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
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
                "daily": {"decision": "replace", "replacement": "tdxhub"},
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
        """全部已裁决，无未裁决域 → PASS，warns=0。"""
        sunset_path = _write_sunset(
            tmp_path,
            expires="2026-09-10",
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
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
                "daily": {"decision": "replace", "replacement": "tdxhub"},
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
        """已过期但全部已裁决 → PASS。"""
        # 设到期日为昨天
        expires = (date(2026, 8, 31) - timedelta(days=1)).strftime("%Y-%m-%d")
        sunset_path = _write_sunset(
            tmp_path,
            expires=expires,
            domains={
                "daily": {"decision": "replace", "replacement": "tdxhub"},
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
