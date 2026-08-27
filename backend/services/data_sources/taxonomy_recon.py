"""Read-only four-chain taxonomy recon. Does not cut primaries.

Rulers:
- DC membership = ``fact_dc_member_daily`` (observation-date publication).
- SW membership = ``v_sw_industry_pit`` (interval PIT as-of).
- THS = Fuyao catalog + current constituents (observation snapshot).
``raw_tushare_dc_member`` / ``raw_tushare_index_member_all`` / dim_stock_dc_* /
``v_dc_industry_pit`` are not membership truth. Equal names are candidates,
not identity. TDX ``block`` is not a four-chain namespace.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from services.taxonomy_config import (
    FOUR_CHAIN_NAMESPACES,
    source_index_type,
    source_level_map,
)

DC_MEMBER_PUBLICATION = "fact_dc_member_daily"
SW_PIT_PUBLICATION = "v_sw_industry_pit"
DC_INDEX_CATALOG = "raw_tushare_dc_index"
BANNED_DC_MEMBERSHIP = frozenset(
    {
        "raw_tushare_dc_member",
        "dim_stock_dc_industry",
        "dim_stock_dc_concept",
        "v_dc_industry_pit",
    }
)
BANNED_SW_MEMBERSHIP = frozenset({"raw_tushare_index_member_all"})
BANNED_THS_INTERVAL = frozenset({"raw_tushare_ths_member"})
BLOCKED_NAMESPACES = frozenset({"tdx_block", "block", "tdx_industry"})
SAMPLE_LIMIT = 20
MIAOXIANG_DC_UNIVERSE = {
    "status": "blocked_no_universe_dump",
    "reason": (
        "miaoxiang themes/peer APIs are per-stock, not a market-wide DC "
        "membership dump; they are not a second-source DC universe"
    ),
}

_TABLE_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _table_leaf(table: str) -> str:
    return str(table).split(".")[-1].strip('"')


def sql_table(table: str) -> str:
    parts = [p.strip('"') for p in str(table).split(".") if p.strip('"')]
    if not parts or any(not _TABLE_PART.fullmatch(p) for p in parts):
        raise ValueError(f"bad table identifier: {table!r}")
    return ".".join(f'"{p}"' for p in parts)


def reject_banned_baseline(table: str, *, banned: frozenset[str], accepted: str) -> str:
    name = _table_leaf(table)
    if name in banned:
        raise ValueError(
            f"banned baseline {table!r}; use {accepted} "
            f"(legacy residual is not recon truth)"
        )
    return table


def reject_tdx_block(namespace: str) -> str:
    ns = str(namespace or "").strip()
    if ns in BLOCKED_NAMESPACES:
        raise ValueError(f"{ns!r} is not one of the four taxonomy chains")
    return ns


def reject_ths_interval_row(row: Mapping[str, Any] | None = None) -> None:
    payload = row or {}
    if payload.get("in_date") not in (None, "", "暂无") or payload.get("out_date") not in (
        None,
        "",
        "暂无",
    ):
        raise ValueError(
            "ths in_date/out_date unproven; observation snapshot only "
            "(pit_interval=forbidden)"
        )


def compact_yyyymmdd(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def normalize_member_code(code: Any) -> str | None:
    raw = str(code or "").strip().upper()
    if not raw:
        return None
    if "." in raw:
        ticker, exch = raw.split(".", 1)
        ticker = ticker.zfill(6) if ticker.isdigit() else ticker
        exch = exch.strip()
        if not ticker or not exch:
            return None
        return f"{ticker}.{exch}"
    if raw.isdigit() and len(raw) <= 6:
        return raw.zfill(6)
    return raw


def member_set_diff(
    left: Iterable[Any],
    right: Iterable[Any],
    *,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    left_s = {c for c in (normalize_member_code(x) for x in left) if c}
    right_s = {c for c in (normalize_member_code(x) for x in right) if c}
    only_left = sorted(left_s - right_s)
    only_right = sorted(right_s - left_s)
    both = left_s & right_s
    union = left_s | right_s
    if not left_s and not right_s:
        status = "empty_recon"
        jaccard = None
    else:
        status = "compared"
        jaccard = (len(both) / len(union)) if union else None
    return {
        "status": status,
        "left_n": len(left_s),
        "right_n": len(right_s),
        "intersection": len(both),
        "only_left": len(only_left),
        "only_right": len(only_right),
        "only_left_sample": only_left[:sample_limit],
        "only_right_sample": only_right[:sample_limit],
        "jaccard": jaccard,
        "identity": False,
    }


def name_collision_relation(name: str) -> dict[str, Any]:
    return {
        "name": str(name),
        "identity": False,
        "relation": "name_collision_candidate",
        "note": "equal names are candidates, not equivalent/broader/narrower",
    }


def compare_named_memberships(
    left: Mapping[str, Iterable[Any]],
    right: Mapping[str, Iterable[Any]],
    *,
    left_ns: str,
    right_ns: str,
) -> dict[str, Any]:
    if left_ns == right_ns:
        raise ValueError("cross-namespace compare requires two distinct namespaces")
    if left_ns not in FOUR_CHAIN_NAMESPACES or right_ns not in FOUR_CHAIN_NAMESPACES:
        raise ValueError(f"namespaces must be four-chain: {left_ns!r} vs {right_ns!r}")
    names = sorted(set(left) & set(right))
    per_name: list[dict[str, Any]] = []
    same_sets = 0
    divergent = 0
    empty = 0
    for name in names:
        diff = member_set_diff(left[name], right[name])
        diff.update(name_collision_relation(name))
        if diff["status"] == "empty_recon":
            empty += 1
        elif diff["only_left"] == 0 and diff["only_right"] == 0:
            same_sets += 1
        else:
            divergent += 1
        per_name.append(diff)
    return {
        "left_ns": left_ns,
        "right_ns": right_ns,
        "colliding_names": len(names),
        "identical_member_sets": same_sets,
        "divergent_member_sets": divergent,
        "empty_recon_names": empty,
        "per_name": per_name,
        "note": (
            "name equality is a candidate only; jaccard=1 does not merge namespaces"
        ),
    }


def dc_l1_level_label(*, path: str | None = None) -> str:
    mapping = source_level_map("dc_industry", path=path)
    inv = {level: label for label, level in mapping.items()}
    label = inv.get("L1")
    if not label:
        raise ValueError("dc_industry source_level_map has no L1")
    return label


def load_dc_industry_l1_memberships(
    con: Any,
    trade_date: Any,
    *,
    member_table: str = DC_MEMBER_PUBLICATION,
    catalog_table: str = DC_INDEX_CATALOG,
) -> dict[str, set[str]]:
    reject_banned_baseline(
        member_table, banned=BANNED_DC_MEMBERSHIP, accepted=DC_MEMBER_PUBLICATION
    )
    day = compact_yyyymmdd(trade_date)
    if day is None:
        raise ValueError("trade_date required")
    idx_type = source_index_type("dc_industry")
    level = dc_l1_level_label()
    rows = con.execute(
        f"""
        SELECT i.name, m.con_code
        FROM {sql_table(member_table)} m
        JOIN {sql_table(catalog_table)} i
          ON i.ts_code = m.ts_code
         AND CAST(i.trade_date AS VARCHAR) = CAST(m.trade_date AS VARCHAR)
        WHERE CAST(m.trade_date AS VARCHAR) = ?
          AND i.idx_type = ?
          AND i.level = ?
        """,
        [day, idx_type, level],
    ).fetchall()
    out: dict[str, set[str]] = {}
    for name, code in rows:
        key = str(name or "").strip()
        member = normalize_member_code(code)
        if not key or member is None:
            continue
        out.setdefault(key, set()).add(member)
    return out


def load_dc_concept_memberships(
    con: Any,
    trade_date: Any,
    *,
    member_table: str = DC_MEMBER_PUBLICATION,
    catalog_table: str = DC_INDEX_CATALOG,
) -> dict[str, set[str]]:
    reject_banned_baseline(
        member_table, banned=BANNED_DC_MEMBERSHIP, accepted=DC_MEMBER_PUBLICATION
    )
    day = compact_yyyymmdd(trade_date)
    if day is None:
        raise ValueError("trade_date required")
    idx_type = source_index_type("dc_concept")
    rows = con.execute(
        f"""
        SELECT i.name, m.con_code
        FROM {sql_table(member_table)} m
        JOIN {sql_table(catalog_table)} i
          ON i.ts_code = m.ts_code
         AND CAST(i.trade_date AS VARCHAR) = CAST(m.trade_date AS VARCHAR)
        WHERE CAST(m.trade_date AS VARCHAR) = ?
          AND i.idx_type = ?
        """,
        [day, idx_type],
    ).fetchall()
    out: dict[str, set[str]] = {}
    for name, code in rows:
        key = str(name or "").strip()
        member = normalize_member_code(code)
        if not key or member is None:
            continue
        out.setdefault(key, set()).add(member)
    return out


def load_sw_l1_memberships(
    con: Any,
    trade_date: Any,
    *,
    table: str = SW_PIT_PUBLICATION,
) -> dict[str, set[str]]:
    reject_banned_baseline(table, banned=BANNED_SW_MEMBERSHIP, accepted=SW_PIT_PUBLICATION)
    day = compact_yyyymmdd(trade_date)
    if day is None:
        raise ValueError("trade_date required")
    rows = con.execute(
        f"""
        SELECT l1_name, ts_code
        FROM {sql_table(table)}
        WHERE in_date <= ?
          AND (out_date IS NULL OR out_date > ?)
        """,
        [day, day],
    ).fetchall()
    out: dict[str, set[str]] = {}
    for name, code in rows:
        key = str(name or "").strip()
        member = normalize_member_code(code)
        if not key or member is None:
            continue
        out.setdefault(key, set()).add(member)
    return out


def publication_vs_landing_pairs(
    pub: Iterable[tuple[Any, Any]],
    landing: Iterable[tuple[Any, Any]],
) -> dict[str, Any]:
    """Same-day grain integrity. Landing is residual, not the DC ruler."""
    def _pairs(rows: Iterable[tuple[Any, Any]]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for board, con in rows:
            b = normalize_member_code(board)
            c = normalize_member_code(con)
            if b and c:
                out.add((b, c))
        return out

    left = _pairs(pub)
    right = _pairs(landing)
    only_pub = sorted(left - right)
    only_land = sorted(right - left)
    both = left & right
    if not left and not right:
        status = "empty_recon"
    else:
        status = "compared"
    return {
        "status": status,
        "publication_n": len(left),
        "landing_n": len(right),
        "intersection": len(both),
        "only_publication": len(only_pub),
        "only_landing": len(only_land),
        "only_publication_sample": [f"{a}|{b}" for a, b in only_pub[:SAMPLE_LIMIT]],
        "only_landing_sample": [f"{a}|{b}" for a, b in only_land[:SAMPLE_LIMIT]],
        "note": "landing is rebuild residual; publication is the membership ruler",
    }


def fuyao_catalog_rows(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        thscode = str(item.get("thscode") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if not thscode or not name:
            continue
        rows.append({"thscode": thscode, "name": name})
    return rows


def fuyao_constituent_codes(items: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in items:
        reject_ths_interval_row(item)
        code = normalize_member_code(item.get("thscode"))
        if code is None:
            ticker = str(item.get("ticker") or "").strip()
            exch = str(item.get("exchange") or "").strip()
            if ticker and exch:
                code = normalize_member_code(f"{ticker}.{exch}")
        if code:
            out.append(code)
    return out


def select_ths_sample(
    catalog: Sequence[Mapping[str, str]],
    candidate_names: Iterable[str],
    *,
    limit: int = 5,
) -> list[dict[str, str]]:
    names = {str(n).strip() for n in candidate_names if str(n).strip()}
    exact = [dict(row) for row in catalog if row.get("name") in names]
    exact.sort(key=lambda r: (r.get("name") or "", r.get("thscode") or ""))
    if exact:
        return exact[:limit]
    fallback = [dict(row) for row in catalog[:limit]]
    for row in fallback:
        row["sample_reason"] = "no_name_collision_fallback"
    return fallback


def miaoxiang_dc_universe_status() -> dict[str, str]:
    return dict(MIAOXIANG_DC_UNIVERSE)


__all__ = [
    "BANNED_DC_MEMBERSHIP",
    "BANNED_SW_MEMBERSHIP",
    "BANNED_THS_INTERVAL",
    "BLOCKED_NAMESPACES",
    "DC_INDEX_CATALOG",
    "DC_MEMBER_PUBLICATION",
    "SW_PIT_PUBLICATION",
    "compare_named_memberships",
    "compact_yyyymmdd",
    "dc_l1_level_label",
    "fuyao_catalog_rows",
    "fuyao_constituent_codes",
    "load_dc_concept_memberships",
    "load_dc_industry_l1_memberships",
    "load_sw_l1_memberships",
    "member_set_diff",
    "miaoxiang_dc_universe_status",
    "name_collision_relation",
    "normalize_member_code",
    "publication_vs_landing_pairs",
    "reject_banned_baseline",
    "reject_tdx_block",
    "reject_ths_interval_row",
    "select_ths_sample",
    "sql_table",
]
