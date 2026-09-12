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
from typing import Literal, Mapping

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
    # 所以跟未知键一样 fail-closed: 版本不认识就拒载, 不按旧形状猜着读
    # (本仓其它 typed 登记表的 loader 同口径)。
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


# ---------------------------------------------------------------------------
# S1 -- 按日证券身份解析器 (asof_identity_r1.md §3.2/§3.3/§9 S1)
#
# 上面的 loader 只管配置形状; 这里把登记表跟 K 线成员资格 (身份真相, 红线 5)
# 结合起来, 判"这一行发布时该算哪个代码"。两段职责分开:
#
#   1. 前提锁 (kline_entity_duplicate_pairs / assert_identity_rule_valid):
#      §3.2 的 R-K 规则 ("K 线里 (code, day) 有行 = 该代码当天在交易") 只在
#      K 线自己没有"两个不同代码同一天全同"的未登记对时成立 (I2: 现在有,
#      因为 2026-06-11 曾按码全史回写)。解析器每次先跑这段扫描, 未登记的对
#      (或已登记但区间越过 effective_date 的对) 直接 raise, 不 warn 不继续
#      (红线 5: 推导物锁规则有效期, 过期 fail)。
#   2. 逐行解析 (identity_cte_sql / assert_no_unresolved): §3.3 R0-R6。R0
#      (universe 白名单) 由现成的
#      services.data_sources.universe_serve_filter.apply_universe_serve_filter
#      在喂给这里的 SQL 之前先做 ("先过 universe, 再判身份") -- 这里不重复
#      一份前缀判断。R3 (旧码在生效后还出现) 没有独立分支: 落到普通路径后,
#      K 线里那天不会再有旧码的行 (它已经改名了), 自然走不到 R4, 只会落
#      R5 (前沿之后, pending) 或 R6 (前沿之内, unresolved) -- 结果与规则表
#      写的"走 R6"一致 (只要 K 线覆盖到那天)。
#
# raw 忠实存供应商代码不改 (§2 裁决 (b)): 这里只新增列 (vendor_code /
# ts_code_asof / identity_status), 原始列一个不动、一个不删。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentitySpec:
    """一个事件域 (top_inst / block_trade) 的身份解析形状。

    ``natural_key`` 是 R1 判"同批同日是否为同一笔"的自然键 (asof_identity_r1.md
    §3.3 R1; 已实测 I3: top_inst 按 exalter/side/reason/board_rank/buy/sell
    全同才算同一笔, block_trade 按 price/vol/buyer/seller), 与
    ``reland_event_domain.DOMAIN_CANON[domain].key_cols`` 里"码+日"之外的那部分
    同源, 但不是同一个对象 -- 这里的用途是"识别跨代码的同一笔", 那边是"识别
    落地层的到达顺序/唯一性", 两个模块故意不互相 import (施工规格: 不把 DB 路径
    解析与写锁依赖拖进这个纯模块)。
    """

    code_col: str
    date_col: str
    date_format: Literal["yyyymmdd"]
    natural_key: tuple[str, ...]


IDENTITY_SPECS: dict[str, IdentitySpec] = {
    "top_inst": IdentitySpec(
        code_col="ts_code",
        date_col="trade_date",
        date_format="yyyymmdd",
        natural_key=("exalter", "side", "reason", "board_rank", "buy", "sell"),
    ),
    "block_trade": IdentitySpec(
        code_col="ts_code",
        date_col="trade_date",
        date_format="yyyymmdd",
        natural_key=("price", "vol", "buyer", "seller"),
    ),
}


def _identity_spec(domain: str) -> IdentitySpec:
    try:
        return IDENTITY_SPECS[domain]
    except KeyError:
        raise ValueError(
            f"unknown identity domain {domain!r}; expected one of {sorted(IDENTITY_SPECS)}"
        ) from None


def _date_expr(col: str, date_format: str, *, alias: str = "") -> str:
    """SQL expression casting a YYYYMMDD VARCHAR column to DATE.

    Raw domain tables (top_inst/block_trade) store dates as VARCHAR YYYYMMDD
    (matching every other raw table in this codebase); the K 线真相表
    (``canonical_nominal_ohlcv_daily``) stores ``trade_date`` as DATE. This
    is the one place that bridges the two representations -- IdentitySpec's
    ``date_format`` says which parse rule applies, so a future domain with a
    different date shape fails closed here instead of silently miscomparing.
    """
    if date_format != "yyyymmdd":
        raise ValueError(
            f"unsupported IdentitySpec.date_format {date_format!r}; only 'yyyymmdd' is implemented"
        )
    qualified = f"{alias}.{col}" if alias else col
    return f"strptime({qualified}, '%Y%m%d')::DATE"


class IdentityError(Exception):
    """Base class for §3.2/§3.3 identity-resolution failures.

    Deliberately not a ``ValueError`` subclass: the loader above raises plain
    ``ValueError`` for config-shape problems (fixed once the YAML is fixed);
    these two describe a data-state problem (fixed by registering an event,
    or by re-running once new data lands), a different failure family a
    caller may want to catch separately.
    """


