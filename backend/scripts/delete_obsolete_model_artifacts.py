#!/usr/bin/env python3
"""Delete obsolete model-scoped rows and model files after dependency checks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.model_artifact_gc import (  # noqa: E402
    execute_obsolete_model_artifact_delete,
    plan_obsolete_model_artifact_delete,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--keep-model-id", action="append", default=[])
    parser.add_argument("--current-policy-hash", default=None)
    args = parser.parse_args()
    keep_model_ids = {str(item) for item in args.keep_model_id if str(item).strip()}
    with get_conn() as conn:
        if args.execute:
            result = execute_obsolete_model_artifact_delete(
                conn,
                run_id=args.run_id,
                approve=True,
                keep_model_ids=keep_model_ids,
                current_policy_hash=args.current_policy_hash,
            )
        else:
            result = plan_obsolete_model_artifact_delete(
                conn,
                keep_model_ids=keep_model_ids,
                current_policy_hash=args.current_policy_hash,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
