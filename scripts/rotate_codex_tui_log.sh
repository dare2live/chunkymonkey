#!/usr/bin/env bash
# Byte-based guard for Codex TUI tracing logs.
#
# Rationale: codex-tui.log can contain extremely long single-line tracing spans.
# Line-based retention (for example tail -n 5000) is not safe when one line can
# be multiple GB. This script rotates by bytes and preserves the live inode.
set -euo pipefail

LOG_PATH="${CODEX_TUI_LOG:-$HOME/.codex/log/codex-tui.log}"
ARCHIVE_DIR="${CODEX_TUI_ARCHIVE_DIR:-$HOME/.codex/log/archive}"
MAX_BYTES="${CODEX_TUI_MAX_BYTES:-104857600}"      # 100 MiB
KEEP_BYTES="${CODEX_TUI_KEEP_BYTES:-52428800}"     # 50 MiB
KEEP_ARCHIVES="${CODEX_TUI_KEEP_ARCHIVES:-6}"

if [[ ! -f "$LOG_PATH" ]]; then
  exit 0
fi

size=$(stat -f%z "$LOG_PATH" 2>/dev/null || stat -c%s "$LOG_PATH")
if [[ "$size" -le "$MAX_BYTES" ]]; then
  exit 0
fi

mkdir -p "$ARCHIVE_DIR"
ts=$(date +%Y%m%d_%H%M%S)
archive="$ARCHIVE_DIR/codex-tui.tail.$ts.log"

tail -c "$KEEP_BYTES" "$LOG_PATH" > "$archive"
: > "$LOG_PATH"

gzip -f "$archive" 2>/dev/null || true

find "$ARCHIVE_DIR" -name 'codex-tui.tail.*.log.gz' -type f -print \
  | sort -r \
  | awk -v keep="$KEEP_ARCHIVES" 'NR > keep {print}' \
  | xargs -r rm -f

echo "rotated $LOG_PATH: ${size} bytes -> 0; archived tail to ${archive}.gz"