class IdentityRuleInvalid(IdentityError):
    """R-K 前提锁失败 (§3.2): K 线里存在一对未登记的实体重复, 或已登记但重复
    区间越过了该事件的 effective_date。身份真相 (K 线成员资格) 在这种状态下
    不可信 -- 解析器拒绝运行, 不猜、不跳过 (红线 5)。"""


class IdentityUnresolvedError(IdentityError):
    """R6 (§3.3): 至少一行既不能由已登记事件解释, 也不在 K 线成员资格里 (不论
    是否在 K 线前沿之后)。fail-closed, 不静默剔除 (红线 3: 缺失只能传播为
    缺失, 不是"当它不存在")。"""


def register_code_changes_temp(
    con, ccs: CodeChangeSet, *, table: str = "security_code_change"
) -> None:
    """把 ``ccs`` 物化成当前连接里的一张 TEMP TABLE, 供 :func:`identity_cte_sql`
    与 :func:`assert_identity_rule_valid` 用 SQL JOIN。``effective_date`` 在
    这里 (Python 侧, 只做一次) 转成 DATE, 调用方不必在每一行 SQL 里重新解析
    YYYYMMDD 字符串。幂等: 重复调用会先 DROP 再重建, 不会累积重复行。
    """
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TEMP TABLE {table} (
            old_code VARCHAR NOT NULL,
            new_code VARCHAR NOT NULL,
            effective_date DATE NOT NULL
        )
        """
    )
    for event in ccs.events:
        con.execute(
            f"INSERT INTO {table} (old_code, new_code, effective_date) VALUES (?, ?, ?)",
            (
                event.old_code,
                event.new_code,
                datetime.strptime(event.effective_date, "%Y%m%d").date(),
            ),
        )


# §3.2 阈值: 全表扫描 2026-09-12 (I2) 长度分布恰为 {221 天: 1 对, 1472 天: 1 对},
# 不存在任何更短的巧合对 -- 阈值定 1 天, 不需要容忍量 (asof_identity_r1.md §3.2)。
_DUPLICATE_DAY_THRESHOLD = 1


def kline_entity_duplicate_pairs(
    con, *, kline_sql: str
) -> list[tuple[str, str, int, str, str]]:
    """扫描 ``kline_sql`` (K 线表名或子查询), 找出"两个不同代码在同一天
    close/vol/amount 全同"的对 (asof_identity_r1.md §3.2 R-K 前提)。

    返回 ``(code_a, code_b, days, first_day, last_day)``, ``code_a < code_b``
    (字符串序, 避免同一对正反各报一次), ``days`` 是重复的交易日数,
    ``first_day``/``last_day`` 是 YYYYMMDD 字符串。只返回 ``days >=
    _DUPLICATE_DAY_THRESHOLD`` 的对。
    """
    sql = f"""
        SELECT
            a.ts_code AS code_a,
            b.ts_code AS code_b,
            COUNT(*) AS n_days,
            strftime(MIN(a.trade_date), '%Y%m%d') AS first_day,
            strftime(MAX(a.trade_date), '%Y%m%d') AS last_day
        FROM {kline_sql} a
        JOIN {kline_sql} b
          ON a.trade_date = b.trade_date
         AND a.ts_code < b.ts_code
         AND a.close = b.close
         AND a.vol = b.vol
         AND a.amount = b.amount
        GROUP BY a.ts_code, b.ts_code
        HAVING COUNT(*) >= {_DUPLICATE_DAY_THRESHOLD}
        ORDER BY a.ts_code, b.ts_code
    """
    rows = con.execute(sql).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4])) for r in rows]


def assert_identity_rule_valid(con, ccs: CodeChangeSet, *, kline_sql: str) -> None:
    """§3.2 前提锁: 每一对 :func:`kline_entity_duplicate_pairs` 找到的 K 线
    实体重复, 必须能对应恰好一条登记事件, 且重复区间必须整段落在
    ``[K 线首日, effective_date)`` 内 (用 ``last_day < effective_date`` 判断
    上界即可 -- ``first_day`` 来自 K 线自己, 下界天然满足)。

    不满足 -> ``IdentityRuleInvalid``, 不 warn、不继续往下解析 (红线 5)。
    """
    for code_a, code_b, n_days, first_day, last_day in kline_entity_duplicate_pairs(
        con, kline_sql=kline_sql
    ):
        event = ccs.by_new.get(code_a)
        if event is None or event.old_code != code_b:
            event = ccs.by_new.get(code_b)
            if event is None or event.old_code != code_a:
                event = None
        if event is None:
            raise IdentityRuleInvalid(
                f"unregistered K-line entity-duplicate pair ({code_a}, {code_b}): "
                f"{n_days} day(s) [{first_day}..{last_day}] match no event in "
                "security_code_changes.yaml"
            )
        if last_day >= event.effective_date:
            raise IdentityRuleInvalid(
                f"K-line entity-duplicate pair ({code_a}, {code_b}) is registered as "
                f"{event.old_code}->{event.new_code} effective {event.effective_date}, "
                f"but the duplicate interval [{first_day}..{last_day}] does not end "
                "before effective_date"
            )


def identity_cte_sql(
    domain: str,
    *,
    src_alias: str,
    kline_alias: str,
    events_table: str = "security_code_change",
) -> str:
    """产出一条自包含的 ``WITH ident AS (...) SELECT * FROM ident`` 查询,
    实现 §3.3 的 R1/R2/R4/R5/R6 (R0 由调用方在喂给这条 SQL 之前, 用现成的
    ``apply_universe_serve_filter`` 先过滤掉, 这里不重复一份前缀判断; R3
    不需要独立分支, 见本节头注)。

    ``ident`` 的列 = ``src_alias`` 的全部原始列 (供应商代码原样不改) + 新增
    ``vendor_code`` (供应商代码的显式拷贝) + ``ts_code_asof`` (解析出的当日
    代码; backfill_duplicate/unresolved 行为 NULL -- 缺失不伪造, 红线 3) +
    ``identity_status``。

    ``src_alias``/``kline_alias`` 是表名或可直接放进 ``FROM`` 的子查询字符串;
    ``events_table`` 默认对应 :func:`register_code_changes_temp` 建的表名。
    """
    spec = _identity_spec(domain)
    code_col, date_col, natural_key = spec.code_col, spec.date_col, spec.natural_key
    if not natural_key:
        raise ValueError(f"IDENTITY_SPECS[{domain!r}].natural_key must be non-empty")

    twin_predicate = " AND ".join(
        f"o.{col} IS NOT DISTINCT FROM s.{col}" for col in natural_key
    )
    row_date_as_date_s = _date_expr(date_col, spec.date_format, alias="s")
    row_date_as_date_t = _date_expr(date_col, spec.date_format, alias="t")

    return f"""
