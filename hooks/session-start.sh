#!/bin/sh
set -eu

SETTINGS_PATH=${HOME}/.claude/settings.json
ORIENTATION_MESSAGE="claude-anyteam is installed; Agent Teams teammates named codex-* route to Codex and gemini-* route to Gemini CLI. Docs: https://github.com/JonathanRosado/claude-anyteam"
DRIFT_WARNING='claude-anyteam: settings drifted — run `claude-anyteam install` to repair'

settings_has_required_env() {
  if [ ! -f "$SETTINGS_PATH" ]; then
    return 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    if python3 - "$SETTINGS_PATH" <<'PY'
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

command = env.get("CLAUDE_CODE_TEAMMATE_COMMAND", "")
binary = env.get("CLAUDE_ANYTEAM_BINARY", "")
gemini_binary = env.get("CLAUDE_ANYTEAM_GEMINI_BINARY", "")

raise SystemExit(
    0
    if (
        isinstance(command, str)
        and command.strip()
        and isinstance(binary, str)
        and binary.strip()
        and isinstance(gemini_binary, str)
        and gemini_binary.strip()
    )
    else 1
)
PY
    then
      return 0
    fi
    return 1
  fi

  grep -Eq '"CLAUDE_CODE_TEAMMATE_COMMAND"[[:space:]]*:[[:space:]]*"[^[:space:]"][^"]*"' "$SETTINGS_PATH" \
    && grep -Eq '"CLAUDE_ANYTEAM_BINARY"[[:space:]]*:[[:space:]]*"[^[:space:]"][^"]*"' "$SETTINGS_PATH" \
    && grep -Eq '"CLAUDE_ANYTEAM_GEMINI_BINARY"[[:space:]]*:[[:space:]]*"[^[:space:]"][^"]*"' "$SETTINGS_PATH"
}

if settings_has_required_env; then
  printf '%s\n' "$ORIENTATION_MESSAGE"
  exit 0
fi

printf '%s\n' "$DRIFT_WARNING"
exit 0
