#!/usr/bin/env python3
"""Score Phase-3 throughput/error metrics from visibility event logs.

This scorer is intentionally read-only over the B9/07 §7 VisibilityEvent
substrate.  It uses ``claude_anyteam.protocol_io.read_events`` as the canonical
reader when possible and falls back to a line-tolerant reader only when the
canonical helper encounters a malformed JSONL line (the brief requires malformed
lines to be logged and skipped without changing protocol_io itself).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import protocol_io
from claude_anyteam.messages import VisibilityEvent

MIN_RUNTIME_SECONDS = 3.0
KNOWN_TOOL_CATEGORIES = ("host_tool", "mcp_tool", "team_tool", "shadow_tool")


@dataclass(frozen=True)
class EventSource:
    """Resolved visibility-event source."""

    events_dir: Path
    team: str | None = None


@dataclass
class AgentComputation:
    """Computed scorecard plus raw components needed for aggregation."""

    scorecard: dict[str, Any]
    m1: float | None
    m2_total: int
    m5_failed: int
    m5_completed: int
    idle_samples: list[float]
    m10_warnings: int
    m11b_samples_by_backend: dict[str, list[float]]


def _eprint(stderr: TextIO, message: str) -> None:
    print(message, file=stderr)


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting the VisibilityEvent ``Z`` form."""

    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@contextmanager
def _temporary_teams_dir(base: Path):
    """Temporarily point claude_teams messaging at ``base`` for read_events()."""

    old = cs_messaging.TEAMS_DIR
    cs_messaging.TEAMS_DIR = base
    try:
        yield
    finally:
        cs_messaging.TEAMS_DIR = old


def resolve_source(*, team: str | None, events_dir: str | None) -> EventSource:
    if bool(team) == bool(events_dir):
        raise ValueError("provide exactly one of --team or --events-dir")
    if team:
        return EventSource(events_dir=cs_messaging.TEAMS_DIR / team / "events", team=team)
    return EventSource(events_dir=Path(events_dir).expanduser().resolve(), team=None)  # type: ignore[arg-type]


def discover_agents(events_dir: Path) -> list[str]:
    return sorted(path.stem for path in events_dir.glob("*.jsonl") if path.is_file())


def _read_events_tolerant(path: Path, stderr: TextIO) -> list[VisibilityEvent]:
    """Line-tolerant fallback for malformed JSONL edge cases.

    The canonical helper currently validates the whole file and raises on a bad
    line.  The scorer's brief requires malformed lines to be skipped, so this
    fallback mirrors the helper's validation line-by-line only after the helper
    has failed.
    """

    events: list[VisibilityEvent] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            events.append(VisibilityEvent.model_validate_json(line))
        except Exception as exc:  # noqa: BLE001 - diagnostics-only tolerant reader
            _eprint(stderr, f"warning: malformed JSONL skipped: {path}:{lineno}: {exc}")
    return events


def read_agent_events(source: EventSource, agent: str, stderr: TextIO) -> list[VisibilityEvent]:
    """Read one agent's events via protocol_io.read_events where possible."""

    path = source.events_dir / f"{agent}.jsonl"
    if source.team is not None:
        try:
            return protocol_io.read_events(source.team, agent)
        except Exception:
            return _read_events_tolerant(path, stderr)

    # Explicit --events-dir is normally an archived .../<run>/events directory.
    # Re-root TEAMS_DIR around that shape so protocol_io.read_events remains the
    # primary reader instead of hand-parsing JSONL.
    if source.events_dir.name == "events":
        pseudo_team = source.events_dir.parent.name
        pseudo_base = source.events_dir.parent.parent
        try:
            with _temporary_teams_dir(pseudo_base):
                return protocol_io.read_events(pseudo_team, agent)
        except Exception:
            return _read_events_tolerant(path, stderr)

    _eprint(
        stderr,
        "warning: --events-dir does not end in 'events'; using tolerant VisibilityEvent reader",
    )
    return _read_events_tolerant(path, stderr)


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _p95(values: list[float]) -> float | None:
    if len(values) < 5:
        return None
    return statistics.quantiles(values, n=20)[18]


