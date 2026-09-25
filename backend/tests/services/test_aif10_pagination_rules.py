"""``aif10_pagination_rules.py`` loader 判据 (2026-09-25 刀 A, spec_holders_
pagination.md §7.3 + build_holders_cutA.md §15.2 S6 的刀 A 部分)。

L1 读真实 ``backend/config/aif10_pagination.yaml``; 其余用 ``tmp_path`` 注入
最小合法结构再逐项改坏, 断言精确的 ``AIF10PaginationRulesError`` 前缀。
"""
from __future__ import annotations

import copy

import pytest
import yaml

from aif10_scraper.registry import REPORT_BY_NAME
from services.data_sources.aif10_pagination_rules import (
    AIF10PaginationRulesError,
    holders_notice_recheck,
    load_aif10_pagination_rules,
    page_size_for,
    policy_for,
)


def _base_config() -> dict:
    return {
        "version": 2,
        "reports": {
            "RPT_F10_EH_FREEHOLDERS": {
                "sort_columns": "END_DATE,SECURITY_CODE,HOLDER_RANK,HOLDER_NAME",
                "sort_types": "-1,1,1,1",
                "identity_columns": ["SECURITY_CODE", "END_DATE", "HOLDER_RANK", "HOLDER_NAME"],
                "page_size": 500,
                "row_tolerance_rows": 0,
                "duplicates": "error",
                "drift_refetch": 0,
                "evidence": "",
            },
        },
        "holders_notice_recheck": {
            "settle_days": 1,
            "evidence": "unit test fixture",
        },
        "audits": {
            "holders_notice_pagination": {
                "exposure_start": "20260701",
                "dup_scan": "all_partitions",
                "evidence": "unit test fixture",
            },
        },
    }


def _write(tmp_path, cfg: dict):
    path = tmp_path / "aif10_pagination.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# L1 — 读真实 YAML
# ---------------------------------------------------------------------------


def test_L1_real_config_loads():
    rules = load_aif10_pagination_rules()
    expected_reports = {
        "RPT_F10_EH_FREEHOLDERS",
        "RPT_F10_EH_HOLDERS",
        "RPT_OPERATEDEPT_TRADE",
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "RPT_DATA_BLOCKTRADE",
        "RPT_DMSK_HOLDERS",
    }
    assert set(rules.reports) == expected_reports
    for name in expected_reports:
        policy_for(name, rules=rules)
        page_size_for(name, rules=rules)

    free = rules.reports["RPT_F10_EH_FREEHOLDERS"]
    assert set(free.identity_columns) == {"SECURITY_CODE", "END_DATE", "HOLDER_RANK", "HOLDER_NAME"}
    assert free.drift_refetch == 1
    assert free.row_tolerance_rows == 0
    assert free.duplicates == "error"

    assert holders_notice_recheck(rules=rules).settle_days == 1

    with pytest.raises(AIF10PaginationRulesError):
        policy_for("RPT_MAIN_ORGHOLDDETAIL", rules=rules)


# ---------------------------------------------------------------------------
# L2 — 未知键 (报表级 / 根级 / holders_notice_recheck 级各一变体)
# ---------------------------------------------------------------------------


def test_L2_unknown_key_at_report_level_fails(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["extra_bogus_key"] = 1
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="^aif10_pagination:"):
        load_aif10_pagination_rules(path)


def test_L2_unknown_key_at_root_level_fails(tmp_path):
    cfg = _base_config()
    cfg["extra_bogus_section"] = {}
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="^aif10_pagination:"):
        load_aif10_pagination_rules(path)


def test_L2_unknown_key_at_holders_notice_recheck_level_fails(tmp_path):
    cfg = _base_config()
    cfg["holders_notice_recheck"]["recheck_floor"] = "20260701"
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="^aif10_pagination:"):
        load_aif10_pagination_rules(path)


def test_L2_max_days_per_run_is_also_an_unknown_key(tmp_path):
    """2026-09-25 主循环追加 (S6): max_days_per_run 不进 YAML (跑批预算不是
    判据), 刀 B 用代码常量 —— 加进本节即未知键。"""
    cfg = _base_config()
    cfg["holders_notice_recheck"]["max_days_per_run"] = 40
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="^aif10_pagination:"):
        load_aif10_pagination_rules(path)


# ---------------------------------------------------------------------------
# L3 — 悬空引用 (registry 里没有这个 report_name)
# ---------------------------------------------------------------------------


