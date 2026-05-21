#!/usr/bin/env bash

require_gcp_explicit_ok() {
    local entrypoint="${1:-GCP entrypoint}"
    if [[ "${CHUNKYMONKEY_GCP_EXPLICIT_OK:-0}" != "1" ]]; then
        echo "[gcp_guard] BLOCK: ${entrypoint} requires GCP access." >&2
        echo "[gcp_guard] GCP is controlled-use: state scope/cost/artifacts/stop plan before cloud work." >&2
        echo "[gcp_guard] Set CHUNKYMONKEY_GCP_EXPLICIT_OK=1 only for an intentional GCP command." >&2
        exit 3
    fi
}