def _counter_to_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _record_digest_tool_counts(
    *,
    event: VisibilityEvent,
    raw_counts: Counter[str],
    category_counts: Counter[str],
    stderr: TextIO,
) -> int:
    """Record terminal-only digest tool counts when partial events are absent."""

    payload = event.payload or {}
    if payload.get("partial_events_available") is not False:
        return 0

    # Future-proof for richer digest counters, while supporting today's
    # headless payload's scalar tool_call_events count.
    raw_digest = (
        payload.get("tool_event_count_by_raw_backend_type")
        or payload.get("tool_event_counts_by_raw_backend_type")
        or payload.get("tool_event_counts")
    )
    category_digest = (
        payload.get("tool_event_count_by_category")
        or payload.get("tool_event_counts_by_category")
    )

    total = 0
    if isinstance(raw_digest, dict):
        for key, count_value in raw_digest.items():
            count = _as_int(count_value)
            if count <= 0:
                continue
            raw_counts[str(key)] += count
            total += count
    else:
        total = max(0, _as_int(payload.get("tool_call_events")))
        if total:
            key = f"_unknown_raw:{event.event_id}"
            raw_counts[key] += total
            _eprint(
                stderr,
                f"warning: terminal digest lacks raw_backend_type detail for {event.event_id}; "
                f"bucketed {total} as {key}",
            )

    if isinstance(category_digest, dict):
        for key, count_value in category_digest.items():
            count = _as_int(count_value)
            if count > 0:
                category_counts[str(key)] += count
    elif total:
        category_counts["_unknown_category"] += total
        _eprint(
            stderr,
            f"warning: terminal digest lacks category detail for {event.event_id}; "
            f"bucketed {total} as _unknown_category",
        )
    return total


def _compute_idle_samples(events_with_ts: list[tuple[VisibilityEvent, datetime]], stderr: TextIO) -> list[float]:
    samples: list[float] = []
    last_completed_at: datetime | None = None
    for event, ts in events_with_ts:
        if event.kind == "turn_started":
            if last_completed_at is not None:
                if ts > last_completed_at:
                    samples.append((ts - last_completed_at).total_seconds())
                else:
                    _eprint(
                        stderr,
                        f"warning: turn_started {event.event_id} is not after previous turn_completed; "
                        "idle pair skipped",
                    )
                last_completed_at = None
        elif event.kind == "turn_completed":
            if last_completed_at is not None:
                _eprint(
                    stderr,
                    f"warning: consecutive turn_completed without turn_started before {event.event_id}; "
                    "idle pair skipped",
                )
            last_completed_at = ts
    return samples


def _turn_key(event: VisibilityEvent) -> str:
    return event.turn_id or "_implicit_current_turn"


def _compute_turn_duration_samples(
    events_with_ts: list[tuple[VisibilityEvent, datetime]],
    stderr: TextIO,
) -> dict[str, list[float]]:
    """Pair turn_started with turn_completed/turn_failed and group durations by backend."""

    starts_by_turn: dict[str, tuple[datetime, str]] = {}
    current_start: tuple[str, datetime, str] | None = None
    samples: dict[str, list[float]] = {}
    for event, ts in events_with_ts:
        if event.kind == "turn_started":
            backend = event.backend or "_unknown_backend"
            key = _turn_key(event)
            starts_by_turn[key] = (ts, backend)
            current_start = (key, ts, backend)
            continue
        if event.kind not in {"turn_completed", "turn_failed"}:
            continue
        key = _turn_key(event)
        start = starts_by_turn.pop(key, None)
        if start is None and current_start is not None:
            current_key, current_ts, current_backend = current_start
            start = (current_ts, current_backend)
            starts_by_turn.pop(current_key, None)
        if start is None:
            continue
        start_ts, start_backend = start
        if ts < start_ts:
            _eprint(
                stderr,
                f"warning: terminal turn event {event.event_id} precedes turn_started; duration skipped",
            )
            continue
        backend = event.backend or start_backend or "_unknown_backend"
        samples.setdefault(backend, []).append((ts - start_ts).total_seconds())
        if current_start is not None and current_start[0] == key:
            current_start = None
    return samples


