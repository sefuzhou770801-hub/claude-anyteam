"""S10a/S10b ablation knobs — runtime env-var disable for peer-capability discovery.

Tests the two scenario-driven ablations that `tools/stress/run_scenario.py` sets
when running S10a + S10b:

- ``CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS=1`` (S10a) — strips the R14
  peer-capability prompt fragments at prompt-build time. Tests whether the
  prompt-level discovery vector carries weight (R14's specific contribution)
  vs peers finding manifests through other means.
- ``CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE=1`` (S10b) — disables the R12 cache
  load AND the R13 wrapper MCP handler AND silently drops cache updates.
  Tests whether the §1 capability layer (R12+R13+R14 jointly) is doing real
  work vs being decorative.

Both knobs are runtime-only — no substrate edits to capabilities.py /
models.py / prompts.py. Per
references/external-claude-code-re/proto-rev-execution-log/specs/
S10-ablation-implementation-spec.md.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import loop as codex_loop
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.capability_manifest import CapabilityManifestCache
from claude_anyteam.config import Settings
from claude_anyteam.messages import CapabilityManifestUpdatedIn


TEAM = "t"
SELF = "self"


def _settings_codex() -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _settings_gemini() -> GeminiSettings:
    return GeminiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
    )


def _settings_kimi() -> KimiSettings:
    return KimiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
    )


def _populated_cache() -> CapabilityManifestCache:
    cache = CapabilityManifestCache(team=TEAM, self_name=SELF)
    cache.manifests = {
        "codex-peer": {
            "agent_name": "codex-peer",
            "capabilities": {
                "thread_fork": {
                    "description": "Fork a Codex App Server thread.",
                    "when_to_use": "When work depends on prior Codex context.",
                    "callable_from_peers": True,
                },
            },
        },
    }
    return cache


# ─────────────────────────────────────────────────────────────────────────
# S10b — CapabilityManifestCache disable knob
# ─────────────────────────────────────────────────────────────────────────


def test_load_startup_no_op_when_manifest_cache_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE", "1")
    # seed real manifests on disk to ensure the ablation skips the load
    manifest_dir = tmp_path / TEAM / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "codex-peer.json").write_text(
        '{"agent_name": "codex-peer", "capabilities": {"thread_fork": {"description": "x"}}}',
        encoding="utf-8",
    )

    cache = CapabilityManifestCache(team=TEAM, self_name=SELF, root=tmp_path)
    cache.load_startup()

    assert cache.manifests == {}, "manifest cache should be empty under S10b ablation"


def test_load_startup_loads_normally_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE", raising=False)
    manifest_dir = tmp_path / TEAM / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "codex-peer.json").write_text(
        '{"agent_name": "codex-peer", "capabilities": {"thread_fork": {"description": "x"}}}',
        encoding="utf-8",
    )

    cache = CapabilityManifestCache(team=TEAM, self_name=SELF, root=tmp_path)
    cache.load_startup()

    assert "codex-peer" in cache.manifests


def test_apply_update_no_op_when_manifest_cache_disabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE", "1")
    cache = CapabilityManifestCache(team=TEAM, self_name=SELF)
    update = CapabilityManifestUpdatedIn(
        from_="codex-peer",
        agent_name="codex-peer",
        version=2,
        capabilities=["thread_fork"],
    )

    result = cache.apply_update(update)

    assert result is False, "apply_update should be no-op under S10b"
    assert "codex-peer" not in cache.manifests, "cache should remain empty"


# ─────────────────────────────────────────────────────────────────────────
# S10a — peer-prompt-fragment disable knob (per-backend loop integration)
# ─────────────────────────────────────────────────────────────────────────


def test_codex_loop_peer_prompt_fragments_empty_when_s10a_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS", "1")
    state = codex_loop.LoopState(
        settings=_settings_codex(),
        peer_manifest_cache=_populated_cache(),
    )

    fragments = codex_loop._peer_prompt_fragments(state)

    assert fragments == "", "S10a should strip fragments in Codex loop"


def test_codex_loop_peer_prompt_fragments_present_when_env_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS", raising=False)
    state = codex_loop.LoopState(
        settings=_settings_codex(),
        peer_manifest_cache=_populated_cache(),
    )

    fragments = codex_loop._peer_prompt_fragments(state)

    assert "codex-peer" in fragments
    assert "thread_fork" in fragments


def test_gemini_loop_peer_prompt_fragments_empty_when_s10a_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS", "1")
    state = gemini_loop.GeminiLoopState(
        settings=_settings_gemini(),
        peer_manifest_cache=_populated_cache(),
    )

    fragments = gemini_loop._peer_prompt_fragments(state)

    assert fragments == "", "S10a should strip fragments in Gemini loop"


def test_kimi_loop_peer_prompt_fragments_empty_when_s10a_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS", "1")
    state = kimi_loop.KimiLoopState(
        settings=_settings_kimi(),
        peer_manifest_cache=_populated_cache(),
    )

    fragments = kimi_loop._peer_prompt_fragments(state)

    assert fragments == "", "S10a should strip fragments in Kimi loop"
