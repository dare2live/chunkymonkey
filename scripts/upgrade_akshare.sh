#!/bin/bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_BIN="${PIP_BIN:-pip3}"

old_v="$($PYTHON_BIN - <<'PY' 2>/dev/null || true
try:
    import akshare as ak
    print(getattr(ak, "__version__", "unknown"))
except Exception:
    print("")
PY
)"

if ! command -v "$PIP_BIN" >/dev/null 2>&1; then
  echo "pip not found: $PIP_BIN" >&2
  exit 1
fi

echo "akshare current: v${old_v:-not-installed}"
"$PIP_BIN" install --upgrade akshare --upgrade-strategy only-if-needed --timeout 30

new_v="$($PYTHON_BIN - <<'PY' 2>/dev/null || true
try:
    import akshare as ak
    print(getattr(ak, "__version__", "unknown"))
except Exception:
    print("")
PY
)"
echo "akshare updated: v${old_v:-not-installed} -> v${new_v:-unknown}"
