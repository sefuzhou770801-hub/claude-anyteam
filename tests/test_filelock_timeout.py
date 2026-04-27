from __future__ import annotations

from pathlib import Path

from claude_teams import _filelock


def test_file_lock_defaults_to_30s_timeout(monkeypatch, tmp_path: Path):
    captured: list[float] = []

    class FakeFileLock:
        def __init__(self, path: str, timeout: float):
            self.path = path
            captured.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.delenv(_filelock.FILE_LOCK_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(_filelock.LEGACY_FILE_LOCK_TIMEOUT_ENV, raising=False)
    monkeypatch.setattr(_filelock, "FileLock", FakeFileLock)

    with _filelock.file_lock(tmp_path / ".lock"):
        pass

    assert captured == [30.0]


def test_file_lock_timeout_env_override(monkeypatch, tmp_path: Path):
    captured: list[float] = []

    class FakeFileLock:
        def __init__(self, path: str, timeout: float):
            captured.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setenv(_filelock.FILE_LOCK_TIMEOUT_ENV, "2.5")
    monkeypatch.setattr(_filelock, "FileLock", FakeFileLock)

    with _filelock.file_lock(tmp_path / ".lock"):
        pass

    assert captured == [2.5]