def test_L3_report_not_in_registry(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_NOPE"] = cfg["reports"].pop("RPT_F10_EH_FREEHOLDERS")
    path = _write(tmp_path, cfg)
    assert "RPT_NOPE" not in REPORT_BY_NAME
    with pytest.raises(AIF10PaginationRulesError, match="RPT_NOPE"):
        load_aif10_pagination_rules(path)


# ---------------------------------------------------------------------------
# L4 — identity_columns 不是 sort_columns 的子集
# ---------------------------------------------------------------------------


def test_L4_identity_not_subset_of_sort(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["identity_columns"] = ["NOT_IN_SORT_COLUMNS"]
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="NOT_IN_SORT_COLUMNS"):
        load_aif10_pagination_rules(path)


# ---------------------------------------------------------------------------
# L5 — duplicates: allow 或 drift_refetch > 0 时 evidence 必须非空
# ---------------------------------------------------------------------------


def test_L5_duplicates_allow_requires_evidence(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["identity_columns"] = []
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["duplicates"] = "allow"
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["evidence"] = ""
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="evidence"):
        load_aif10_pagination_rules(path)


def test_L5_drift_refetch_requires_evidence(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["drift_refetch"] = 1
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["evidence"] = ""
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="evidence"):
        load_aif10_pagination_rules(path)


# ---------------------------------------------------------------------------
# L6 — registry 排序键与 YAML 相等 (两处定义、一道相等锁)
# ---------------------------------------------------------------------------


def test_L6_registry_sort_equals_yaml():
    rules = load_aif10_pagination_rules()
    compared = 0
    for report_name, rule in rules.reports.items():
        spec = REPORT_BY_NAME[report_name]
        if not spec.sort_columns:
            # registry 该字段非空时才比较 (RPT_OPERATEDEPT_TRADE 在 registry 里
            # 没有 sort_columns, YAML 是它唯一定义点)。
            continue
        compared += 1
        assert spec.sort_columns == rule.sort_columns, report_name
        assert spec.sort_types == rule.sort_types, report_name
    # 防「全部跳过」假绿: 至少 5 张报表被真正比较过。
    assert compared >= 5


# ---------------------------------------------------------------------------
# L7 — 未登记报表 fail-closed (不给缺省策略)
# ---------------------------------------------------------------------------


def test_L7_unlisted_report_fails_closed():
    assert "RPT_F10_EH_HOLDERNUM" in REPORT_BY_NAME  # 真实注册但没进 YAML
    with pytest.raises(AIF10PaginationRulesError):
        policy_for("RPT_F10_EH_HOLDERNUM")


# ---------------------------------------------------------------------------
# L8 — holders_notice_recheck / audits 节的类型化校验
# ---------------------------------------------------------------------------


def test_L8_settle_days_must_be_at_least_1(tmp_path):
    cfg = _base_config()
    cfg["holders_notice_recheck"]["settle_days"] = 0
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="settle_days"):
        load_aif10_pagination_rules(path)


def test_L8_settle_days_must_be_int(tmp_path):
    cfg = _base_config()
    cfg["holders_notice_recheck"]["settle_days"] = "1"
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="settle_days"):
        load_aif10_pagination_rules(path)


def test_L8_dup_scan_must_be_all_partitions(tmp_path):
    cfg = _base_config()
    cfg["audits"]["holders_notice_pagination"]["dup_scan"] = "window"
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="dup_scan"):
        load_aif10_pagination_rules(path)


def test_L8_exposure_start_must_be_yyyymmdd(tmp_path):
    cfg = _base_config()
    cfg["audits"]["holders_notice_pagination"]["exposure_start"] = "2026-07-01"
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="exposure_start"):
        load_aif10_pagination_rules(path)


# ---------------------------------------------------------------------------
# S6 的刀 A 部分 — identity_columns 非空 ⇒ row_tolerance_rows == 0
# ---------------------------------------------------------------------------


def test_identity_columns_nonempty_requires_zero_tolerance(tmp_path):
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["row_tolerance_rows"] = 5
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["evidence"] = "some evidence"
    path = _write(tmp_path, cfg)
    with pytest.raises(AIF10PaginationRulesError, match="row_tolerance_rows"):
        load_aif10_pagination_rules(path)


def test_identity_columns_empty_allows_nonzero_tolerance(tmp_path):
    """隔离对照: 其它条件不变, identity_columns 清空后同样的容差必须放行
    (证明只有「identity 非空」这一个条件在挡)。"""
    cfg = _base_config()
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["identity_columns"] = []
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["duplicates"] = "allow"
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["row_tolerance_rows"] = 5
    cfg["reports"]["RPT_F10_EH_FREEHOLDERS"]["evidence"] = "some evidence"
    path = _write(tmp_path, cfg)
    rules = load_aif10_pagination_rules(path)
    assert rules.reports["RPT_F10_EH_FREEHOLDERS"].row_tolerance_rows == 5


def test_base_config_fixture_is_itself_valid(tmp_path):
    """健康检查: _base_config() 本身必须能通过 loader (否则上面每条"改坏一处"
    的用例都测不出它声称测的东西 —— memory: 测试要单独考到每个门控条件)。"""
    path = _write(tmp_path, _base_config())
    rules = load_aif10_pagination_rules(path)
    assert set(rules.reports) == {"RPT_F10_EH_FREEHOLDERS"}


def test_deepcopy_isolation_sanity():
    """_base_config() 每次调用返回全新字典 (不是共享可变默认值), 否则一个用例
    改了 cfg 会污染另一个用例。"""
    a = _base_config()
    b = _base_config()
    a["reports"]["RPT_F10_EH_FREEHOLDERS"]["page_size"] = 999
    assert b["reports"]["RPT_F10_EH_FREEHOLDERS"]["page_size"] == 500
    assert copy.deepcopy(a) is not a
