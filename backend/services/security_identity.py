"""证券换码事件登记 -- typed YAML loader (S0 切片, asof_identity_r1.md §2/§3.1).

按日证券身份问题的换码事实不可从供应商代码或 K 线本身推导 (asof_identity_r1.md I7: 清理前
无法只靠价格邻接配对区分真换码与巧合; I10: 妙想内码/北交所对照公式均有失效案例; I11: 沪深 A 股
换码逐案公告, 没有交易所级清单) -- 只能人工登记, 且每条必须带 lineage (红线 14)。

本模块只做纯配置校验: 加载并验证 backend/config/security_code_changes.yaml。不开数据库连接、
不读任何环境变量。把这张登记表与 K 线成员资格结合、判定「某行发布时该算哪个代码」的解析 SQL
属于后续切片 S1, 在本文件同一模块内追加, 不在本片写。

红线对齐 (asof_identity_r1.md §2 裁决 (b)): raw 忠实存供应商代码; 只有发布层读这张表做身份
解析。这张表登记的是「哪个代码在哪天换成哪个代码」这一条历史事实, 不是「哪些行被豁免」--
一条事件能解释多少行, 由发布层按自然键全同现比现算 (§3.3 R1), 不由这里的人工圈定行数或日期
范围决定。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CODE_CHANGES_YAML = _BACKEND_DIR / "config" / "security_code_changes.yaml"

_ALLOWED_TOP_KEYS = {"version", "events"}
_EVENT_KEYS = {
    "old_code",
    "new_code",
    "effective_date",
    "exchange",
    "kind",
    "source_kind",
    "source_ref",
    "checked_at",
}
_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_EFFECTIVE_DATE_RE = re.compile(r"^\d{8}$")
_KIND_VALUES = {"reorg_rename", "bse_920_migration"}
_SOURCE_KIND_VALUES = {"announcement", "kline_succession_observed"}


@dataclass(frozen=True)
class CodeChangeEvent:
    old_code: str
    new_code: str
    effective_date: str
    exchange: str
    kind: str
    source_kind: str
    source_ref: str
    checked_at: str


@dataclass(frozen=True)
class CodeChangeSet:
    events: tuple[CodeChangeEvent, ...]
    by_new: Mapping[str, CodeChangeEvent]
    by_old: Mapping[str, CodeChangeEvent]
    sha256: str


def _require_code(p: Path, i: int, key: str, value: object) -> str:
    if not isinstance(value, str) or not _CODE_RE.match(value):
        raise ValueError(
            f"{p}: events[{i}].{key} = {value!r} does not match ^\\d{{6}}\\.(SH|SZ|BJ)$"
        )
    return value


def _require_effective_date(p: Path, i: int, value: object) -> str:
    if not isinstance(value, str) or not _EFFECTIVE_DATE_RE.match(value):
        raise ValueError(
            f"{p}: events[{i}].effective_date = {value!r} must be an 8-digit YYYYMMDD string"
        )
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(
            f"{p}: events[{i}].effective_date = {value!r} is not a valid calendar date"
        ) from exc
    return value


def _require_nonempty_str(p: Path, i: int, key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{p}: events[{i}].{key} must be a non-empty string, got {value!r}")
    return value


def _require_enum(p: Path, i: int, key: str, value: object, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{p}: events[{i}].{key} = {value!r} not in {sorted(allowed)}")
    return value


def load_security_code_changes(path: Path | None = None) -> CodeChangeSet:
    """Load + validate backend/config/security_code_changes.yaml.

    Fail-closed (asof_identity_r1.md §3.1, 红线 11/14). Every failure below
    raises ``ValueError`` naming the offending ``events[i]`` index and key:

    - root is not a mapping, or its top-level keys are not exactly
      ``{version, events}``;
    - ``events`` is missing, empty, or not a list;
    - an event's keys are not exactly the 8 required keys (old_code,
      new_code, effective_date, exchange, kind, source_kind, source_ref,
      checked_at) -- a missing or an extra key both fail;
    - ``old_code``/``new_code`` do not match ``^\\d{6}\\.(SH|SZ|BJ)$``;
    - ``old_code == new_code``;
    - ``effective_date`` is not an 8-digit string that is also a real
      calendar date (e.g. "20250230" fails: Feb has no 30th);
    - ``exchange``/``source_ref``/``checked_at`` are not non-empty strings;
    - ``kind`` is not in {reorg_rename, bse_920_migration};
    - ``source_kind`` is not in {announcement, kline_succession_observed};
    - the same ``old_code`` (or the same ``new_code``) appears in more than
      one event;
    - a code-change chain (event A's new_code feeds event B's old_code) has
      a non strictly increasing ``effective_date`` from A to B;
    - the old_code/new_code edges form a cycle (e.g. A->B and B->A) --
      detected by graph structure alone, independently of the chain-date
      check above, so it still fires even if that check is disabled.

    Pure config validation: this function opens no database connection and
    reads no environment variable. Consuming the returned ``CodeChangeSet``
    against a K-line table (§3.2/§3.3) is slice S1's job, appended later to
    this same module.
    """
    p = Path(path) if path is not None else _DEFAULT_CODE_CHANGES_YAML
    raw_bytes = p.read_bytes()
    doc = yaml.safe_load(raw_bytes.decode("utf-8"))

    if not isinstance(doc, dict):
        raise ValueError(f"{p}: root must be a mapping, got {type(doc).__name__}")
    top_keys = set(doc.keys())
    if top_keys != _ALLOWED_TOP_KEYS:
        raise ValueError(
            f"{p}: top-level keys must be exactly {sorted(_ALLOWED_TOP_KEYS)}, "
            f"got {sorted(top_keys)}"
        )

    # version 是契约版本: 值变了意味着这份登记的形状可能已经不是 loader 认识的那一份,
    # 所以跟未知键一样 fail-closed (与 reland_event_domain.load_vendor_gaps 同口径)。
    if doc.get("version") != 1:
        raise ValueError(f"{p}: version must be 1, got {doc.get('version')!r}")

    events_raw = doc["events"]
    if not isinstance(events_raw, list) or not events_raw:
        raise ValueError(f"{p}: events must be a non-empty list")

    events: list[CodeChangeEvent] = []
    by_old: dict[str, CodeChangeEvent] = {}
    by_new: dict[str, CodeChangeEvent] = {}
    index_of_old: dict[str, int] = {}
    index_of_new: dict[str, int] = {}

    for i, body in enumerate(events_raw):
        if not isinstance(body, dict):
            raise ValueError(f"{p}: events[{i}] must be a mapping, got {type(body).__name__}")
        keys = set(body.keys())
        if keys != _EVENT_KEYS:
            raise ValueError(
                f"{p}: events[{i}] keys must be exactly {sorted(_EVENT_KEYS)}; "
                f"missing={sorted(_EVENT_KEYS - keys)} extra={sorted(keys - _EVENT_KEYS)}"
            )

        old_code = _require_code(p, i, "old_code", body["old_code"])
        new_code = _require_code(p, i, "new_code", body["new_code"])
        if old_code == new_code:
            raise ValueError(
                f"{p}: events[{i}].old_code == events[{i}].new_code == {old_code!r}"
            )

        effective_date = _require_effective_date(p, i, body["effective_date"])
        exchange = _require_nonempty_str(p, i, "exchange", body["exchange"])
        kind = _require_enum(p, i, "kind", body["kind"], _KIND_VALUES)
        source_kind = _require_enum(
            p, i, "source_kind", body["source_kind"], _SOURCE_KIND_VALUES
        )
        source_ref = _require_nonempty_str(p, i, "source_ref", body["source_ref"])
        checked_at = _require_nonempty_str(p, i, "checked_at", body["checked_at"])

        if old_code in index_of_old:
            j = index_of_old[old_code]
            raise ValueError(
                f"{p}: events[{i}].old_code {old_code!r} duplicates events[{j}].old_code"
            )
        if new_code in index_of_new:
            j = index_of_new[new_code]
            raise ValueError(
                f"{p}: events[{i}].new_code {new_code!r} duplicates events[{j}].new_code"
            )

        event = CodeChangeEvent(
            old_code=old_code,
            new_code=new_code,
            effective_date=effective_date,
            exchange=exchange,
            kind=kind,
            source_kind=source_kind,
            source_ref=source_ref,
            checked_at=checked_at,
        )
        events.append(event)
        by_old[old_code] = event
        by_new[new_code] = event
        index_of_old[old_code] = i
        index_of_new[new_code] = i

    # Cycle detection: pure graph structure (old_code -> new_code edges),
    # independent of effective_date. Because every code is at most one
    # event's old_code and at most one event's new_code (enforced above),
    # this is a functional graph (out-degree <= 1) -- three-colour DFS finds
    # a cycle by revisiting a node still "in progress" on the current walk.
    codes = set(by_old) | set(by_new)
    color: dict[str, int] = {}  # 0 unseen, 1 in progress, 2 done
    for start in codes:
        if color.get(start, 0) != 0:
            continue
        path: list[str] = []
        node: str | None = start
        while node is not None and color.get(node, 0) == 0:
            color[node] = 1
            path.append(node)
            nxt = by_old.get(node)
            node = nxt.new_code if nxt is not None else None
        if node is not None and color.get(node) == 1:
            cycle = path[path.index(node):] + [node]
            raise ValueError(f"{p}: code-change cycle detected: {' -> '.join(cycle)}")
        for c in path:
            color[c] = 2

    # Chain must strictly increase: if event P's new_code feeds event E's
    # old_code (P happened, then the resulting code changed again to become
    # E), E must be dated strictly after P.
    for i, event in enumerate(events):
        pred = by_new.get(event.old_code)
        if pred is None:
            continue
        j = index_of_new[event.old_code]
        if event.effective_date <= pred.effective_date:
            raise ValueError(
                f"{p}: events[{i}].effective_date {event.effective_date!r} must be strictly "
                f"greater than events[{j}].effective_date {pred.effective_date!r} "
                f"({pred.old_code}->{pred.new_code}->{event.new_code} chain not increasing)"
            )

    return CodeChangeSet(
        events=tuple(events),
        by_new=MappingProxyType(dict(by_new)),
        by_old=MappingProxyType(dict(by_old)),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
