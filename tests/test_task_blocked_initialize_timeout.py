"""Regression test for #40 Phase 1 — typed ``task_blocked.reason`` for
the App Server ``initialize`` timeout path.

Before this fix the wrapper used the raw error string as the
``task_blocked.reason``: lead-side filters had to grep substrings like
``"did not respond to initialize"`` to recognize the same failure mode
across runs. After this fix, initialize timeouts surface a stable
machine-readable token (``"app_server_initialize_timeout"``) so the
lead's task view + automation can branch on it directly.

This is the §2 visibility-parity win product-steward asked for in their
#40 Phase 1 brief: "Replace prose_reply timeout fallback with typed
lifecycle payload — Land as task_blocked with structured reason:
'app_server_initialize_timeout' + incident_id."
"""

from __future__ import annotations

from types import SimpleNamespace

from claude_anyteam.loop import _classify_task_block_reason


def test_initialize_timeout_classified_to_stable_token() -> None:
    """The App Server initialize-handshake timeout error string the
    underlying ``JsonRpcStdioClient`` raises must collapse to the stable
    ``app_server_initialize_timeout`` token.
    """

    result = SimpleNamespace(
        error=(
            "app_server error: JSON-RPC stdio process did not respond to "
            "initialize within 90.0s"
        ),
        exit_code=1,
    )
    assert _classify_task_block_reason(result) == "app_server_initialize_timeout"


def test_initialize_timeout_token_case_insensitive() -> None:
    """Pin: case-insensitive substring match. If the error string
    upstream changes capitalization (e.g., 'Did Not Respond'), we still
    classify correctly. Substring match is the only stable handle we
    have on the upstream error wire shape today.
    """

    result = SimpleNamespace(
        error="JSON-RPC stdio process Did Not Respond to initialize within 60.0s",
        exit_code=1,
    )
    assert _classify_task_block_reason(result) == "app_server_initialize_timeout"


def test_other_failures_preserve_raw_error_for_now() -> None:
    """Pin: failures we haven't classified yet propagate their raw error
    string verbatim — preserves prior behavior so this PR doesn't widen
    the scope beyond initialize-timeout. The classifier vocabulary can
    grow incrementally as new high-cost failure modes surface.
    """

    result = SimpleNamespace(
        error="codex exec --output-schema produced non-JSON output",
        exit_code=2,
    )
    assert _classify_task_block_reason(result) == (
        "codex exec --output-schema produced non-JSON output"
    )


def test_no_error_falls_back_to_exit_code_summary() -> None:
    """Pin: when there is no error string at all, fall back to a
    generic exit-code summary (also matches prior behavior). Prevents
    sending an empty ``reason`` string that downstream task-state
    rendering would mis-render as "blocked: ".
    """

    result = SimpleNamespace(error="", exit_code=42)
    assert _classify_task_block_reason(result) == (
        "codex exited 42 with no structured result"
    )


def test_initialize_timeout_during_shutdown_uses_distinct_token() -> None:
    """Pin: when initialize times out while the adapter is honoring an
    in-flight shutdown_request, the ``reason`` is the distinct
    ``app_server_shutdown_timeout`` token. Per product-steward's #40
    Phase 1 brief: lets the lead surface a no-op shutdown burning the
    budget separately from a work-turn timeout in the same
    discriminator.
    """

    result = SimpleNamespace(
        error=(
            "app_server error: JSON-RPC stdio process did not respond to "
            "initialize within 90.0s"
        ),
        exit_code=1,
    )
    assert _classify_task_block_reason(result, shutdown_requested=True) == (
        "app_server_shutdown_timeout"
    )
    # And without the shutdown context, it remains the regular token.
    assert _classify_task_block_reason(result, shutdown_requested=False) == (
        "app_server_initialize_timeout"
    )


def test_both_initialize_tokens_are_in_known_registry() -> None:
    """Pin: both new typed tokens emitted by the codex backend MUST be
    in ``KNOWN_TASK_BLOCKED_REASONS``. Otherwise the wrapper-MCP
    drift-warn validator would emit a ``visibility_degraded`` event for
    every legitimate task_blocked emission — defeating the §2 invariant.
    """

    from claude_anyteam.messages import KNOWN_TASK_BLOCKED_REASONS

    assert "app_server_initialize_timeout" in KNOWN_TASK_BLOCKED_REASONS
    assert "app_server_shutdown_timeout" in KNOWN_TASK_BLOCKED_REASONS
