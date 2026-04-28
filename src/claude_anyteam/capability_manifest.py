"""Rich capability-manifest disk cache and broadcast helpers.

09 R12 stores each teammate's Agent Card under
``~/.claude/teams/<team>/manifests/<agent>.json`` and broadcasts a
``capability_manifest_updated`` mailbox event so peers can invalidate their
local cache. 09 R13's wrapper MCP tool reads the in-memory cache populated
here, matching 08 §6.4's hybrid lookup: cheap flags in ``config.json``, rich
manifest from the peer cache.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from claude_teams._filelock import config_lock, file_lock

from . import logger
from .capabilities import peer_prompt_fragments_for
from .messages import CapabilityManifestUpdatedIn, CapabilityManifestUpdatedOut, now_iso, parse_protocol_text

TEAMS_ROOT = Path.home() / ".claude" / "teams"
MANIFEST_MESSAGE_KIND = "capability_manifest_updated"


def team_dir(team: str) -> Path:
    return TEAMS_ROOT / team


def manifests_dir_for_team_dir(team_root: Path) -> Path:
    return team_root / "manifests"


def manifest_path_for_team_dir(team_root: Path, agent_name: str) -> Path:
    return manifests_dir_for_team_dir(team_root) / f"{agent_name}.json"


def manifest_path(team: str, agent_name: str) -> Path:
    return manifest_path_for_team_dir(team_dir(team), agent_name)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def write_manifest(team_root: Path, agent_name: str, manifest: dict[str, Any]) -> Path:
    """Atomically write ``agent_name``'s rich Agent Card manifest."""
    path = manifest_path_for_team_dir(team_root, agent_name)
    with config_lock(team_root):
        _atomic_write_json(path, manifest)
    return path


def delete_manifest(team_root: Path, agent_name: str) -> bool:
    path = manifest_path_for_team_dir(team_root, agent_name)
    with config_lock(team_root):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


def read_manifest_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"manifest at {path} is not a JSON object")
    return raw


