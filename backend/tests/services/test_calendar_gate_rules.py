"""backend/config/calendar_gate.yaml typed loader 测试 (cut_calendar_horizon 2026-09-25).

C8: 真配置可加载 (floor == date(2005,1,4)、warn <= fail) + 每条 fail-closed 校验各自独立
报错 (隔离用例——每条只违反规格 §7.1 里的一个条件, 其它字段全合法)。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "calendar_gate.yaml"
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return p


def _valid_payload() -> dict:
    return {
        "version": 1,
        "serve_projection_floor": "20050104",
        "next_year_entry": {"warn_from": "11-15", "fail_from": "12-20"},
    }


def test_real_config_loads():
    """R1 前提: 真 calendar_gate.yaml 成功且 floor == date(2005,1,4), warn <= fail。"""
    from services.calendar_gate_rules import load_calendar_gate_rules

    cfg = load_calendar_gate_rules()
    assert cfg.version == 1
    assert cfg.serve_projection_floor == date(2005, 1, 4)
    assert cfg.next_year_warn_from <= cfg.next_year_fail_from


def test_missing_config_file_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(CalendarGateRulesError, match="missing calendar_gate.yaml"):
        load_calendar_gate_rules(missing)


def test_malformed_yaml_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    path = tmp_path / "calendar_gate.yaml"
    path.write_text("version: 1\n  bad_indent: [unclosed\n", encoding="utf-8")
    with pytest.raises(CalendarGateRulesError, match="unreadable calendar_gate.yaml"):
        load_calendar_gate_rules(path)


def test_root_not_a_mapping_fails_closed(tmp_path):
    """根是裸标量 (非 dict/list) -> fail closed。用标量而非 list: 若把 _mapping() 校验
    去掉直接透传, 标量在下一步 set(value) 上会抛不成形的 TypeError 而不是
    CalendarGateRulesError —— 用标量能把这条校验和"缺键"校验区分开(list 会被"缺键"
    错误顺带接住, 掩盖 _mapping() 本身被删掉的事实)。"""
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    path = tmp_path / "calendar_gate.yaml"
    path.write_text("42\n", encoding="utf-8")
    with pytest.raises(CalendarGateRulesError, match="root"):
        load_calendar_gate_rules(path)


def test_unknown_root_key_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["extra_bogus_key"] = 1
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="extra_bogus_key"):
        load_calendar_gate_rules(path)


def test_missing_root_key_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    del payload["serve_projection_floor"]
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="serve_projection_floor"):
        load_calendar_gate_rules(path)


def test_unknown_next_year_entry_key_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["next_year_entry"]["extra_bogus_key"] = 1
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="extra_bogus_key"):
        load_calendar_gate_rules(path)


def test_missing_next_year_entry_key_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    del payload["next_year_entry"]["fail_from"]
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="fail_from"):
        load_calendar_gate_rules(path)


def test_version_not_1_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["version"] = 2
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="version"):
        load_calendar_gate_rules(path)


def test_floor_not_compact_fails_closed(tmp_path):
    """floor '200501040' (9 位, 多一位尾缀) -> fail closed。用 9 位而非带连字符的
    '2005-01-04': 后者切片取 [4:6]/[6:8] 时本身就会因非数字段撞上 int() 转换异常, 被
    "日期是否合法"那一步顺带接住, 掩盖"长度是否恰好 8 位"这条校验被删掉的事实; 9 位
    全数字字符串切片后仍能拼出合法日期 (静默丢弃尾缀), 只有长度校验能拦住它。"""
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["serve_projection_floor"] = "200501040"
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="serve_projection_floor"):
        load_calendar_gate_rules(path)


def test_floor_invalid_calendar_date_fails_closed(tmp_path):
    """floor '20051301' (13 月不存在, 紧凑 8 位但不可解析) -> fail closed。"""
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["serve_projection_floor"] = "20051301"
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="serve_projection_floor"):
        load_calendar_gate_rules(path)


def test_warn_from_missing_dash_fails_closed(tmp_path):
    """warn_from '1115' (缺连字符, 不是 MM-DD) -> fail closed。"""
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["next_year_entry"]["warn_from"] = "1115"
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="warn_from"):
        load_calendar_gate_rules(path)


def test_warn_from_invalid_month_fails_closed(tmp_path):
    """warn_from '02-30' (2 月没有 30 号) -> fail closed。用 02-30 而非 13-01: 月份
    13 会让 (13,1) > (12,20) 的 tuple 比较成立, 被"warn<=fail"排序校验顺带接住, 掩盖
    "月日本身是否合法"这条校验被删掉的事实; 02-30 的月份仍在排序比较里小于 fail_from
    的 12 月, 只有 date(2000,2,30) 校验能拦住它。"""
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["next_year_entry"]["warn_from"] = "02-30"
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="warn_from"):
        load_calendar_gate_rules(path)


def test_warn_from_after_fail_from_fails_closed(tmp_path):
    from services.calendar_gate_rules import CalendarGateRulesError, load_calendar_gate_rules

    payload = _valid_payload()
    payload["next_year_entry"]["warn_from"] = "12-25"
    payload["next_year_entry"]["fail_from"] = "12-20"
    path = _write(tmp_path, payload)
    with pytest.raises(CalendarGateRulesError, match="warn_from"):
        load_calendar_gate_rules(path)
