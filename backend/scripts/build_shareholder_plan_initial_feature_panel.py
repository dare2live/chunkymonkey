#!/usr/bin/env python3
"""Build the research-only initial shareholder-plan daily feature panel."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.shareholder_plan_initial_event import build_shareholder_plan_initial_event  # noqa: E402
from services.shareholder_plan_initial_feature_panel import (  # noqa: E402
    DEFAULT_CONTEXT_FEATURES,
    DEFAULT_FEATURE_SET_ID,
    DEFAULT_LABELS,
    DEFAULT_WINDOW_DAYS,
    build_shareholder_plan_initial_feature_panel,
)


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--feature-set-id", default=DEFAULT_FEATURE_SET_ID)
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--context-features", default=",".join(DEFAULT_CONTEXT_FEATURES))
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--no-require-complete-labels", action="store_true")
    parser.add_argument("--no-require-complete-context", action="store_true")
    parser.add_argument("--no-refresh-initial-events", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        refresh_result = None
        if not args.no_refresh_initial_events:
            refresh_result = build_shareholder_plan_initial_event(conn)
        result = build_shareholder_plan_initial_feature_panel(
            conn,
            run_id=args.run_id,
            feature_set_id=args.feature_set_id,
            labels=_parse_csv(args.labels),
            context_features=_parse_csv(args.context_features),
            window_days=args.window_days,
            start_date=args.start_date,
            end_date=args.end_date,
            require_complete_labels=not args.no_require_complete_labels,
            require_complete_context=not args.no_require_complete_context,
        )
        if refresh_result is not None:
            result["initial_event_refresh"] = refresh_result
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