def percentile_triplet(samples: Iterable[float]) -> dict[str, Any]:
    values = sorted(float(v) for v in samples)
    enough = len(values) >= 5
    quantiles = statistics.quantiles(values, n=20) if enough else []
    return {
        "p50": quantiles[9] if enough else None,
        "p95": quantiles[18] if enough else None,
        "max": max(values) if values else None,
        "samples": len(values),
    }


def compute_agent_score(
    *,
    agent: str,
    events: list[VisibilityEvent],
    scenario: str,
    run_id: str,
    stderr: TextIO = sys.stderr,
) -> AgentComputation:
    """Compute one per-agent scorecard and aggregation components."""

    if not events:
        return AgentComputation(
            scorecard={
                "schema_version": 1,
                "agent": agent,
                "backend": None,
                "scenario": scenario,
                "run_id": run_id,
                "wall_clock_seconds": 0.0,
                "metrics": None,
                "notes": ["empty_log"],
            },
            m1=None,
            m2_total=0,
            m5_failed=0,
            m5_completed=0,
            idle_samples=[],
            m10_warnings=0,
            m11b_samples_by_backend={},
        )

    notes: list[str] = []
    backend = events[0].backend

    events_with_ts: list[tuple[VisibilityEvent, datetime]] = []
    previous_ts: datetime | None = None
    clock_regress = False
    for event in events:
        ts = _parse_timestamp(event.timestamp)
        if previous_ts is not None and ts < previous_ts:
            clock_regress = True
        previous_ts = ts
        events_with_ts.append((event, ts))
    if clock_regress:
        notes.append("clock_regress_observed")

    timestamps = [ts for _, ts in events_with_ts]
    wall_clock_seconds = (max(timestamps) - min(timestamps)).total_seconds()
    wall_clock_minutes = wall_clock_seconds / 60.0

    completed_count = sum(1 for event in events if event.kind == "turn_completed")
    failed_count = sum(1 for event in events if event.kind == "turn_failed")
    warning_count = sum(1 for event in events if event.kind == "turn_warning")

    if wall_clock_seconds < MIN_RUNTIME_SECONDS:
        m1: float | None = None
        notes.append("insufficient_runtime")
    else:
        m1 = completed_count / wall_clock_minutes

    m5_denominator = completed_count + failed_count
    if m5_denominator == 0:
        m5_rate: float | None = None
        notes.append("no_turns_observed")
    else:
        m5_rate = failed_count / m5_denominator

    raw_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter({category: 0 for category in KNOWN_TOOL_CATEGORIES})
    m2_notes: list[str] = []
    m2_total = 0

    for event in events:
        payload = event.payload or {}
        if event.kind == "tool_event":
            raw_backend_type = payload.get("raw_backend_type")
            if raw_backend_type:
                raw_key = str(raw_backend_type)
            elif payload.get("tool_name"):
                raw_key = f"_unknown_raw:{payload['tool_name']}"
                _eprint(
                    stderr,
                    f"warning: tool_event {event.event_id} missing raw_backend_type; "
                    f"bucketed as {raw_key}",
                )
            else:
                raw_key = f"_unknown_raw:{event.event_id}"
                _eprint(
                    stderr,
                    f"warning: tool_event {event.event_id} missing raw_backend_type and tool_name; "
                    f"bucketed as {raw_key}",
                )

            category = payload.get("category")
            if not category:
                category = "_unknown_category"
                _eprint(
                    stderr,
                    f"warning: tool_event {event.event_id} missing payload.category; "
                    "bucketed as _unknown_category",
                )

            raw_counts[raw_key] += 1
            category_counts[str(category)] += 1
            m2_total += 1
        elif payload.get("partial_events_available") is False:
            if "partial_events_only" not in m2_notes:
                m2_notes.append("partial_events_only")
            m2_total += _record_digest_tool_counts(
                event=event,
                raw_counts=raw_counts,
                category_counts=category_counts,
                stderr=stderr,
            )

    m2_score: dict[str, Any] = {
        "by_raw_backend_type": _counter_to_dict(raw_counts),
        "by_category": _counter_to_dict(category_counts),
        "total": m2_total,
    }
    if m2_notes:
        m2_score["notes"] = m2_notes

    idle_samples = _compute_idle_samples(events_with_ts, stderr)
    m8_notes: list[str] = []
    p95 = _p95(idle_samples)
    if p95 is None:
        m8_notes.append("p95_undersampled")
    m8_score: dict[str, Any] = {
        "mean": _mean(idle_samples),
        "median": statistics.median(idle_samples) if idle_samples else None,
        "p95": p95,
        "samples": len(idle_samples),
    }
    if m8_notes:
        m8_score["notes"] = m8_notes

    m11b_samples_by_backend = _compute_turn_duration_samples(events_with_ts, stderr)
    m11b_score = {
        backend_name: percentile_triplet(samples)
        for backend_name, samples in sorted(m11b_samples_by_backend.items())
    }
    for backend_name, samples in sorted(m11b_samples_by_backend.items()):
        if len(samples) < 5:
            notes.append(f"m11b_undersampled:{backend_name}")

    scorecard: dict[str, Any] = {
        "schema_version": 1,
        "agent": agent,
        "backend": backend,
        "scenario": scenario,
        "run_id": run_id,
        "wall_clock_seconds": wall_clock_seconds,
        "metrics": {
            "M1_throughput_per_min": m1,
            "M2_tool_event_count": m2_score,
            "M5_turn_failed_rate": m5_rate,
            "M5_turn_failed_count": failed_count,
            "M5_turn_completed_count": completed_count,
            "M8_idle_time_seconds": m8_score,
            "M10_turn_warning_count": warning_count,
            "M11b_turn_duration_seconds_by_backend": m11b_score,
        },
    }
    if notes:
        scorecard["notes"] = notes

    return AgentComputation(
        scorecard=scorecard,
        m1=m1,
        m2_total=m2_total,
        m5_failed=failed_count,
        m5_completed=completed_count,
        idle_samples=idle_samples,
        m10_warnings=warning_count,
        m11b_samples_by_backend=m11b_samples_by_backend,
    )


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"sum": None, "mean": None, "min": None, "max": None}
    return {
        "sum": sum(values),
        "mean": statistics.mean(values),
        "min": min(values),
        "max": max(values),
    }


