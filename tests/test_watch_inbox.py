from __future__ import annotations

import threading
import time
from pathlib import Path

from claude_anyteam.watch_inbox import WatchInbox


def test_watch_inbox_wakes_quickly_on_target_file_change(tmp_path: Path):
    inbox_dir = tmp_path / "inboxes"
    inbox_dir.mkdir()
    inbox_file = inbox_dir / "agent-a.json"
    inbox_file.write_text("[]\n", encoding="utf-8")

    watcher = WatchInbox(inbox_file, fallback_timeout_s=1.0)
    try:
        # Give the daemon watch thread a short runway before measuring wake
        # latency. The measured interval below starts after this warmup.
        watcher.wait_for_change(0.05)

        def modify_inbox() -> None:
            time.sleep(0.03)
            inbox_file.write_text('[{"text": "hello"}]\n', encoding="utf-8")

        writer = threading.Thread(target=modify_inbox)
        start = time.perf_counter()
        writer.start()
        changed = watcher.wait_for_change(1.0)
        elapsed = time.perf_counter() - start
        writer.join(timeout=1.0)

        assert changed is True
        assert elapsed < 0.2
    finally:
        watcher.close()


def test_watch_inbox_filters_other_inbox_basenames(tmp_path: Path):
    inbox_dir = tmp_path / "inboxes"
    inbox_dir.mkdir()
    inbox_file = inbox_dir / "agent-a.json"
    other_file = inbox_dir / "agent-b.json"
    inbox_file.write_text("[]\n", encoding="utf-8")
    other_file.write_text("[]\n", encoding="utf-8")

    watcher = WatchInbox(inbox_file, fallback_timeout_s=1.0)
    try:
        watcher.wait_for_change(0.05)

        other_file.write_text('[{"text": "not for agent-a"}]\n', encoding="utf-8")

        assert watcher.wait_for_change(0.12) is False
    finally:
        watcher.close()


def test_watch_inbox_falls_back_to_timeout_when_watch_errors(tmp_path: Path):
    inbox_dir = tmp_path / "inboxes"
    inbox_dir.mkdir()
    inbox_file = inbox_dir / "agent-a.json"
    inbox_file.write_text("[]\n", encoding="utf-8")

    def broken_watch(*_args, **_kwargs):
        raise RuntimeError("watch unavailable")

    watcher = WatchInbox(inbox_file, fallback_timeout_s=0.05, watch_func=broken_watch)
    try:
        start = time.perf_counter()
        changed = watcher.wait_for_change(1.0)
        elapsed = time.perf_counter() - start

        assert changed is False
        assert elapsed >= 0.045
        assert elapsed < 0.25
        assert "watch unavailable" in (watcher.disabled_reason or "")
    finally:
        watcher.close()
