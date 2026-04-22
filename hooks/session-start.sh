#!/bin/sh
set -eu

PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
SETTINGS_PATH=${HOME}/.claude/settings.json

has_configured_command() {
  if [ ! -f "$SETTINGS_PATH" ]; then
    return 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$SETTINGS_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

if not isinstance(data, dict):
    raise SystemExit(1)

env = data.get("env")
if not isinstance(env, dict):
    raise SystemExit(1)

value = env.get("CLAUDE_CODE_TEAMMATE_COMMAND", "")
raise SystemExit(0 if isinstance(value, str) and value.strip() else 1)
PY
    return $?
  fi

  grep -Eq '"CLAUDE_CODE_TEAMMATE_COMMAND"[[:space:]]*:[[:space:]]*"[^[:space:]"][^"]*"' "$SETTINGS_PATH"
}

if has_configured_command; then
  exit 0
fi

if "$PLUGIN_ROOT/bin/codex-teammate" install; then
  exit 0
else
  status=$?
fi

if [ "$status" -eq 127 ]; then
  exit 0
fi

exit "$status"