def compute_scenario_score(
    *,
    computations: list[AgentComputation],
    scenario: str,
    run_id: str,
) -> dict[str, Any]:
    agents = [item.scorecard["agent"] for item in computations]
    m1_values = [item.m1 for item in computations if item.m1 is not None]
    failures = sum(item.m5_failed for item in computations)
    turns = sum(item.m5_failed + item.m5_completed for item in computations)
    all_idle_samples = [sample for item in computations for sample in item.idle_samples]
    m11b_samples_by_backend: dict[str, list[float]] = {}
    for item in computations:
        for backend_name, samples in item.m11b_samples_by_backend.items():
            m11b_samples_by_backend.setdefault(backend_name, []).extend(samples)
    m11b_by_backend = {
        backend_name: percentile_triplet(samples)
        for backend_name, samples in sorted(m11b_samples_by_backend.items())
    }
    m11b_p95_values = [
        summary["p95"]
        for summary in m11b_by_backend.values()
        if summary.get("p95") is not None
    ]

    return {
        "schema_version": 1,
        "scenario": scenario,
        "run_id": run_id,
        "agents": agents,
        "aggregate": {
            "M1_throughput_per_min": _summary(m1_values),
            "M2_total_tool_events": sum(item.m2_total for item in computations),
            "M5_turn_failed_rate": {
                "weighted_mean": (failures / turns) if turns else None,
            },
            "M8_idle_time_seconds": {"team_p95": _p95(all_idle_samples)},
            "M10_total_turn_warnings": sum(item.m10_warnings for item in computations),
            "M11b_turn_duration_seconds_by_backend": m11b_by_backend,
            "M11b_team_p95_turn_duration_seconds": max(m11b_p95_values) if m11b_p95_values else None,
        },
        "per_agent_files": [f"agents/{agent}.json" for agent in agents],
    }


