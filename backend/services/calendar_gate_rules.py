"""calendar_gate_rules — check_continuity_integrity 日历门参数 typed 配置
(backend/config/calendar_gate.yaml)。

单一定义点: dim_trading_calendar 应从哪天起服务 (serve_projection_floor) + 下一年节假日
未按期录入的 WARN/FAIL 月日 (next_year_entry)。取代 cut_calendar_horizon 之前散落在
check_continuity_integrity.py 里的字面量 (CALENDAR_HORIZON_MIN_TRADING_DAYS=60 等,
2026-09-25) —— 日历换成规则推导后, "还能看多远" 不再由供应商决定, 约束变成"当年节假日
配置存在"与"下一年在国务院公布后按期录入"两条, 门要问的参数因此改变。

loader 放在独立文件而非 check_continuity_integrity.py: 前者是"规则"(阈值/日期, typed
YAML + fail-closed 校验), 后者是"机制"(怎么判定一次日历检查) —— CLAUDE.md #11/#13
"规则进 typed YAML 不 hardcode"、"一件事若无法机器验证, 写进规则, 别写进闸"的另一面:
能机器验证的判断 (未知键/格式非法) 就该有独立、可单测的 loader, 不掺进检测逻辑里。

owner: 本文件 + backend/config/calendar_gate.yaml。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "CalendarGateRules",
    "CalendarGateRulesError",
    "load_calendar_gate_rules",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "backend" / "config" / "calendar_gate.yaml"

_ROOT_KEYS = {"version", "serve_projection_floor", "next_year_entry"}
_NEXT_YEAR_ENTRY_KEYS = {"warn_from", "fail_from"}


class CalendarGateRulesError(ValueError):
    """Config 缺键 / 未知键 / 类型不对 / 格式非法 -> fail closed, 不做默认值兜底。"""


@dataclass(frozen=True)
class CalendarGateRules:
    """typed 快照; 由 :func:`load_calendar_gate_rules` 产出, 不接受直接构造。"""

    version: int
    serve_projection_floor: date
    next_year_warn_from: tuple[int, int]
    next_year_fail_from: tuple[int, int]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalendarGateRulesError(f"{field} must be a mapping, got {type(value).__name__}")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise CalendarGateRulesError(f"{field} missing keys: {missing}")
    if unknown:
        raise CalendarGateRulesError(f"{field} unknown keys: {unknown}")


def _parse_compact_date(value: Any, field: str) -> date:
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise CalendarGateRulesError(
            f"{field} must be a compact 8-digit YYYYMMDD string, got {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise CalendarGateRulesError(f"{field} is not a valid calendar date: {value!r}") from exc


def _parse_month_day(value: Any, field: str) -> tuple[int, int]:
    text = str(value).strip()
    if len(text) != 5 or text[2] != "-" or not (text[:2].isdigit() and text[3:].isdigit()):
        raise CalendarGateRulesError(
            f"{field} must be MM-DD (two-digit month and day), got {value!r}")
    month, day = int(text[:2]), int(text[3:])
    try:
        date(2000, month, day)  # 2000 是闰年, 允许 02-29; 只借它校验 month/day 合法范围
    except ValueError as exc:
        raise CalendarGateRulesError(f"{field} is not a valid month/day: {value!r}") from exc
    return (month, day)


def load_calendar_gate_rules(path: Path | str | None = None) -> CalendarGateRules:
    """Load one strict config snapshot.

    文件不存在 / 不可解析 / 根不是 mapping / 根键不恰好是 {version,
    serve_projection_floor, next_year_entry} / next_year_entry 键不恰好是
    {warn_from, fail_from} / version != 1 / floor 不是紧凑 8 位可解析日期 / 月日不是
    MM-DD / warn_from > fail_from 一律 raise CalendarGateRulesError, 不做默认值兜底
    (CLAUDE.md #11: 未知键/悬空引用 fail-closed)。
    """
    p = Path(path) if path is not None else _CONFIG_PATH
    if not p.is_file():
        raise CalendarGateRulesError(f"missing calendar_gate.yaml: {p}")
    try:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CalendarGateRulesError(f"unreadable calendar_gate.yaml: {exc}") from exc
    raw = _mapping(loaded, "root")
    _exact_keys(raw, _ROOT_KEYS, "root")

    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise CalendarGateRulesError(f"version must be exactly 1, got {version!r}")

    floor = _parse_compact_date(raw["serve_projection_floor"], "serve_projection_floor")

    next_year_entry = _mapping(raw["next_year_entry"], "next_year_entry")
    _exact_keys(next_year_entry, _NEXT_YEAR_ENTRY_KEYS, "next_year_entry")
    warn_from = _parse_month_day(next_year_entry["warn_from"], "next_year_entry.warn_from")
    fail_from = _parse_month_day(next_year_entry["fail_from"], "next_year_entry.fail_from")
    if warn_from > fail_from:
        raise CalendarGateRulesError(
            f"next_year_entry.warn_from {warn_from} must be <= fail_from {fail_from}")

    return CalendarGateRules(
        version=version,
        serve_projection_floor=floor,
        next_year_warn_from=warn_from,
        next_year_fail_from=fail_from,
    )
