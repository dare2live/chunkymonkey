"""Service-level update task adapters used by API routers."""
from __future__ import annotations

from typing import Any


def ingest_holders_tdxhub_raw_parse(*, workers: int, con: Any) -> dict[str, Any]:
    """Run the tdxhub holder raw fetch and canonical replay task."""
    from scripts.ingest_holders_tdxhub import run

    return run(workers=workers, con=con)


def profile_tdx_gpcw_fields_task(conn: Any) -> dict[str, Any]:
    """Profile TDX GPCW fields through the script-owned pipeline function."""
    from scripts.profile_tdx_gpcw_fields import profile_tdx_gpcw_fields

    return profile_tdx_gpcw_fields(conn)


def build_tdx_gpcw_auto_features_task(
    conn: Any,
    *,
    profile_run_id: str,
    report_dates: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Build automatic GPCW features through the script-owned pipeline function."""
    from scripts.build_tdx_gpcw_auto_features import build_tdx_gpcw_auto_features

    return build_tdx_gpcw_auto_features(
        conn,
        profile_run_id=profile_run_id,
        report_dates=report_dates,
    )


def sync_gpcw_files_and_auto_features(conn: Any, *, quarters: int = 12) -> dict[str, Any]:
    """Sync GPCW files and rebuild derived profile/auto features when needed."""
    from services.tdx_affair_client import sync_gpcw_files

    result = sync_gpcw_files(conn, quarters=quarters)
    affected_dates = list(result.get("affected_report_dates") or [])
    if affected_dates:
        profile = profile_tdx_gpcw_fields_task(conn)
        auto_features = build_tdx_gpcw_auto_features_task(
            conn,
            profile_run_id=profile["profile_run_id"],
            report_dates=affected_dates,
        )
        result["field_profile"] = {
            "profile_run_id": profile["profile_run_id"],
            "field_count": profile["field_count"],
            "model_candidate_count": profile["model_candidate_count"],
        }
        result["auto_feature_rebuild"] = {
            "report_dates": auto_features["rebuilt_report_dates"],
            "rows": auto_features["rebuilt_rows"],
            "features": auto_features["rebuilt_features"],
        }
    return result