WITH ident_step1 AS (
    SELECT
        s.*,
        s.{code_col} AS vendor_code,
        e.old_code AS _cand_old,
        e.effective_date AS _cand_effective,
        EXISTS (
            SELECT 1 FROM {src_alias} o
            WHERE o.{code_col} = e.old_code
              AND o.{date_col} = s.{date_col}
              AND {twin_predicate}
        ) AS _has_twin
    FROM {src_alias} s
    LEFT JOIN {events_table} e
        ON e.new_code = s.{code_col}
       AND {row_date_as_date_s} < e.effective_date
),
ident AS (
    SELECT
        t.* EXCLUDE (_cand_old, _cand_effective, _has_twin),
        CASE
            WHEN t._cand_effective IS NOT NULL AND t._has_twin THEN NULL
            WHEN t._cand_effective IS NOT NULL AND NOT t._has_twin THEN t._cand_old
            ELSE t.vendor_code
        END AS ts_code_asof,
        CASE
            WHEN t._cand_effective IS NOT NULL AND t._has_twin THEN 'backfill_duplicate'
            WHEN t._cand_effective IS NOT NULL AND NOT t._has_twin THEN
                CASE WHEN EXISTS (
                        SELECT 1 FROM {kline_alias} k
                        WHERE k.ts_code = t._cand_old
                          AND k.trade_date = {row_date_as_date_t}
                     ) THEN 'backfill_remapped'
                     ELSE 'unresolved'
                END
            WHEN EXISTS (
                    SELECT 1 FROM {kline_alias} k
                    WHERE k.ts_code = t.vendor_code
                      AND k.trade_date = {row_date_as_date_t}
                 ) THEN 'kline_confirmed'
            WHEN {row_date_as_date_t} > (SELECT MAX(trade_date) FROM {kline_alias}) THEN 'kline_pending'
            ELSE 'unresolved'
        END AS identity_status
    FROM ident_step1 t
)
SELECT * FROM ident
""".strip()


def assert_no_unresolved(con, *, ident_sql: str, sample: int = 20) -> dict[str, int]:
    """跑 ``ident_sql`` (通常是 :func:`identity_cte_sql` 的返回值) 并统计每种
    ``identity_status`` 的行数; 出现 R6 (``unresolved``) 就 raise, 不静默剔除
    (红线 3/§3.3: "发布 raise 并列出 (code, d) 样本; 不得静默剔除")。

    没有 unresolved 行时返回 ``{status: count}``。
    """
    counts_rows = con.execute(
        f"SELECT identity_status, COUNT(*) FROM ({ident_sql}) GROUP BY 1"
    ).fetchall()
    counts = {str(row[0]): int(row[1]) for row in counts_rows}
    unresolved = counts.get("unresolved", 0)
    if unresolved:
        cur = con.execute(
            f"SELECT * FROM ({ident_sql}) WHERE identity_status = 'unresolved' LIMIT {int(sample)}"
        )
        cols = [d[0] for d in cur.description]
        sample_rows = [dict(zip(cols, list(row))) for row in cur.fetchall()]
        raise IdentityUnresolvedError(
            f"{unresolved} unresolved identity row(s) (§3.3 R6); sample={sample_rows!r}"
        )
    return counts
