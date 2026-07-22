"""CX-2 read-only state sensors for delta_manifest.state_changes.

Detects ST 戴帽/摘帽, holders ratio/rank/exit changes, and delist/active-universe
removals. Sensors are **observers only** — they never write Tier0
landing/canonical/accepted_partition truth.

PIT posture:
- ST: diff consecutive *accepted* stock_st partitions only.
- holders: latest *accepted* notice_date partition vs prior same-grain rows;
  ratio (even if rank unchanged), rank remap, and ``is_exit_row`` exits.
  Do **not** ST-style set-diff consecutive notice partitions (different filers).
- delist: dim_active_a_stock set diff (acquire before/after refresh and/or
  persisted as-of fingerprint). Identity publication refresh remains the
  writer; this module only reports the set delta.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DIM_ACTIVE_AS_OF_PATH = REPO / "data/reports/dim_active_codes_as_of.json"
SAMPLE_CAP = 20

STOCK_ST_DATASET = "tier0.security_identity.stock_st_daily"
STOCK_ST_TABLE = "canonical_stock_st_daily"
HOLDERS_DATASET = "tier0.disclosure.top10_float_holders_period"
HOLDERS_TABLE = "canonical_top10_float_holders_period"
HOLDERS_DETECTION = "canonical_holders_notice_delta"


def _yyyymmdd(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if not text:
        return ""
    return text.replace("-", "")[:8]


def _capped(items: list[Any], *, cap: int = SAMPLE_CAP) -> list[Any]:
    return list(items[:cap])


def empty_state_changes() -> dict[str, Any]:
    return {
        "stock_st": {
            "status": "not_evaluated",
            "changed": False,
            "detection": "accepted_partition_membership_diff",
            "tier0_write": False,
        },
        "holders": {
            "status": "not_evaluated",
            "changed": False,
            "detection": HOLDERS_DETECTION,
            "tier0_write": False,
        },
        "delist": {
            "status": "not_evaluated",
            "changed": False,
            "detection": "dim_active_code_set_diff",
            "tier0_write": False,
        },
    }


def any_state_changed(state_changes: dict[str, Any] | None) -> bool:
    if not state_changes:
        return False
    for key in ("stock_st", "holders", "delist"):
        block = state_changes.get(key) or {}
        if bool(block.get("changed")):
            return True
    return False


def state_change_force_reasons(state_changes: dict[str, Any] | None) -> list[str]:
    """Typed reasons that must block unsafe process skips (CX-2 kill criterion)."""
    out: list[str] = []
    if not state_changes:
        return out
    for key in ("stock_st", "holders", "delist"):
        block = state_changes.get(key) or {}
        if bool(block.get("changed")):
            out.append(f"state_change:{key}")
    return out


def membership_diff(
    prev_rows: list[tuple[str, str, str, str]],
    curr_rows: list[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """Diff ST membership rows as (ts_code, name, type, type_name)."""
    prev = {r[0]: r for r in prev_rows}
    curr = {r[0]: r for r in curr_rows}
    entered = sorted(set(curr) - set(prev))
    exited = sorted(set(prev) - set(curr))
    attr_changed: list[str] = []
    for code in sorted(set(prev) & set(curr)):
        if prev[code][1:] != curr[code][1:]:
            attr_changed.append(code)
    changed = bool(entered or exited or attr_changed)
    return {
        "status": "ok",
        "changed": changed,
        "prev_n": len(prev),
        "curr_n": len(curr),
        "entered_n": len(entered),
        "exited_n": len(exited),
        "attr_changed_n": len(attr_changed),
        "entered_sample": _capped(
            [
                {
                    "ts_code": code,
                    "name": curr[code][1],
                    "type": curr[code][2],
                    "type_name": curr[code][3],
                    "kind": "enter",
                }
                for code in entered
            ]
        ),
        "exited_sample": _capped(
            [
                {
                    "ts_code": code,
                    "name": prev[code][1],
                    "type": prev[code][2],
                    "type_name": prev[code][3],
                    "kind": "exit",
                }
                for code in exited
            ]
        ),
        "attr_changed_sample": _capped(attr_changed),
        "detection": "accepted_partition_membership_diff",
        "tier0_write": False,
    }


def detect_stock_st_state_changes(
    conn,
    *,
    dataset_id: str = STOCK_ST_DATASET,
    table: str = STOCK_ST_TABLE,
) -> dict[str, Any]:
    """Compare the two latest accepted stock_st partitions (read-only)."""
    try:
        partitions = [
            _yyyymmdd(r[0])
            for r in conn.execute(
                """
                SELECT partition_value
                FROM accepted_partition
                WHERE dataset_id = ?
                ORDER BY partition_value DESC
                LIMIT 2
                """,
                [dataset_id],
            ).fetchall()
            if _yyyymmdd(r[0])
        ]
    except Exception as exc:
        return {
            "status": "unavailable",
            "changed": False,
            "reason": f"accepted_partition_read_failed:{type(exc).__name__}",
            "detection": "accepted_partition_membership_diff",
            "tier0_write": False,
        }
    if len(partitions) < 2:
        return {
            "status": "skipped_no_pair",
            "changed": False,
            "reason": "need_two_accepted_stock_st_partitions",
            "as_of": partitions[0] if partitions else None,
            "detection": "accepted_partition_membership_diff",
            "tier0_write": False,
        }
    curr_p, prev_p = partitions[0], partitions[1]

    def _load(partition: str) -> list[tuple[str, str, str, str]]:
        day = date(int(partition[:4]), int(partition[4:6]), int(partition[6:8]))
        rows = conn.execute(
            f"""
            SELECT ts_code, name, type, type_name
            FROM {table}
            WHERE trade_date = ?
            """,
            [day],
        ).fetchall()
        return [
            (str(r[0]), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""))
            for r in rows
        ]

    try:
        prev_rows = _load(prev_p)
        curr_rows = _load(curr_p)
    except Exception as exc:
        return {
            "status": "unavailable",
            "changed": False,
            "reason": f"canonical_stock_st_read_failed:{type(exc).__name__}",
            "as_of": curr_p,
            "baseline": prev_p,
            "detection": "accepted_partition_membership_diff",
            "tier0_write": False,
        }
    out = membership_diff(prev_rows, curr_rows)
    out["as_of"] = curr_p
    out["baseline"] = prev_p
    return out


def _ratio_unequal(prev_ratio: Any, curr_ratio: Any) -> bool:
    if prev_ratio is None and curr_ratio is None:
        return False
    if prev_ratio is None or curr_ratio is None:
        return True
    return float(prev_ratio) != float(curr_ratio)


def holders_notice_diff(
    *,
    curr_active: list[tuple[Any, ...]],
    prev_active: list[tuple[Any, ...]],
    exit_rows: list[tuple[Any, ...]],
) -> dict[str, Any]:
    """Ratio (same grain), rank remap (name identity), and exit rows.

    Active row tuple: (stock_code, holder_set, holder_rank, row_seq,
    hold_ratio_float, holder_name)
    Exit row tuple: (stock_code, holder_set, holder_rank, hold_ratio_float,
    holder_name)
    """
    prev_by_grain = {
        (str(r[0]), str(r[1]), int(r[2]), int(r[3])): r for r in prev_active
    }
    curr_by_grain = {
        (str(r[0]), str(r[1]), int(r[2]), int(r[3])): r for r in curr_active
    }
    prev_by_name = {
        (str(r[0]), str(r[1]), str(r[5])): r for r in prev_active
    }
    curr_by_name = {
        (str(r[0]), str(r[1]), str(r[5])): r for r in curr_active
    }

    ratio_keys: list[tuple[str, str, int, int]] = []
    for key, row in curr_by_grain.items():
        if key not in prev_by_grain:
            continue
        if _ratio_unequal(prev_by_grain[key][4], row[4]):
            ratio_keys.append(key)

    rank_keys: list[tuple[str, str, str]] = []
    for key, row in curr_by_name.items():
        if key not in prev_by_name:
            continue
        if int(prev_by_name[key][2]) != int(row[2]):
            rank_keys.append(key)

    ratio_sample = [
        {
            "stock_code": key[0],
            "holder_set": key[1],
            "holder_rank": key[2],
            "row_seq": key[3],
            "prev_ratio": prev_by_grain[key][4],
            "curr_ratio": curr_by_grain[key][4],
            "holder_name": curr_by_grain[key][5],
        }
        for key in ratio_keys[:SAMPLE_CAP]
    ]
    rank_sample = [
        {
            "stock_code": key[0],
            "holder_set": key[1],
            "holder_name": key[2],
            "prev_rank": int(prev_by_name[key][2]),
            "curr_rank": int(curr_by_name[key][2]),
        }
        for key in rank_keys[:SAMPLE_CAP]
    ]
    exit_sample = [
        {
            "stock_code": str(r[0]),
            "holder_set": str(r[1]),
            "holder_rank": int(r[2]),
            "hold_ratio_float": r[3],
            "holder_name": str(r[4]),
        }
        for r in exit_rows[:SAMPLE_CAP]
    ]
    ratio_n = len(ratio_keys)
    rank_n = len(rank_keys)
    exit_n = len(exit_rows)
    return {
        "status": "ok",
        "changed": bool(ratio_n or rank_n or exit_n),
        "ratio_changed_n": ratio_n,
        "rank_changed_n": rank_n,
        "exit_n": exit_n,
        "curr_n": len(curr_by_grain),
        "compared_n": sum(1 for k in curr_by_grain if k in prev_by_grain),
        "sample": {
            "ratio": ratio_sample,
            "rank": rank_sample,
            "exit": exit_sample,
        },
        "detection": HOLDERS_DETECTION,
        "tier0_write": False,
    }


# Back-compat alias for older unit helpers / imports.
def holders_ratio_diff(
    curr_rows: list[tuple[Any, ...]],
    prev_rows: list[tuple[Any, ...]],
) -> dict[str, Any]:
    out = holders_notice_diff(
        curr_active=curr_rows,
        prev_active=prev_rows,
        exit_rows=[],
    )
    # Preserve flat sample shape expected by early CX-2 tests.
    out["sample"] = list(out["sample"]["ratio"])
    return out


def _holders_as_of_partition(
    conn,
    *,
    dataset_id: str = HOLDERS_DATASET,
    table: str = HOLDERS_TABLE,
) -> tuple[str | None, str | None]:
    """Return (as_of, miss_reason). Accepted partition only — no MAX fallback."""
    try:
        row = conn.execute(
            """
            SELECT partition_value
            FROM accepted_partition
            WHERE dataset_id = ?
            ORDER BY partition_value DESC
            LIMIT 1
            """,
            [dataset_id],
        ).fetchone()
        if row and _yyyymmdd(row[0]):
            return _yyyymmdd(row[0]), None
        return None, "skipped_no_accepted"
    except Exception as exc:  # rule-compliance: ok evidence=fail-closed sensor; never invent as_of via MAX(notice_date)
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        except Exception as table_exc:
            return None, f"holders_read_failed:{type(table_exc).__name__}"
        return None, f"accepted_partition_read_failed:{type(exc).__name__}"


def detect_holders_state_changes(
    conn,
    *,
    table: str = HOLDERS_TABLE,
    dataset_id: str = HOLDERS_DATASET,
) -> dict[str, Any]:
    """Accepted notice as_of: ratio + rank + exit (read-only)."""
    latest_s, miss = _holders_as_of_partition(conn, dataset_id=dataset_id, table=table)
    if latest_s is None:
        if miss and (
            miss.startswith("holders_read_failed")
            or miss.startswith("accepted_partition_read_failed")
        ):
            return {
                "status": "unavailable",
                "changed": False,
                "reason": miss,
                "detection": HOLDERS_DETECTION,
                "tier0_write": False,
            }
        return {
            "status": "skipped_no_accepted",
            "changed": False,
            "reason": miss or "no_accepted_holders_partition",
            "detection": HOLDERS_DETECTION,
            "tier0_write": False,
        }
    try:
        curr_active = conn.execute(
            f"""
            SELECT stock_code, holder_set, holder_rank, row_seq,
                   hold_ratio_float, holder_name
            FROM {table}
            WHERE notice_date = ? AND COALESCE(is_exit_row, FALSE) = FALSE
            """,
            [latest_s],
        ).fetchall()
        exit_rows = conn.execute(
            f"""
            SELECT stock_code, holder_set, holder_rank,
                   hold_ratio_float, holder_name
            FROM {table}
            WHERE notice_date = ? AND COALESCE(is_exit_row, FALSE) = TRUE
            """,
            [latest_s],
        ).fetchall()
        prev_active = conn.execute(
            f"""
            WITH prev_dates AS (
              SELECT stock_code, holder_set, holder_rank, row_seq,
                     MAX(notice_date) AS prev_notice
              FROM {table}
              WHERE notice_date < ? AND COALESCE(is_exit_row, FALSE) = FALSE
              GROUP BY 1, 2, 3, 4
            )
            SELECT h.stock_code, h.holder_set, h.holder_rank, h.row_seq,
                   h.hold_ratio_float, h.holder_name
            FROM prev_dates p
            JOIN {table} h
              ON h.stock_code = p.stock_code
             AND h.holder_set = p.holder_set
             AND h.holder_rank = p.holder_rank
             AND h.row_seq = p.row_seq
             AND h.notice_date = p.prev_notice
             AND COALESCE(h.is_exit_row, FALSE) = FALSE
            """,
            [latest_s],
        ).fetchall()
    except Exception as exc:
        return {
            "status": "unavailable",
            "changed": False,
            "reason": f"holders_diff_query_failed:{type(exc).__name__}",
            "as_of": latest_s,
            "detection": HOLDERS_DETECTION,
            "tier0_write": False,
        }
    out = holders_notice_diff(
        curr_active=list(curr_active),
        prev_active=list(prev_active),
        exit_rows=list(exit_rows),
    )
    out["as_of"] = latest_s
    return out


def detect_holders_ratio_state_changes(conn, *, table: str = HOLDERS_TABLE) -> dict[str, Any]:
    """Back-compat wrapper → :func:`detect_holders_state_changes`."""
    return detect_holders_state_changes(conn, table=table)


def code_set_fingerprint(codes: set[str] | list[str]) -> str:
    payload = "\n".join(sorted(str(c).zfill(6) for c in codes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def delist_diff(
    prev_codes: set[str],
    curr_codes: set[str],
) -> dict[str, Any]:
    removed = sorted(set(prev_codes) - set(curr_codes))
    added = sorted(set(curr_codes) - set(prev_codes))
    return {
        "status": "ok",
        "changed": bool(removed or added),
        "removed_n": len(removed),
        "added_n": len(added),
        "prev_n": len(prev_codes),
        "curr_n": len(curr_codes),
        "removed_sample": _capped(removed),
        "added_sample": _capped(added),
        "detection": "dim_active_code_set_diff",
        "tier0_write": False,
    }


def read_dim_active_as_of(path: Path | None = None) -> dict[str, Any] | None:
    marker = path or DIM_ACTIVE_AS_OF_PATH
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_dim_active_as_of(
    codes: set[str] | list[str],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    marker = path or DIM_ACTIVE_AS_OF_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    code_list = sorted(str(c).zfill(6) for c in codes)
    payload = {
        "code_count": len(code_list),
        "code_set_sha256": code_set_fingerprint(code_list),
        "codes": code_list,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    marker.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def load_dim_active_codes(conn) -> set[str]:
    rows = conn.execute("SELECT stock_code FROM dim_active_a_stock").fetchall()
    return {str(r[0]).zfill(6) for r in rows if r[0]}


def detect_delist_state_changes(
    *,
    before_codes: set[str] | None = None,
    after_codes: set[str] | None = None,
    as_of_path: Path | None = None,
    persist_after: bool = True,
) -> dict[str, Any]:
    """Diff dim_active code sets. Prefer in-run before/after; else as-of file."""
    if after_codes is None:
        return {
            "status": "unavailable",
            "changed": False,
            "reason": "after_codes_missing",
            "detection": "dim_active_code_set_diff",
            "tier0_write": False,
        }
    prev = set(before_codes) if before_codes is not None else None
    if prev is None:
        prior = read_dim_active_as_of(as_of_path)
        if prior and isinstance(prior.get("codes"), list):
            prev = {str(c).zfill(6) for c in prior["codes"]}
        else:
            if persist_after:
                write_dim_active_as_of(after_codes, path=as_of_path)
            return {
                "status": "skipped_no_baseline",
                "changed": False,
                "reason": "dim_active_as_of_missing_first_publish",
                "curr_n": len(after_codes),
                "detection": "dim_active_code_set_diff",
                "tier0_write": False,
            }
    out = delist_diff(prev, set(after_codes))
    if persist_after:
        write_dim_active_as_of(after_codes, path=as_of_path)
    return out


def collect_state_changes(
    *,
    stock_st_conn=None,
    holders_conn=None,
    delist_before: set[str] | None = None,
    delist_after: set[str] | None = None,
    dim_as_of_path: Path | None = None,
    persist_dim_as_of: bool = True,
) -> dict[str, Any]:
    """Assemble typed state_changes block for delta_manifest."""
    out = empty_state_changes()
    if stock_st_conn is not None:
        out["stock_st"] = detect_stock_st_state_changes(stock_st_conn)
    if holders_conn is not None:
        out["holders"] = detect_holders_state_changes(holders_conn)
    if delist_after is not None or delist_before is not None:
        out["delist"] = detect_delist_state_changes(
            before_codes=delist_before,
            after_codes=delist_after,
            as_of_path=dim_as_of_path,
            persist_after=persist_dim_as_of,
        )
    out["any_changed"] = any_state_changed(out)
    out["force_reasons"] = state_change_force_reasons(out)
    return out


__all__ = [
    "DIM_ACTIVE_AS_OF_PATH",
    "HOLDERS_DETECTION",
    "any_state_changed",
    "code_set_fingerprint",
    "collect_state_changes",
    "delist_diff",
    "detect_delist_state_changes",
    "detect_holders_ratio_state_changes",
    "detect_holders_state_changes",
    "detect_stock_st_state_changes",
    "empty_state_changes",
    "holders_notice_diff",
    "holders_ratio_diff",
    "load_dim_active_codes",
    "membership_diff",
    "read_dim_active_as_of",
    "state_change_force_reasons",
    "write_dim_active_as_of",
]
