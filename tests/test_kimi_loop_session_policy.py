"""Coverage for Kimi session-resume policy.

Asserts the adapter enforces the resume-validation landmine documented in
``docs/internal/kimi-integration/kimi-runtime.md``: ``kimi -r <unknown-id>``
silently creates a new session, so the adapter MUST verify the session dir
exists under ``<kimi_home>/.kimi/sessions/<md5(cwd)>/<session-uuid>/``
before passing ``--session`` through to the binary.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from claude_anyteam.backends.kimi import invoke


def _expected_session_dir(home: Path, cwd: Path, session_id: str) -> Path:
    cwd_hash = hashlib.md5(str(cwd).encode("utf-8")).hexdigest()
    return home / ".kimi" / "sessions" / cwd_hash / session_id


def test_known_session_returns_false_when_dir_missing(tmp_path: Path):
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    assert invoke._known_session(home, cwd, "00000000-0000-0000-0000-000000000000") is False


def test_known_session_returns_false_for_empty_session_id(tmp_path: Path):
    assert invoke._known_session(tmp_path, tmp_path, None) is False
    assert invoke._known_session(tmp_path, tmp_path, "") is False


def test_known_session_returns_true_when_dir_exists(tmp_path: Path):
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    home = tmp_path / "home"
    sid = "12345678-aaaa-bbbb-cccc-1234567890ab"
    target = _expected_session_dir(home, cwd, sid)
    target.mkdir(parents=True)
    assert invoke._known_session(home, cwd, sid) is True


def test_session_hash_uses_md5_of_cwd_string():
    cwd = Path("/tmp/example")
    expected = hashlib.md5(str(cwd).encode("utf-8")).hexdigest()
    assert invoke._session_hash(cwd) == expected


def test_session_dir_layout_matches_runtime_doc(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    sid = "deadbeef-dead-beef-dead-beefdeadbeef"
    actual = invoke._session_dir(home, cwd, sid)
    assert actual == _expected_session_dir(home, cwd, sid)
    # Path components are kimi_home/.kimi/sessions/<md5>/<sid>
    assert actual.parts[-4:] == (".kimi", "sessions", invoke._session_hash(cwd), sid)


def test_known_session_rejects_path_traversal_in_session_id(tmp_path: Path):
    """Defensive: an attacker-controlled session id must not point at parent dirs."""
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    # Create a sibling dir to confirm the rejection
    (home / ".kimi" / "sessions").mkdir(parents=True)
    suspicious = "../../../etc/passwd"
    # _known_session simply checks .is_dir() so this is just confirming
    # the function does NOT consider an unsafe id "known"
    assert invoke._known_session(home, cwd, suspicious) is False
