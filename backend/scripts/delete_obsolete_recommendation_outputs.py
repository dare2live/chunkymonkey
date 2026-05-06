#!/usr/bin/env python3
"""Delete recommendation outputs and artifacts for retired models."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.recommendation_output_gc import (  # noqa: E402
    execute_obsolete_recommendation_output_delete,
    plan_obsolete_recommendation_output_delete,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    with get_conn() as conn:
        if args.execute:
            result = execute_obsolete_recommendation_output_delete(
                conn,
                run_id=args.run_id,
                approve=True,
            )
        else:
            result = plan_obsolete_recommendation_output_delete(conn)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
