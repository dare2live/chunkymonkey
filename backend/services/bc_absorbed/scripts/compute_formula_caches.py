from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute import compute_historical, get_strategy_profiles


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute formula strategy caches sequentially.")
    parser.add_argument("--only", nargs="*", help="Profile ids to compute. Defaults to all formula profiles.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of profiles.")
    args = parser.parse_args()

    profiles = get_strategy_profiles()
    ids = [pid for pid, p in profiles.items() if p.get("signal_source") == "formula"]
    if args.only:
        wanted = set(args.only)
        ids = [pid for pid in ids if pid in wanted]
    if args.limit > 0:
        ids = ids[: args.limit]

    if not ids:
        print("no formula profiles selected")
        return

    for pid in ids:
        profile = profiles[pid]
        print(f"compute_formula_cache:start {pid} {profile['name']}", flush=True)
        t0 = time.time()

        def progress(done: int, total: int) -> None:
            print(f"compute_formula_cache:progress {pid} {done}/{total}", flush=True)

        data = compute_historical(profile, progress_cb=progress)
        with_signal = sum(1 for r in data.values() if int(r.get("signal_count") or 0) > 0)
        print(
            f"compute_formula_cache:done {pid} stocks={len(data)} with_signal={with_signal} elapsed={time.time()-t0:.1f}s",
            flush=True,
        )


if __name__ == "__main__":
    main()
