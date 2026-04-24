"""Persistent Claude Code settings installer for claude-anyteam.

This writes the leader-side environment variables that Claude Code reads at
startup so users do not need to hand-edit ~/.claude/settings.json, and it
also manages the ``teammateMode`` key in ~/.claude.json so Claude Code
routes teammate spawns through the tmux/psmux pane backend (required for
out-of-process teammates to appear in the TUI presence line).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TEAMMATE_COMMAND_KEY = "CLAUDE_CODE_TEAMMATE_COMMAND"
TEAMMATE_BINARY_KEY = "CLAUDE_ANYTEAM_BINARY"
LEGACY_TEAMMATE_BINARY_KEY = "CODEX_TEAMMATE_BINARY"

SHIM_BASENAME = "claude-anyteam-spawn-shim"
LEGACY_SHIM_BASENAME = "codex-teammate-spawn-shim"
BINARY_BASENAME = "claude-anyteam"
LEGACY_BINARY_BASENAME = "codex-teammate"

MANAGED_BINARY_KEYS = (TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY)
MANAGED_SHIM_BASENAMES = {SHIM_BASENAME, LEGACY_SHIM_BASENAME}
MANAGED_BINARY_BASENAMES = {BINARY_BASENAME, LEGACY_BINARY_BASENAME}

TEAMMATE_MODE_KEY = "teammateMode"
TEAMMATE_MODE_TARGET_VALUE = "tmux"
STATE_SCHEMA_VERSION = 1

PLUGIN_DATA_DIR_NAME = "claude-anyteam-claude-anyteam"
STATE_FILE_NAME = "install-state.json"


@dataclass(frozen=True)
class ManagedPaths:
    settings_path: Path
    shim_path: Path
    binary_path: Path


@dataclass(frozen=True)
class PrereqCheck:
    """Result of checking for a terminal multiplexer on PATH."""

    found: bool
    binary: str | None
    path: Path | None
    platform: str  # "linux" | "darwin" | "windows" | <sys.platform fallback>


@dataclass(frozen=True)
class TeammateModeResult:
    """Outcome of install_teammate_mode()."""

    claude_json_path: Path
    state_path: Path
    previous_value: str | None  # what the key held before we touched it (None if absent)
    new_value: str  # what the key holds now ("tmux" in every success branch)
    wrote_value: bool  # True if we mutated claude.json; False on the already-"tmux" no-op
    state_written: bool  # True if a state file was created/overwritten


@dataclass(frozen=True)
class TeammateModeRevertResult:
    """Outcome of uninstall_teammate_mode()."""

    claude_json_path: Path
    state_path: Path
    state_was_present: bool
    managed_by_us: bool  # state.teammateMode_set_by_anyteam at read time
    restored_value: str | None  # value put back (None = key removed)
    claude_json_touched: bool  # True if we mutated claude.json
    state_file_removed: bool


@dataclass(frozen=True)
class InstallResult:
    paths: ManagedPaths
    created_file: bool
    changed: dict[str, str]
    removed_legacy_keys: tuple[str, ...] = ()
    prereq: PrereqCheck | None = None
    teammate_mode: TeammateModeResult | None = None

    @property
    def changed_anything(self) -> bool:
        return (
            self.created_file
            or bool(self.changed)
            or bool(self.removed_legacy_keys)
            or (self.teammate_mode is not None and self.teammate_mode.wrote_value)
        )


@dataclass(frozen=True)
class UninstallResult:
    settings_path: Path
    removed: dict[str, str]
    skipped: dict[str, str]
    file_present: bool
    teammate_mode: TeammateModeRevertResult | None = None

    @property
    def changed_anything(self) -> bool:
        return bool(self.removed) or (
            self.teammate_mode is not None and self.teammate_mode.claude_json_touched
        )


class InstallError(ValueError):
    """Raised when install/uninstall cannot safely update Claude settings.

    Some install failures warrant a distinct CLI exit code (e.g. user declining
    the teammateMode overwrite prompt). Callers may attach a ``cli_exit_code``
    attribute on the exception instance to steer the CLI; default is 2.
    """



# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def default_claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def default_state_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "data" / PLUGIN_DATA_DIR_NAME / STATE_FILE_NAME



# ---------------------------------------------------------------------------
# Path discovery (existing install/uninstall helpers, unchanged)
# ---------------------------------------------------------------------------

def _resolve_executable(name_or_path: str | None) -> Path | None:
    if not name_or_path:
        return None

    candidate = Path(name_or_path)
    raw = str(candidate)
    has_sep = os.sep in raw or (os.altsep is not None and os.altsep in raw)
    if candidate.parent != Path(".") or has_sep:
        if candidate.exists():
            return candidate.resolve()
        return None

    found = shutil.which(name_or_path)
    if not found:
        return None
    return Path(found).resolve()



def _first_resolved(*candidates: str | None) -> Path | None:
    for candidate in candidates:
        resolved = _resolve_executable(candidate)
        if resolved is not None:
            return resolved
    return None



def discover_managed_paths(
    *,
    settings_path: Path | str | None = None,
    argv0: str | None = None,
    shim_path: str | None = None,
    binary_path: str | None = None,
) -> ManagedPaths:
    raw_settings = Path(settings_path) if settings_path is not None else default_settings_path()
    settings = raw_settings.expanduser().resolve()
    current = _resolve_executable(argv0)

    resolved_binary = _resolve_executable(binary_path)
    if resolved_binary is None and current is not None and current.name in MANAGED_BINARY_BASENAMES:
        resolved_binary = current
    if resolved_binary is None:
        resolved_binary = _first_resolved(BINARY_BASENAME, LEGACY_BINARY_BASENAME)

    resolved_shim = _resolve_executable(shim_path)
    if resolved_shim is None and current is not None:
        if current.name in MANAGED_BINARY_BASENAMES:
            for sibling_name in (SHIM_BASENAME, LEGACY_SHIM_BASENAME):
                sibling = current.with_name(sibling_name)
                if sibling.exists():
                    resolved_shim = sibling.resolve()
                    break
        elif current.name in MANAGED_SHIM_BASENAMES:
            resolved_shim = current
    if resolved_shim is None:
        resolved_shim = _first_resolved(SHIM_BASENAME, LEGACY_SHIM_BASENAME)

    if resolved_binary is None:
        raise InstallError(
            "Unable to resolve the claude-anyteam binary. Ensure the package is "
            "installed and the console script is on PATH."
        )
    if resolved_shim is None:
        raise InstallError(
            "Unable to resolve the claude-anyteam-spawn-shim binary. Ensure the "
            "package is installed and the console script is on PATH."
        )

    return ManagedPaths(
        settings_path=settings,
        shim_path=resolved_shim,
        binary_path=resolved_binary,
    )



# ---------------------------------------------------------------------------
# JSON I/O (shared by settings.json, claude.json, state file)
# ---------------------------------------------------------------------------

def _load_settings(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw, True



def _env_block(settings: dict[str, Any], *, path: Path, create: bool) -> dict[str, str]:
    env = settings.get("env")
    if env is None:
        if not create:
            return {}
        env = {}
        settings["env"] = env

    if not isinstance(env, dict):
        raise InstallError(f"{path} has an 'env' entry, but it is not a JSON object.")

    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InstallError(
                f"{path} has a non-string entry under 'env'; refusing to overwrite it."
            )

    return env



def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _write_settings(path: Path, settings: dict[str, Any]) -> None:
    _atomic_write_json(path, settings)


def _load_claude_json(path: Path) -> tuple[dict[str, Any], bool]:
    # Same shape/contract as _load_settings but applied to ~/.claude.json.
    if not path.exists():
        return {}, False

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw, True


def _write_claude_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(path, payload)



# ---------------------------------------------------------------------------
# State file (install-state.json) management
# ---------------------------------------------------------------------------

def _load_state(path: Path) -> dict[str, Any] | None:
    """Returns the parsed state dict, or None if absent.

    Does NOT raise on missing-file — that's the expected case for fresh installs
    and for backward-compat with installs that predate this feature.
    """
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw


def _write_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write_json(path, state)


def _delete_state(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False



# ---------------------------------------------------------------------------
# Terminal multiplexer prereq check
# ---------------------------------------------------------------------------

def _platform_name() -> str:
    raw = sys.platform
    if raw.startswith("linux"):
        return "linux"
    if raw == "darwin":
        return "darwin"
    if raw in ("win32", "cygwin"):
        return "windows"
    return raw


def _check_terminal_multiplexer() -> PrereqCheck:
    """Checks PATH for tmux (Linux/mac) or psmux/tmux (Windows).

    Windows prefers ``psmux`` (handles Claude Code's POSIX-shaped teammate-spawn
    command via a PowerShell translator), but accepts a plain ``tmux`` binary if
    the user installed one via Cygwin / MSYS2 / WSL-interop.

    Linux and macOS require ``tmux`` on PATH — there is no reason to look for
    psmux there.
    """
    platform = _platform_name()

    if platform == "windows":
        candidates = ("psmux", "tmux")
    else:
        candidates = ("tmux",)

    for name in candidates:
        found_path = shutil.which(name)
        if found_path:
            return PrereqCheck(
                found=True,
                binary=name,
                path=Path(found_path).resolve(),
                platform=platform,
            )

    return PrereqCheck(found=False, binary=None, path=None, platform=platform)


def _install_instructions(platform: str) -> str:
    if platform == "linux":
        return (
            "  Debian/Ubuntu: sudo apt install tmux\n"
            "  Fedora/RHEL:   sudo dnf install tmux\n"
            "  Arch:          sudo pacman -S tmux"
        )
    if platform == "darwin":
        return "  macOS (Homebrew): brew install tmux"
    if platform == "windows":
        return (
            "  Recommended: winget install psmux\n"
            "  Also supported: choco install psmux / scoop install psmux"
        )
    return "  Install tmux via your platform's package manager."



# ---------------------------------------------------------------------------
# teammateMode install/uninstall
# ---------------------------------------------------------------------------

def install_teammate_mode(
    *,
    claude_json_path: Path,
    state_path: Path,
    prompt_fn: Callable[[str], bool],
) -> TeammateModeResult:
    """Ensures ~/.claude.json has teammateMode='tmux', recording what we did in state.

    Branches per the approved install spec:
      * key absent → write 'tmux'; state records original=None, set_by=True.
      * key == 'tmux' → no-op on claude.json; state records original='tmux', set_by=False.
      * key in {'auto', 'in-process', other} → call prompt_fn(current_value).
          - True  → overwrite to 'tmux'; state records original=current, set_by=True.
          - False → raise InstallError with cli_exit_code=3 (no state, no mutation).
    """
    claude_json, existed = _load_claude_json(claude_json_path)
    current = claude_json.get(TEAMMATE_MODE_KEY)

    if current is not None and not isinstance(current, str):
        raise InstallError(
            f"{claude_json_path} has a non-string {TEAMMATE_MODE_KEY!r} value; refusing to touch it."
        )

    # Case 1: absent.
    if current is None:
        claude_json[TEAMMATE_MODE_KEY] = TEAMMATE_MODE_TARGET_VALUE
        _write_claude_json(claude_json_path, claude_json)
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "teammateMode_original": None,
            "teammateMode_set_by_anyteam": True,
        }
        _write_state(state_path, state)
        return TeammateModeResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            previous_value=None,
            new_value=TEAMMATE_MODE_TARGET_VALUE,
            wrote_value=True,
            state_written=True,
        )

    # Case 2: already tmux.
    if current == TEAMMATE_MODE_TARGET_VALUE:
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "teammateMode_original": TEAMMATE_MODE_TARGET_VALUE,
            "teammateMode_set_by_anyteam": False,
        }
        _write_state(state_path, state)
        return TeammateModeResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            previous_value=TEAMMATE_MODE_TARGET_VALUE,
            new_value=TEAMMATE_MODE_TARGET_VALUE,
            wrote_value=False,
            state_written=True,
        )

    # Case 3: something else. Prompt before overwriting.
    if not prompt_fn(current):
        err = InstallError(
            f"Install aborted: existing {TEAMMATE_MODE_KEY}={current!r} in {claude_json_path}\n"
            "  claude-anyteam needs teammateMode=\"tmux\" to route teammates through the pane backend.\n"
            "  Re-run with --assume-yes to accept, or manually set teammateMode=\"tmux\" in ~/.claude.json."
        )
        err.cli_exit_code = 3  # type: ignore[attr-defined]
        raise err

    claude_json[TEAMMATE_MODE_KEY] = TEAMMATE_MODE_TARGET_VALUE
    _write_claude_json(claude_json_path, claude_json)
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "teammateMode_original": current,
        "teammateMode_set_by_anyteam": True,
    }
    _write_state(state_path, state)
    return TeammateModeResult(
        claude_json_path=claude_json_path,
        state_path=state_path,
        previous_value=current,
        new_value=TEAMMATE_MODE_TARGET_VALUE,
        wrote_value=True,
        state_written=True,
    )


def uninstall_teammate_mode(
    *,
    claude_json_path: Path,
    state_path: Path,
) -> TeammateModeRevertResult:
    """Reverses whatever install_teammate_mode did, using the state file as ground truth.

    No state file (fresh install pre-feature, or user hand-deleted) → no-op.
    State exists but set_by_anyteam=False → no-op on claude.json; state file is cleared.
    State exists and set_by_anyteam=True → restore teammateMode_original (None = remove key).

    Deleting the state file is best-effort; failure does not block env unwind.
    """
    state = _load_state(state_path)
    if state is None:
        return TeammateModeRevertResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            state_was_present=False,
            managed_by_us=False,
            restored_value=None,
            claude_json_touched=False,
            state_file_removed=False,
        )

    managed = bool(state.get("teammateMode_set_by_anyteam"))
    original = state.get("teammateMode_original")
    if original is not None and not isinstance(original, str):
        # Corrupted state. Don't mutate claude.json but do remove the state file.
        state_removed = _delete_state(state_path)
        return TeammateModeRevertResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            state_was_present=True,
            managed_by_us=False,
            restored_value=None,
            claude_json_touched=False,
            state_file_removed=state_removed,
        )

    if not managed:
        # We didn't own the value — leave claude.json alone, delete state.
        state_removed = _delete_state(state_path)
        return TeammateModeRevertResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            state_was_present=True,
            managed_by_us=False,
            restored_value=None,
            claude_json_touched=False,
            state_file_removed=state_removed,
        )

    claude_json, existed = _load_claude_json(claude_json_path)
    touched = False
    if original is None:
        if TEAMMATE_MODE_KEY in claude_json:
            claude_json.pop(TEAMMATE_MODE_KEY, None)
            touched = True
    else:
        if claude_json.get(TEAMMATE_MODE_KEY) != original:
            claude_json[TEAMMATE_MODE_KEY] = original
            touched = True

    if touched:
        if not claude_json and not existed:
            # Edge case: we'd be writing an empty object to a non-existent file.
            # Leave claude.json uncreated.
            touched = False
        else:
            _write_claude_json(claude_json_path, claude_json)

    state_removed = _delete_state(state_path)
    return TeammateModeRevertResult(
        claude_json_path=claude_json_path,
        state_path=state_path,
        state_was_present=True,
        managed_by_us=True,
        restored_value=original,
        claude_json_touched=touched,
        state_file_removed=state_removed,
    )



# ---------------------------------------------------------------------------
# Top-level install / uninstall
# ---------------------------------------------------------------------------

def install(
    *,
    settings_path: Path | str | None = None,
    argv0: str | None = None,
    shim_path: str | None = None,
    binary_path: str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
    prompt_fn: Callable[[str], bool] | None = None,
    prereq_check_fn: Callable[[], PrereqCheck] | None = None,
) -> InstallResult:
    """Full install: prereq check → env block write → teammateMode update.

    Install is all-or-nothing. If teammateMode can't be set (user declines the
    prompt), the env block written in this call is rolled back so the user
    is left in their original state.

    prereq_check_fn defaults to the real PATH probe. Tests inject a stub to
    exercise the found-or-missing branches without touching shutil.which.
    prompt_fn defaults to an auto-decline (False) so a scripted install that
    does not pass --assume-yes will fail loudly rather than hang; real TTY
    prompting is handled in cli.py.
    """
    checker = prereq_check_fn if prereq_check_fn is not None else _check_terminal_multiplexer
    prereq = checker()
    if not prereq.found:
        raise InstallError(
            "claude-anyteam requires a terminal multiplexer on PATH; none was found.\n"
            "Install one of:\n"
            f"{_install_instructions(prereq.platform)}\n"
            "After installing, re-run `claude-anyteam install`."
        )

    paths = discover_managed_paths(
        settings_path=settings_path,
        argv0=argv0,
        shim_path=shim_path,
        binary_path=binary_path,
    )

    settings, existed = _load_settings(paths.settings_path)
    pre_env_snapshot = copy.deepcopy(settings.get("env"))
    env = _env_block(settings, path=paths.settings_path, create=True)

    desired = {
        TEAMMATE_COMMAND_KEY: str(paths.shim_path),
        TEAMMATE_BINARY_KEY: str(paths.binary_path),
    }
    changed: dict[str, str] = {}
    for key, value in desired.items():
        if env.get(key) != value:
            env[key] = value
            changed[key] = value

    removed_legacy: list[str] = []
    legacy_value = env.get(LEGACY_TEAMMATE_BINARY_KEY)
    if legacy_value is not None and _looks_managed(LEGACY_TEAMMATE_BINARY_KEY, legacy_value):
        env.pop(LEGACY_TEAMMATE_BINARY_KEY, None)
        removed_legacy.append(LEGACY_TEAMMATE_BINARY_KEY)

    env_mutation = bool(changed) or bool(removed_legacy) or not existed
    if env_mutation:
        _write_settings(paths.settings_path, settings)

    resolved_claude_json = (
        Path(claude_json_path).expanduser().resolve()
        if claude_json_path is not None
        else default_claude_json_path()
    )
    resolved_state_path = (
        Path(state_path).expanduser().resolve()
        if state_path is not None
        else default_state_path()
    )
    effective_prompt = prompt_fn if prompt_fn is not None else (lambda _current: False)

    try:
        mode_result = install_teammate_mode(
            claude_json_path=resolved_claude_json,
            state_path=resolved_state_path,
            prompt_fn=effective_prompt,
        )
    except InstallError:
        _rollback_env_block(
            settings=settings,
            path=paths.settings_path,
            pre_env_snapshot=pre_env_snapshot,
            pre_existed=existed,
        )
        raise

    return InstallResult(
        paths=paths,
        created_file=not existed,
        changed=changed,
        removed_legacy_keys=tuple(removed_legacy),
        prereq=prereq,
        teammate_mode=mode_result,
    )


def _rollback_env_block(
    *,
    settings: dict[str, Any],
    path: Path,
    pre_env_snapshot: Any,
    pre_existed: bool,
) -> None:
    """Best-effort rollback of the env-block mutation performed earlier in install().

    Called only on post-env-write failure (currently: teammateMode prompt declined).
    If the settings file did not exist before install(), we remove it entirely.
    Otherwise we restore the captured snapshot (or drop the `env` key if it was
    absent originally).
    """
    if not pre_existed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    if pre_env_snapshot is None:
        settings.pop("env", None)
    else:
        settings["env"] = pre_env_snapshot
    _write_settings(path, settings)



def _looks_managed(key: str, value: str) -> bool:
    basename = Path(value).name
    if key == TEAMMATE_COMMAND_KEY:
        return basename in MANAGED_SHIM_BASENAMES
    if key in MANAGED_BINARY_KEYS:
        return basename in MANAGED_BINARY_BASENAMES
    return False



def uninstall(
    *,
    settings_path: Path | str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
) -> UninstallResult:
    raw_path = Path(settings_path) if settings_path is not None else default_settings_path()
    path = raw_path.expanduser().resolve()
    settings, existed = _load_settings(path)

    resolved_claude_json = (
        Path(claude_json_path).expanduser().resolve()
        if claude_json_path is not None
        else default_claude_json_path()
    )
    resolved_state_path = (
        Path(state_path).expanduser().resolve()
        if state_path is not None
        else default_state_path()
    )

    # teammateMode revert is independent of the env-block unwind and should
    # proceed whether or not settings.json exists.
    mode_result = uninstall_teammate_mode(
        claude_json_path=resolved_claude_json,
        state_path=resolved_state_path,
    )

    if not existed:
        return UninstallResult(
            settings_path=path,
            removed={},
            skipped={},
            file_present=False,
            teammate_mode=mode_result,
        )

    env = _env_block(settings, path=path, create=False)
    removed: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for key in (TEAMMATE_COMMAND_KEY, TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY):
        value = env.get(key)
        if value is None:
            continue
        if _looks_managed(key, value):
            removed[key] = value
            env.pop(key, None)
        else:
            skipped[key] = value

    if removed:
        if not env:
            settings.pop("env", None)
        _write_settings(path, settings)

    return UninstallResult(
        settings_path=path,
        removed=removed,
        skipped=skipped,
        file_present=True,
        teammate_mode=mode_result,
    )



# ---------------------------------------------------------------------------
# User-facing summary formatting
# ---------------------------------------------------------------------------

def format_install_message(result: InstallResult) -> str:
    lines = [
        f"Updated {result.paths.settings_path}",
        f"Set env.{TEAMMATE_COMMAND_KEY}={result.paths.shim_path}",
        f"Set env.{TEAMMATE_BINARY_KEY}={result.paths.binary_path}",
    ]
    if result.removed_legacy_keys:
        lines.append(f"Removed legacy env.{LEGACY_TEAMMATE_BINARY_KEY} entry.")

    mode = result.teammate_mode
    if mode is not None:
        if mode.wrote_value:
            if mode.previous_value is None:
                lines.append(f"Set {TEAMMATE_MODE_KEY}=\"tmux\" in {mode.claude_json_path}")
            else:
                lines.append(
                    f"Set {TEAMMATE_MODE_KEY}=\"tmux\" in {mode.claude_json_path} "
                    f"(was {mode.previous_value!r})"
                )
        else:
            lines.append(f"{TEAMMATE_MODE_KEY} already \"tmux\" in {mode.claude_json_path}; no change")

    lines.append("Restart Claude Code for the changes to take effect.")
    if not result.changed_anything:
        lines.insert(1, "The existing settings already matched this install.")
    return "\n".join(lines)



def format_uninstall_message(result: UninstallResult) -> str:
    lines: list[str] = []

    if not result.file_present:
        lines.append(f"No settings file found at {result.settings_path}; nothing to remove.")
    elif result.removed:
        removed_keys = ", ".join(f"env.{key}" for key in result.removed)
        lines.append(f"Updated {result.settings_path}")
        lines.append(f"Removed {removed_keys}")
    elif result.skipped:
        lines.append(f"Updated {result.settings_path}")
        lines.append("No claude-anyteam-managed env keys were removed; existing values were left intact.")
    else:
        lines.append(f"Updated {result.settings_path}")
        lines.append("No claude-anyteam env keys were present; existing settings were left intact.")

    mode = result.teammate_mode
    if mode is not None and mode.state_was_present:
        if mode.managed_by_us and mode.claude_json_touched:
            if mode.restored_value is None:
                lines.append(f"Removed {TEAMMATE_MODE_KEY} from {mode.claude_json_path}")
            else:
                lines.append(
                    f"Restored {TEAMMATE_MODE_KEY}={mode.restored_value!r} in {mode.claude_json_path}"
                )
        elif not mode.managed_by_us:
            lines.append(f"{TEAMMATE_MODE_KEY} was not managed by claude-anyteam; left as-is")

    lines.append("Restart Claude Code for the changes to take effect.")
    return "\n".join(lines)