def write_scorecards(
    *,
    computations: list[AgentComputation],
    scenario_score: dict[str, Any],
    out: Path,
) -> None:
    agents_dir = out / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for item in computations:
        agent = item.scorecard["agent"]
        (agents_dir / f"{agent}.json").write_text(
            json.dumps(item.scorecard, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (out / "scenario.json").write_text(
        json.dumps(scenario_score, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def score_source(
    *,
    source: EventSource,
    scenario: str,
    run_id: str,
    out: Path,
    stderr: TextIO = sys.stderr,
) -> int:
    if not source.events_dir.exists() or not source.events_dir.is_dir():
        _eprint(stderr, f"error: events dir missing: {source.events_dir}")
        return 1

    agents = discover_agents(source.events_dir)
    if not agents:
        _eprint(stderr, f"error: no agent event logs found in: {source.events_dir}")
        return 1

    try:
        computations = [
            compute_agent_score(
                agent=agent,
                events=read_agent_events(source, agent, stderr),
                scenario=scenario,
                run_id=run_id,
                stderr=stderr,
            )
            for agent in agents
        ]
        scenario_score = compute_scenario_score(
            computations=computations,
            scenario=scenario,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic boundary
        _eprint(stderr, f"error: failed to compute throughput score: {exc}")
        return 1

    try:
        write_scorecards(computations=computations, scenario_score=scenario_score, out=out)
    except Exception as exc:  # noqa: BLE001 - output-write boundary per brief
        _eprint(stderr, f"error: failed to write throughput score output: {exc}")
        return 2

    for item in computations:
        metrics = item.scorecard.get("metrics")
        if metrics is None:
            _eprint(stderr, f"agent {item.scorecard['agent']}: empty_log")
            continue
        _eprint(
            stderr,
            "agent {agent}: backend={backend} M1={m1} M2={m2} M5={failed}/{turns} M10={warnings}".format(
                agent=item.scorecard["agent"],
                backend=item.scorecard["backend"],
                m1=metrics["M1_throughput_per_min"],
                m2=metrics["M2_tool_event_count"]["total"],
                failed=metrics["M5_turn_failed_count"],
                turns=metrics["M5_turn_failed_count"] + metrics["M5_turn_completed_count"],
                warnings=metrics["M10_turn_warning_count"],
            ),
        )
    _eprint(stderr, f"wrote throughput scorecards to {out}")
    return 0


def _default_label(value: str | None, fallback: str) -> str:
    return value if value is not None else fallback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--team", help="Team name under claude_teams.messaging.TEAMS_DIR")
    source.add_argument("--events-dir", help="Explicit archived events directory")
    parser.add_argument("--scenario", help="Scenario label (for example S1)")
    parser.add_argument("--run-id", help="Run identifier (for example 20260427T1530Z)")
    parser.add_argument("--out", required=True, help="Output directory for scorecards")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        source = resolve_source(team=args.team, events_dir=args.events_dir)
    except ValueError as exc:
        _eprint(sys.stderr, f"error: {exc}")
        return 1
    return score_source(
        source=source,
        scenario=_default_label(args.scenario, "unknown"),
        run_id=_default_label(args.run_id, "unknown"),
        out=Path(args.out).expanduser().resolve(),
        stderr=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
