#!/usr/bin/env python3
"""L3 pre-knife impact audit — moth coupling + codegraph explore once.

Binding practice (eng_gov §15 / goal): before an L3 knife that touches
``backend/services`` / config / deletion, run this once per logical name so
impact is planned before code moves. Reuses existing tools; no new framework.

Usage:
  PYTHONPATH=backend python backend/scripts/pre_knife_audit.py <name>
  scripts/chunkyctl pre-knife <name>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(cmd: list[str]) -> int:
    print(f"+ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO), check=False)
    return int(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chunkyctl pre-knife",
        description=__doc__,
    )
    parser.add_argument(
        "name",
        nargs="?",
        default="",
        help="symbol / table / module name for moth --impact and codegraph callers",
    )
    args = parser.parse_args(argv)
    name = str(args.name or "").strip()
    if not name or name.startswith("-"):
        print("ERROR: pre-knife requires a non-empty <name>", file=sys.stderr)
        return 2

    rc_moth = _run(["moth", "coupling", "--repo", ".", "--impact", name])
    rc_cg = _run(["codegraph", "explore", f"{name} callers"])
    if rc_moth != 0 or rc_cg != 0:
        print(
            f"FAIL pre-knife: moth_rc={rc_moth} codegraph_rc={rc_cg}",
            file=sys.stderr,
        )
        return 1
    print(f"OK pre-knife: impact audit complete for {name!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