def _manifest_agent_name(path: Path, manifest: dict[str, Any]) -> str:
    for key in ("agent_name", "agentName", "name"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            return value
    return path.stem


def manifest_version(manifest: dict[str, Any] | None) -> str | None:
    if not manifest:
        return None
    value = manifest.get("capability_version", manifest.get("capabilityVersion"))
    return str(value) if value is not None else None


def load_manifest_cache(team_root: Path, *, self_name: str | None = None) -> dict[str, dict[str, Any]]:
    """Load all cached manifests for ``team_root`` into memory.

    This is the eager R12 cache fill. Callers may pass ``self_name`` to keep
    their peer cache peer-only, but wrapper MCP intentionally leaves it unset
    so it can also answer questions about the caller's own manifest.
    """
    cache: dict[str, dict[str, Any]] = {}
    root = manifests_dir_for_team_dir(team_root)
    if not root.exists():
        return cache
    for path in sorted(root.glob("*.json")):
        try:
            manifest = read_manifest_file(path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warn("capability_manifest.load_skip", path=str(path), error=str(e))
            continue
        agent = _manifest_agent_name(path, manifest)
        if self_name is not None and agent == self_name:
            continue
        cache[agent] = manifest
    return cache


def _team_config_member_names(team_root: Path) -> list[str] | None:
    """Return roster names from ``config.json`` or ``None`` when unavailable.

    R12 originally filled the cache by scanning ``manifests/*.json``.  The
    HydraTeams-style prewarm path is roster-driven instead: at formation/boot,
    enumerate known teammates and call the protocol helper
    ``read_agent_manifest`` for each one.  Falling back to a manifest-dir scan
    keeps older tests and partially-created teams tolerant when ``config.json``
    is not available yet.
    """

    try:
        raw = json.loads((team_root / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warn("capability_manifest.config_scan_fail", team_root=str(team_root), error=str(e))
        return None

    members = raw.get("members") if isinstance(raw, dict) else None
    if not isinstance(members, list):
        return []

    names: list[str] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        name = member.get("name")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def load_manifest_cache_from_roster(
    team: str,
    *,
    root: Path = TEAMS_ROOT,
) -> dict[str, dict[str, Any]] | None:
    """Prewarm manifests by walking the team roster and reading each Agent Card.

    This is the HydraTeams-pattern lift: do the capability-manifest discovery
    once at team formation / wrapper boot, before the first peer-DM or inbox
    poll, rather than lazily discovering on first invocation.  ``None`` means
    "no roster to drive from"; callers should fall back to the legacy directory
    scan in that case.
    """

    team_root = root / team
    names = _team_config_member_names(team_root)
    if names is None:
        return None

    from . import protocol_io as pio

    cache: dict[str, dict[str, Any]] = {}
    for agent_name in names:
        # Adapter peer caches historically kept the caller's own manifest in
        # memory too; keep that behavior so wrapper MCP can answer self-lookups.
        try:
            manifest = pio.read_agent_manifest(team, agent_name, teams_root=root)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warn(
                "capability_manifest.roster_load_skip",
                team=team,
                agent=agent_name,
                error=str(e),
            )
            continue
        if manifest is None:
            continue
        agent = _manifest_agent_name(manifest_path_for_team_dir(team_root, agent_name), manifest)
        cache[agent] = manifest
    return cache


def manifest_version_map(cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Return ``agent -> capability_version`` for every cached manifest."""

    versions: dict[str, str] = {}
    for agent, manifest in cache.items():
        version = manifest_version(manifest)
        if version is not None:
            versions[agent] = version
    return versions


def apply_update_to_cache(
    cache: dict[str, dict[str, Any]],
    team_root: Path,
    update: CapabilityManifestUpdatedIn,
    *,
    self_name: str | None = None,
) -> bool:
    """Apply one ``capability_manifest_updated`` event to ``cache``.

    Returns True when the cache changed. A same-version event is a no-op; a
    capability_version bump invalidates and reloads that peer within the next
    inbox poll cycle, per 09 R12 / 08 §6.4.2.
    """
    agent = update.agent_name
    if not agent or (self_name is not None and agent == self_name):
        return False

    if update.removed:
        return cache.pop(agent, None) is not None

    cached_version = manifest_version(cache.get(agent))
    event_version = str(update.capability_version) if update.capability_version is not None else None
    if agent in cache and event_version is not None and cached_version == event_version:
        return False

    path = Path(update.manifest_path) if update.manifest_path else manifest_path_for_team_dir(team_root, agent)
    try:
        manifest = read_manifest_file(path)
    except FileNotFoundError:
        changed = cache.pop(agent, None) is not None
        logger.info("capability_manifest.removed_on_missing_file", agent=agent, path=str(path))
        return changed
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warn("capability_manifest.reload_fail", agent=agent, path=str(path), error=str(e))
        return False

    cache[agent] = manifest
    logger.info(
        "capability_manifest.cache_reloaded",
        agent=agent,
        capability_version=manifest_version(manifest),
    )
    return True


def _read_inbox_entries_unmarked(team_root: Path, agent_name: str) -> list[dict[str, Any]]:
    path = team_root / "inboxes" / f"{agent_name}.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warn("capability_manifest.inbox_scan_fail", agent=agent_name, error=str(e))
        return []
    return raw if isinstance(raw, list) else []


def refresh_cache_from_inbox(
    cache: dict[str, dict[str, Any]],
    team_root: Path,
    *,
    self_name: str,
    unread_only: bool = False,
) -> int:
    """Refresh ``cache`` by scanning capability update events in our inbox.

    The scan is read-only: it deliberately does not mark messages read, so it
    cannot race with the adapter loop's normal mailbox drain. Wrapper MCP calls
    this before answering the R13 tool, letting long-lived wrapper processes
    observe version bumps without turning inbox ownership into a second writer.
    """
    changed = 0
    for entry in _read_inbox_entries_unmarked(team_root, self_name):
        if not isinstance(entry, dict):
            continue
        if unread_only and entry.get("read") is True:
            continue
        if entry.get("messageKind") not in (None, MANIFEST_MESSAGE_KIND):
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        payload = parse_protocol_text(text)
        if isinstance(payload, CapabilityManifestUpdatedIn):
            if apply_update_to_cache(cache, team_root, payload, self_name=self_name):
                changed += 1
    return changed


def append_manifest_event(
    team_root: Path,
    *,
    sender: str,
    recipient: str,
    event: CapabilityManifestUpdatedOut,
    summary: str | None = None,
) -> None:
    """Append one typed manifest-update event to ``recipient``'s inbox."""
    inbox_dir = team_root / "inboxes"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_path = inbox_dir / f"{recipient}.json"
    lock_path = inbox_dir / ".lock"
    body = event.model_dump_json(by_alias=True, exclude_none=True)
    row = {
        "from": sender,
        "text": body,
        "timestamp": now_iso(),
        "read": False,
        "summary": summary or f"capability_manifest_updated:{event.agent_name}",
        "messageKind": MANIFEST_MESSAGE_KIND,
    }
    with file_lock(lock_path):
        if inbox_path.exists():
            try:
                raw = json.loads(inbox_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = []
        else:
            raw = []
        if not isinstance(raw, list):
            raw = []
        raw.append(row)
        inbox_path.write_text(json.dumps(raw), encoding="utf-8")


def broadcast_manifest_event(
    team_root: Path,
    *,
    sender: str,
    recipients: list[str],
    event: CapabilityManifestUpdatedOut,
) -> int:
    """Broadcast an R12 manifest update to all recipients except sender."""
    delivered = 0
    for recipient in recipients:
        if not recipient or recipient == sender:
            continue
        try:
            append_manifest_event(team_root, sender=sender, recipient=recipient, event=event)
            delivered += 1
        except OSError as e:
            logger.warn(
                "capability_manifest.broadcast_fail",
                sender=sender,
                recipient=recipient,
                error=str(e),
            )
    return delivered


@dataclass
class CapabilityManifestCache:
    """In-memory peer manifest cache for wrapper MCP and adapter loops."""

    team: str
    self_name: str | None = None
    root: Path = TEAMS_ROOT
    manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    capability_versions: dict[str, str] = field(default_factory=dict)

    @property
    def entries(self) -> dict[str, dict[str, Any]]:
        """Alias for the cached manifest entries keyed by teammate name.

        The implementation historically called this map ``manifests``.  The
        prewarm path uses the more explicit Agent-Card wording from the §3
        lift target; keep both names pointing at the same cache so older call
        sites and tests remain compatible.
        """

        return self.manifests

    @entries.setter
    def entries(self, value: dict[str, dict[str, Any]]) -> None:
        self.manifests = value
        self.capability_versions = manifest_version_map(self.manifests)

    @property
    def team_root(self) -> Path:
        return self.root / self.team

    def load_startup(self) -> None:
        # Prewarm the complete local Agent Card cache. Adapter consumers
        # usually read peers from it; wrapper MCP can also answer questions
        # about the caller's own manifest without a special case. Prefer the
        # HydraTeams-style roster walk (read_agent_manifest for every member)
        # so discovery is complete before the first inbox poll / peer-DM.
        # Fall back to the legacy directory scan when no team config exists
        # yet (e.g. focused unit tests with only manifests on disk).
        #
        # S10b ablation per S10-ablation-implementation-spec.md §1: when
        # ``CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE=1``, skip the load so peers
        # have no manifest data even if the JSON files exist on disk. This
        # tests R12+R13+R14 jointly — does the manifest layer do real work?
        if os.environ.get("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE") == "1":
            logger.info("capability_manifest.cache_disabled_by_env")
            self.manifests = {}
            self.capability_versions = {}
            return
        manifests = load_manifest_cache_from_roster(
            self.team,
            root=self.root,
        )
        if manifests is None:
            manifests = load_manifest_cache(self.team_root)
        self.manifests = manifests
        self.capability_versions = manifest_version_map(self.manifests)

    def refresh_from_inbox(self, *, unread_only: bool = False) -> int:
        if not self.self_name:
            return 0
        changed = refresh_cache_from_inbox(
            self.manifests,
            self.team_root,
            self_name=self.self_name,
            unread_only=unread_only,
        )
        if changed:
            self.capability_versions = manifest_version_map(self.manifests)
        return changed

    def apply_update(self, update: CapabilityManifestUpdatedIn) -> bool:
        # S10b ablation: no-op when manifest cache is disabled — incoming
        # capability updates are silently dropped so peers remain unaware.
        if os.environ.get("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE") == "1":
            return False
        changed = apply_update_to_cache(
            self.manifests,
            self.team_root,
            update,
            self_name=self.self_name,
        )
        if update.removed:
            self.capability_versions.pop(update.agent_name, None)
        elif changed:
            version = manifest_version(self.manifests.get(update.agent_name))
            if version is not None:
                self.capability_versions[update.agent_name] = version
            else:
                self.capability_versions.pop(update.agent_name, None)
        return changed

    def get(self, agent_name: str) -> dict[str, Any] | None:
        return self.manifests.get(agent_name)

    def peer_prompt_fragments_for(self, requester: str) -> str:
        """Return R14 peer-capability prompt fragments for ``requester``."""

        return peer_prompt_fragments_for(requester, self)
