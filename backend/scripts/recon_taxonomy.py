#!/usr/bin/env python3
"""Read-only four-chain taxonomy recon vs DC publication + SW PIT + Fuyao THS.

Does not change primaries. THS constituents are current observation only.
Miaoxiang has no market-wide DC membership dump.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.sources.fuyao import (  # noqa: E402
    FuyaoRestError,
    resolve_api_key,
    rest_json,
)
from services.data_sources.taxonomy_recon import (  # noqa: E402
    DC_MEMBER_PUBLICATION,
    SW_PIT_PUBLICATION,
    compare_named_memberships,
    compact_yyyymmdd,
    fuyao_catalog_rows,
    fuyao_constituent_codes,
    load_dc_concept_memberships,
    load_dc_industry_l1_memberships,
    load_sw_l1_memberships,
    member_set_diff,
    miaoxiang_dc_universe_status,
    publication_vs_landing_pairs,
    select_ths_sample,
    sql_table,
)
from services.duck_adapter import connect  # noqa: E402
from services.taxonomy_config import FOUR_CHAIN_NAMESPACES, load_taxonomy_config  # noqa: E402


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _pairs(con: Any, table: str, day: str) -> list[tuple[str, str]]:
    rows = con.execute(
        f"""
        SELECT ts_code, con_code
        FROM {sql_table(table)}
        WHERE CAST(trade_date AS VARCHAR) = ?
        """,
        [day],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _fetch_ths_tag(api_key: str, tag: str, *, timeout: float) -> dict[str, Any]:
    data = rest_json(
        "/api/a-share-index/catalog/ths-index-list",
        api_key=api_key,
        params={"tag": tag},
        timeout=timeout,
    )
    rows = fuyao_catalog_rows(list((data or {}).get("item") or []))
    return {"tag": tag, "n": len(rows), "rows": rows}


def _fetch_constituents(api_key: str, thscode: str, *, timeout: float) -> list[str]:
    data = rest_json(
        "/api/a-share-index/constituents/ths-stock-list",
        api_key=api_key,
        params={"thscode": thscode},
        timeout=timeout,
    )
    return fuyao_constituent_codes(list((data or {}).get("item") or []))


def _ths_vs_named_maps(
    sample: list[dict[str, str]],
    named_maps: dict[str, dict[str, set[str]]],
    *,
    api_key: str,
    timeout: float,
    ths_ns: str,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {ns: [] for ns in named_maps}
    for row in sample:
        name = row["name"]
        try:
            codes = _fetch_constituents(api_key, row["thscode"], timeout=timeout)
        except FuyaoRestError as exc:
            err = {
                "name": name,
                "thscode": row["thscode"],
                "status": "error",
                "http": exc.http,
                "code": exc.code,
                "message": str(exc)[:240],
            }
            for ns in named_maps:
                out[ns].append(dict(err))
            continue
        for other_ns, named in named_maps.items():
            diff = member_set_diff(codes, named.get(name, set()))
            diff.update(
                {
                    "name": name,
                    "thscode": row["thscode"],
                    "left_ns": ths_ns,
                    "right_ns": other_ns,
                    "sample_reason": row.get("sample_reason", "name_collision"),
                    "pit_model": "observation_snapshot",
                }
            )
            out[other_ns].append(diff)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "taxonomy_recon.json",
    )
    parser.add_argument("--skip-fuyao", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--ths-boards", type=int, default=5)
    args = parser.parse_args(argv)

    cfg = load_taxonomy_config()
    sm_path = db_path("smartmoney")
    raw_path = db_path("tushare_raw")
    con = connect(str(sm_path), read_only=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "primary_cut": False,
        "namespaces": sorted(FOUR_CHAIN_NAMESPACES),
        "cross_namespace_fallback": cfg.get("cross_namespace_fallback"),
        "dc_second_source": miaoxiang_dc_universe_status(),
        "ths_pit_interval": "forbidden",
    }
    asof = None
    dc_l1: dict[str, set[str]] = {}
    sw_l1: dict[str, set[str]] = {}
    dc_concepts: dict[str, set[str]] = {}
    try:
        raw_esc = str(raw_path).replace("'", "''")
        con.execute(f"ATTACH '{raw_esc}' AS tr (READ_ONLY)")
        asof_row = con.execute(
            f'SELECT max(CAST(trade_date AS VARCHAR)) FROM "{DC_MEMBER_PUBLICATION}"'
        ).fetchone()
        asof = compact_yyyymmdd(asof_row[0] if asof_row else None)
        report["asof"] = asof
        if asof is None:
            report["dc_sw"] = {"status": "empty_recon", "reason": "no fact_dc_member_daily asof"}
        else:
            pub_pairs = _pairs(con, DC_MEMBER_PUBLICATION, asof)
            land_pairs = _pairs(con, "tr.raw_tushare_dc_member", asof)
            report["dc_publication_vs_landing"] = publication_vs_landing_pairs(pub_pairs, land_pairs)

            dc_l1 = load_dc_industry_l1_memberships(
                con, asof, catalog_table="tr.raw_tushare_dc_index"
            )
            sw_l1 = load_sw_l1_memberships(con, asof, table=f"tr.{SW_PIT_PUBLICATION}")
            report["dc_sw_l1"] = compare_named_memberships(
                dc_l1, sw_l1, left_ns="dc_industry", right_ns="sw_industry"
            )
            dc_concepts = load_dc_concept_memberships(
                con, asof, catalog_table="tr.raw_tushare_dc_index"
            )
            report["dc_l1_boards"] = len(dc_l1)
            report["sw_l1_boards"] = len(sw_l1)
            report["dc_concept_boards"] = len(dc_concepts)
    finally:
        con.close()

    if asof is None:
        _write(args.out, report)
        print("dc_sw_l1 empty_recon")
        return 0

    if not args.skip_fuyao:
        api_key = resolve_api_key()
        if not api_key:
            report["fuyao"] = {"status": "auth_missing"}
        else:
            fuyao: dict[str, Any] = {"pit_model": "observation_snapshot"}
            try:
                industry = _fetch_ths_tag(api_key, "industry", timeout=args.timeout)
                concept = _fetch_ths_tag(api_key, "cn_concept", timeout=args.timeout)
                fuyao["industry_catalog_n"] = industry["n"]
                fuyao["concept_catalog_n"] = concept["n"]
                fuyao["industry_name_collisions_dc"] = sum(
                    1 for r in industry["rows"] if r["name"] in dc_l1
                )
                fuyao["industry_name_collisions_sw"] = sum(
                    1 for r in industry["rows"] if r["name"] in sw_l1
                )
                fuyao["concept_name_collisions_dc"] = sum(
                    1 for r in concept["rows"] if r["name"] in dc_concepts
                )
                industry_sample = select_ths_sample(
                    industry["rows"], dc_l1.keys(), limit=args.ths_boards
                )
                concept_sample = select_ths_sample(
                    concept["rows"], dc_concepts.keys(), limit=args.ths_boards
                )
                industry_maps = _ths_vs_named_maps(
                    industry_sample,
                    {"dc_industry": dc_l1, "sw_industry": sw_l1},
                    api_key=api_key,
                    timeout=args.timeout,
                    ths_ns="ths_industry",
                )
                concept_maps = _ths_vs_named_maps(
                    concept_sample,
                    {"dc_concept": dc_concepts},
                    api_key=api_key,
                    timeout=args.timeout,
                    ths_ns="ths_concept",
                )
                fuyao["industry_vs_dc"] = industry_maps["dc_industry"]
                fuyao["industry_vs_sw"] = industry_maps["sw_industry"]
                fuyao["concept_vs_dc"] = concept_maps["dc_concept"]
            except FuyaoRestError as exc:
                fuyao["status"] = "error"
                fuyao["http"] = exc.http
                fuyao["code"] = exc.code
                fuyao["message"] = str(exc)[:240]
            report["fuyao"] = fuyao

    _write(args.out, report)
    l1 = report.get("dc_sw_l1") or {}
    print(
        "dc_sw_l1 "
        f"colliding={l1.get('colliding_names')} "
        f"divergent={l1.get('divergent_member_sets')} "
        f"identical_sets={l1.get('identical_member_sets')} "
        f"asof={report.get('asof')}"
    )
    print(f"primary_cut={report['primary_cut']} miaoxiang={report['dc_second_source']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
