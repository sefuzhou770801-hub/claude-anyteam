#!/usr/bin/env python3
"""Micro-benchmark the AnyTeam substrate hot paths.

The benchmark is intentionally synthetic: it exercises the exact Python
helpers used by the manifest cache, peer-steer authorization, R14 prompt
fragment composition, and Codex prompt builders without spawning teammates or
touching the team mailbox.  This keeps the measured units in the µs/call range
and separates substrate cost from backend turn latency.

Run from the repository root:

    python tools/benchmark/substrate_hot_path_profile.py

The default fixture is an S8-like roster: one requester plus seven peers with
mixed Codex App Server, Gemini ACP, and Kimi headless capability manifests.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claude_anyteam import prompts as codex_prompts  # noqa: E402
from claude_anyteam.capabilities import (  # noqa: E402
    CODEX_APP_SERVER_CAPABILITIES,
    GEMINI_ACP_CAPABILITIES,
    KIMI_HEADLESS_CAPABILITIES,
    build_agent_card,
    manifest_accepts_peer_steer,
    rich_capability_manifest,
)
from claude_anyteam.capability_manifest import CapabilityManifestCache  # noqa: E402


def _flag_disabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _flag_enabled(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _enforce_peer_steer_manifest_check() -> bool:
    """Mirror wrapper_server._enforce_peer_steer_manifest_check."""

    if _flag_disabled(os.environ.get("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK")):
        return False
    return _flag_enabled(
        os.environ.get("CLAUDE_ANYTEAM_ENFORCE_PEER_STEER_MANIFEST_CHECK"),
        default=True,
    )


DEFAULT_SAMPLES = 401


@dataclass(frozen=True)
class BenchCase:
    name: str
    func: Callable[[], Any]
    iterations: int
    description: str


@dataclass
class BenchResult:
    name: str
    iterations: int
    samples: int
    median_us: float
    p95_us: float
    min_us: float
    max_us: float
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SyntheticConfig:
    def __init__(self, member_names: list[str]) -> None:
        self.members = [SimpleNamespace(name=name) for name in member_names]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty values")
    ordered = sorted(values)
    rank = math.ceil((pct / 100.0) * len(ordered)) - 1
    return ordered[min(max(rank, 0), len(ordered) - 1)]


def _measure(case: BenchCase, *, samples: int) -> BenchResult:
    per_call_us: list[float] = []
    sink: Any = None

    # Warm the interpreter and any lazy branches outside the timed region.
    for _ in range(min(25, case.iterations)):
        sink = case.func()

    for _sample in range(samples):
        started = time.perf_counter_ns()
        for _ in range(case.iterations):
            sink = case.func()
        elapsed_ns = time.perf_counter_ns() - started
        per_call_us.append(elapsed_ns / case.iterations / 1_000.0)

    # Keep the last result reachable so an optimizing interpreter cannot prove
    # the loop has no externally visible value.  CPython does not currently
    # eliminate these calls, but the assignment makes the intent explicit.
    globals()["_BENCH_SINK"] = sink

    return BenchResult(
        name=case.name,
        iterations=case.iterations,
        samples=samples,
        median_us=statistics.median(per_call_us),
        p95_us=_percentile(per_call_us, 95),
        min_us=min(per_call_us),
        max_us=max(per_call_us),
        description=case.description,
    )


def _card(
    *,
    team_name: str,
    agent_name: str,
    agent_type: str,
    model: str,
    backend_type: str,
    capabilities: list[str],
    steer_authorization: str = "lead_only",
    delivery_mode: str | None = None,
    expiry_semantics: str | None = None,
    host_tool_surface: str | None = None,
    coupling_regime: str = "tight",
) -> dict[str, Any]:
    manifest = rich_capability_manifest(
        capabilities,
        delivery_mode=delivery_mode,
        expiry_semantics=expiry_semantics,
        steer_authorization=steer_authorization,
        host_tool_surface=host_tool_surface,
    )
    return build_agent_card(
        team_name=team_name,
        agent_name=agent_name,
        agent_id=f"id-{agent_name}",
        agent_type=agent_type,
        model=model,
        backend_type=backend_type,
        capabilities=capabilities,
        capability_manifest=manifest,
        transport=backend_type,
        host_tool_surface=host_tool_surface,
        coupling_regime=coupling_regime,
    )


def _build_s8_like_cache() -> CapabilityManifestCache:
    """Build one requester plus seven peer manifests in memory."""

    team_name = "substrate-hot-path-bench"
    cards: dict[str, dict[str, Any]] = {}
    cards["codex-profiler"] = _card(
        team_name=team_name,
        agent_name="codex-profiler",
        agent_type="codex",
        model="gpt-5.5",
        backend_type="codex_app_server",
        capabilities=CODEX_APP_SERVER_CAPABILITIES,
        delivery_mode="mid_turn",
        expiry_semantics="expires_after_turns",
        host_tool_surface="wrapper_mcp",
        coupling_regime="tight",
    )

    peer_specs = [
        ("codex-ablation", "codex", "gpt-5.5", "codex_app_server", CODEX_APP_SERVER_CAPABILITIES, "lead_only", "mid_turn", "expires_after_turns", "wrapper_mcp", "tight"),
        ("codex-iso-r14", "codex", "gpt-5.5", "codex_app_server", CODEX_APP_SERVER_CAPABILITIES, "lead_only", "mid_turn", "expires_after_turns", "wrapper_mcp", "tight"),
        ("gemini-peer-a", "gemini", "gemini-2.5-pro", "gemini_acp", GEMINI_ACP_CAPABILITIES, "any_peer", "next_turn_boundary", "expires_after_turns", "mcp_anyteam", "tight"),
        ("gemini-peer-b", "gemini", "gemini-2.5-pro", "gemini_acp", GEMINI_ACP_CAPABILITIES, "any_peer", "next_turn_boundary", "expires_after_turns", "mcp_anyteam", "tight"),
        ("kimi-peer-a", "kimi", "kimi-k2", "kimi_headless", KIMI_HEADLESS_CAPABILITIES, "lead_only", None, None, "wrapper_mcp", "loose"),
        ("kimi-peer-b", "kimi", "kimi-k2", "kimi_headless", KIMI_HEADLESS_CAPABILITIES, "lead_only", None, None, "wrapper_mcp", "loose"),
        ("team-lead", "claude", "claude-opus", "claude_native", GEMINI_ACP_CAPABILITIES, "any_peer", "native", "live_turn", "claude_native", "tight"),
    ]
    for (
        name,
        agent_type,
        model,
        backend_type,
        capabilities,
        steer_authorization,
        delivery_mode,
        expiry_semantics,
        host_tool_surface,
        coupling_regime,
    ) in peer_specs:
        cards[name] = _card(
            team_name=team_name,
            agent_name=name,
            agent_type=agent_type,
            model=model,
            backend_type=backend_type,
            capabilities=list(capabilities),
            steer_authorization=steer_authorization,
            delivery_mode=delivery_mode,
            expiry_semantics=expiry_semantics,
            host_tool_surface=host_tool_surface,
            coupling_regime=coupling_regime,
        )

    cache = CapabilityManifestCache(team=team_name, self_name="codex-profiler")
    cache.manifests = cards
    return cache


def _steer_recipient_refusal_reasons(
    *,
    to: str,
    cfg: SyntheticConfig,
    cache: CapabilityManifestCache,
    self_name: str,
) -> dict[str, str]:
    """Mirror wrapper_server.build_server::_steer_recipient_refusal_reasons.

    The production helper is a closure inside build_server because it captures
    the wrapper process's identity and manifest cache.  The logic below is a
    line-for-line synthetic equivalent for the manifest-gate portion of
    send_message(kind="steer"), excluding config-file reads and mailbox writes.
    """

    if self_name == "team-lead" or to == "team-lead":
        return {}
    if to == "*":
        recipients: list[str] = []
        for member in getattr(cfg, "members", []):
            target = getattr(member, "name", None)
            if target and target not in {self_name, "team-lead"}:
                recipients.append(target)
    else:
        recipients = [to]

    refusing: dict[str, str] = {}
    for recipient in recipients:
        manifest = cache.get(recipient)
        if manifest is None:
            refusing[recipient] = "manifest_not_queried"
            continue
        if not manifest_accepts_peer_steer(manifest):
            refusing[recipient] = "manifest_denies_peer_steer"
    return refusing


def _task_fixture() -> SimpleNamespace:
    return SimpleNamespace(
        id="60",
        subject="micro-benchmark suspected substrate hot paths",
        description=(
            "Measure CapabilityManifestCache.get, wrapper peer-steer manifest "
            "authorization, R14 peer-prompt-fragment composition, and prompt "
            "construction with and without R14 fragments. Report median and "
            "p95 microseconds per call."
        ),
    )


def _build_cases(cache: CapabilityManifestCache) -> tuple[list[BenchCase], dict[str, Any]]:
    requester = "codex-profiler"
    cfg = SyntheticConfig(list(cache.manifests))
    task = _task_fixture()
    fragment = cache.peer_prompt_fragments_for(requester)

    def cache_get() -> dict[str, Any] | None:
        return cache.get("gemini-peer-a")

    def steer_gate_single_authorized() -> bool:
        refusals = _steer_recipient_refusal_reasons(
            to="gemini-peer-a",
            cfg=cfg,
            cache=cache,
            self_name=requester,
        )
        if _enforce_peer_steer_manifest_check() and refusals:
            return False
        return True

    def steer_gate_broadcast_mixed() -> dict[str, str]:
        refusals = _steer_recipient_refusal_reasons(
            to="*",
            cfg=cfg,
            cache=cache,
            self_name=requester,
        )
        if _enforce_peer_steer_manifest_check() and refusals:
            return refusals
        return {}

    def r14_fragment_composition() -> str:
        return cache.peer_prompt_fragments_for(requester)

    def prompt_without_fragments() -> str:
        return codex_prompts.v7_task_prompt(
            task,
            agent_name=requester,
            team_name=cache.team,
            peer_prompt_fragments="",
        )

    def prompt_with_precomputed_fragments() -> str:
        return codex_prompts.v7_task_prompt(
            task,
            agent_name=requester,
            team_name=cache.team,
            peer_prompt_fragments=fragment,
        )

    def prompt_with_fresh_fragment_composition() -> str:
        return codex_prompts.v7_task_prompt(
            task,
            agent_name=requester,
            team_name=cache.team,
            peer_prompt_fragments=cache.peer_prompt_fragments_for(requester),
        )

    cases = [
        BenchCase(
            name="cache_get_warm",
            func=cache_get,
            iterations=1000,
            description="CapabilityManifestCache.get(team, agent) equivalent on a warmed in-memory cache.",
        ),
        BenchCase(
            name="steer_gate_single_authorized",
            func=steer_gate_single_authorized,
            iterations=1000,
            description="Manifest-gated send_message(kind='steer') authorization for one peer that accepts peer steer.",
        ),
        BenchCase(
            name="steer_gate_broadcast_mixed_7_peers",
            func=steer_gate_broadcast_mixed,
            iterations=1000,
            description="Manifest-gated steer authorization for '*' fanout over seven peers, including denying peers.",
        ),
        BenchCase(
            name="r14_fragment_composition_s8",
            func=r14_fragment_composition,
            iterations=100,
            description="R14 peer-prompt-fragment aggregation over an S8-like manifest cache.",
        ),
        BenchCase(
            name="prompt_construct_without_r14",
            func=prompt_without_fragments,
            iterations=1000,
            description="Codex v7 task prompt construction with no peer fragments.",
        ),
        BenchCase(
            name="prompt_construct_with_precomputed_r14",
            func=prompt_with_precomputed_fragments,
            iterations=1000,
            description="Codex v7 task prompt construction with a precomputed R14 fragment string.",
        ),
        BenchCase(
            name="prompt_construct_with_fresh_r14",
            func=prompt_with_fresh_fragment_composition,
            iterations=100,
            description="End-to-end prompt construction when each call recomposes R14 fragments.",
        ),
    ]
    metadata = {
        "repo_head": _git(["rev-parse", "--short=12", "HEAD"]),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "samples": DEFAULT_SAMPLES,
        "roster_size": len(cache.manifests),
        "peer_count": len(cache.manifests) - 1,
        "fragment_bytes": len(fragment.encode("utf-8")),
        "fragment_chars": len(fragment),
        "prompt_without_r14_bytes": len(prompt_without_fragments().encode("utf-8")),
        "prompt_with_r14_bytes": len(prompt_with_precomputed_fragments().encode("utf-8")),
        "disable_peer_prompt_fragments_env": os.environ.get("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS"),
        "disable_manifest_cache_env": os.environ.get("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE"),
        "enforce_peer_steer_manifest_check": _enforce_peer_steer_manifest_check(),
    }
    return cases, metadata


def _git(args: list[str]) -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _as_markdown(results: list[BenchResult], metadata: dict[str, Any]) -> str:
    lines = [
        "# Substrate hot-path micro-benchmark",
        "",
        "Synthetic S8-like fixture: one requester plus seven peers with mixed Codex App Server, Gemini ACP, and Kimi headless Agent Cards.",
        "Measurements are isolated Python hot paths; they exclude teammate process startup, backend model latency, config-file reads, mailbox writes, and event-log fanout.",
        "",
        "## Environment",
        "",
    ]
    for key in sorted(metadata):
        lines.append(f"- `{key}`: `{metadata[key]}`")
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Component | Iterations/sample | Median µs/call | p95 µs/call | Notes |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        lines.append(
            "| {name} | {iterations} | {median:.3f} | {p95:.3f} | {description} |".format(
                name=result.name,
                iterations=result.iterations,
                median=result.median_us,
                p95=result.p95_us,
                description=result.description.replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = parser.parse_args(argv)

    if args.samples < 5:
        parser.error("--samples must be at least 5")

    cache = _build_s8_like_cache()
    cases, metadata = _build_cases(cache)
    metadata["samples"] = args.samples
    results = [_measure(case, samples=args.samples) for case in cases]

    if args.json:
        print(
            json.dumps(
                {
                    "metadata": metadata,
                    "results": [
                        {
                            "name": result.name,
                            "iterations": result.iterations,
                            "samples": result.samples,
                            "median_us": result.median_us,
                            "p95_us": result.p95_us,
                            "min_us": result.min_us,
                            "max_us": result.max_us,
                            "description": result.description,
                        }
                        for result in results
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_as_markdown(results, metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
