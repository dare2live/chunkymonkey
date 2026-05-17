#!/usr/bin/env bash
# Test TDXHub HQ server reachability from the current host.
#
# Intended usage on the GCP VM:
#   cd ~/chunkymonkey
#   bash gcp/test_tdxhub_connectivity.sh
#
# Optional:
#   CM_TDX_SERVERS="110.41.147.114:7709,124.70.199.56:7709" \
#   CM_TDX_TEST_TIMEOUT=2 \
#   bash gcp/test_tdxhub_connectivity.sh

set -euo pipefail

TIMEOUT="${CM_TDX_TEST_TIMEOUT:-2}"
SYMBOL="${CM_TDX_TEST_SYMBOL:-000001}"
OFFSET="${CM_TDX_TEST_OFFSET:-1}"

DEFAULT_SERVERS=(
  "110.41.147.114:7709"
  "124.70.199.56:7709"
  "121.36.225.169:7709"
  "123.60.70.228:7709"
  "123.60.73.44:7709"
  "124.70.133.119:7709"
  "124.71.187.72:7709"
  "124.71.187.122:7709"
  "116.205.163.254:7709"
)

if [[ -n "${CM_TDX_SERVERS:-}" ]]; then
  IFS=',' read -r -a SERVERS <<< "${CM_TDX_SERVERS}"
else
  SERVERS=("${DEFAULT_SERVERS[@]}")
fi

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export PYTHONPATH="${PYTHONPATH:-backend}"

python - "${TIMEOUT}" "${SYMBOL}" "${OFFSET}" "${SERVERS[@]}" <<'PY'
from __future__ import annotations

import json
import socket
import sys
import time


timeout = float(sys.argv[1])
symbol = sys.argv[2]
offset = int(sys.argv[3])
servers = sys.argv[4:]


def split_server(value: str) -> tuple[str, int]:
    host, port = value.strip().split(":", 1)
    return host, int(port)


def tcp_probe(host: str, port: int) -> tuple[bool, float, str | None]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, time.perf_counter() - started, None
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return False, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def tdxhub_probe(host: str, port: int) -> tuple[bool, float, int, str | None]:
    started = time.perf_counter()
    client = None
    try:
        from tdxhub.quotes import Quotes

        client = Quotes.factory(
            server=(host, port),
            timeout=timeout,
            auto_retry=False,
            raise_exception=True,
        )
        rows = client.bars_records(
            symbol=symbol,
            frequency=9,
            start=0,
            offset=offset,
        )
        row_count = len(rows or [])
        return row_count > 0, time.perf_counter() - started, row_count, None
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return False, time.perf_counter() - started, 0, f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


results: list[dict] = []
for raw_server in servers:
    host, port = split_server(raw_server)
    tcp_ok, tcp_elapsed, tcp_error = tcp_probe(host, port)
    tdx_ok = False
    tdx_elapsed = 0.0
    rows = 0
    tdx_error = "tcp_failed"
    if tcp_ok:
        tdx_ok, tdx_elapsed, rows, tdx_error = tdxhub_probe(host, port)
    result = {
        "server": f"{host}:{port}",
        "tcp_ok": tcp_ok,
        "tcp_elapsed_s": round(tcp_elapsed, 3),
        "tdxhub_ok": tdx_ok,
        "tdxhub_elapsed_s": round(tdx_elapsed, 3),
        "bars_rows": rows,
        "error": tdx_error or tcp_error,
    }
    results.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)

ok = [item for item in results if item["tdxhub_ok"]]
print(
    json.dumps(
        {
            "summary": {
                "servers_tested": len(results),
                "tdxhub_ok": len(ok),
                "best_servers": [item["server"] for item in ok[:5]],
            }
        },
        ensure_ascii=False,
    ),
    flush=True,
)
sys.exit(0 if ok else 2)
PY
