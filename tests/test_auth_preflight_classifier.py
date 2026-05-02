"""Regression tests for src/claude_anyteam/auth_preflight.py classify_auth_error.

Caught during the kimi-pair S8 W7 v2 investigation (2026-04-29): the classifier
matched bare substring "429" against any text containing those three digits,
which mis-tagged a real 401 invalid_authentication_error as quota_exhausted
because the diagnostic blob carried the run-id "20260429T0040Z...". Fix uses
HTTP-status regex with digit-boundary anchors so 429 inside a longer numeric
run does not match.
"""

from __future__ import annotations

from claude_anyteam.auth_preflight import classify_auth_error


def test_real_429_classifies_as_quota_exhausted() -> None:
    text = "Error code: 429 - {'error': {'message': 'rate limit exceeded'}}"
    result = classify_auth_error(text)
    assert result is not None
    assert result[0] == "quota_exhausted"


def test_real_401_classifies_as_invalid_authentication() -> None:
    text = (
        "Error code: 401 - {'error': "
        "{'message': 'The API Key appears to be invalid or may have expired.'}}"
    )
    result = classify_auth_error(text)
    assert result is not None
    assert result[0] == "invalid_authentication"


def test_run_id_with_429_substring_does_not_classify_as_quota() -> None:
    """Regression for kimi-pair S8 W7 v2 investigation: run-id 20260429... must
    not be classified as quota_exhausted just because it contains '429'."""
    text = (
        "kimi auth preflight failed in run "
        "S8-W7-20260429T0040Z-postfix-verify-v2: backend exited 1"
    )
    assert classify_auth_error(text) is None


def test_run_id_with_429_plus_real_401_classifies_as_invalid_auth() -> None:
    """The actual S8 W7 v2 failure shape: invalid_authentication 401 alongside
    a run-id containing the digits 429."""
    text = (
        "Diagnostic for S8-W7-20260429T0040Z-postfix-verify-v2:\n"
        "Error code: 401 - {'error': {'type': 'invalid_authentication_error'}}"
    )
    result = classify_auth_error(text)
    assert result is not None
    assert result[0] == "invalid_authentication"


def test_text_with_no_recognizable_signal_returns_none() -> None:
    assert classify_auth_error("backend crashed for unknown reason") is None


def test_quota_marker_takes_precedence_over_clean_text() -> None:
    text = "rate_limit_exceeded: please retry after 60 seconds"
    result = classify_auth_error(text)
    assert result is not None
    assert result[0] == "quota_exhausted"


def test_unrelated_long_digit_runs_do_not_match_401() -> None:
    """Mirror of the 429 regression: a path or hash containing 401 inside a
    longer digit run must not flip an unrelated diagnostic to invalid_auth."""
    text = "process pid 1401234 exited cleanly"
    assert classify_auth_error(text) is None


def test_word_boundary_429_with_surrounding_digits_does_not_match() -> None:
    text = "request id req-99429 succeeded"
    assert classify_auth_error(text) is None
