#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
PYTHON_BIN="${PYTHON_BIN:-python}"
PIP_BIN="${PIP_BIN:-$PYTHON_BIN -m pip}"

old_v="$($PYTHON_BIN - <<'PY' 2>/dev/null || true
try:
    import akshare as ak
    print(getattr(ak, "__version__", "unknown"))
except Exception:
    print("")
PY
)"

echo "akshare current: v${old_v:-not-installed}"
$PIP_BIN install --upgrade akshare --upgrade-strategy only-if-needed --timeout 30

new_v="$($PYTHON_BIN - <<'PY' 2>/dev/null || true
try:
    import akshare as ak
    print(getattr(ak, "__version__", "unknown"))
except Exception:
    print("")
PY
)"
echo "akshare updated: v${old_v:-not-installed} -> v${new_v:-unknown}"
