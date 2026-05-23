"""Tests for the Codex App Server -> Ink viewer FIFO tee (`_EventFifoTee`).

The cardinal invariant under test: a missing or slow reader must never block
or raise into the adapter's notification hot loop. The tee is a best-effort
live mirror, so dropped events are acceptable; a wedged adapter is not.
"""

from __future__ import annotations

import json
import os
import threading

from claude_anyteam.codex import _EventFifoTee


def test_disabled_when_path_is_none(tmp_path):
    tee = _EventFifoTee(None)
    assert tee.enabled is False
    # All operations are inert no-ops; none raise.
    tee.ensure_fifo()
    tee.write_notification({"method": "turn/started"})
    tee.close()


def test_write_without_reader_is_silent_noop(tmp_path):
    """No reader attached: open hits ENXIO, writes are dropped, nothing raises."""
    fifo_path = str(tmp_path / "viewer.fifo")
    tee = _EventFifoTee(fifo_path)
    tee.ensure_fifo()
    assert os.path.exists(fifo_path)
    # Repeated writes with no reader must not block or raise.
    for _ in range(5):
        tee.write_notification({"method": "turn/started", "params": {}})
    # The node we created is unlinked on close.
    tee.close()
    assert not os.path.exists(fifo_path)


def test_notification_reaches_attached_reader(tmp_path):
    """With a reader attached, the raw notification arrives as one JSON line."""
    fifo_path = str(tmp_path / "viewer.fifo")
    tee = _EventFifoTee(fifo_path)
    tee.ensure_fifo()

    received: list[str] = []

    def reader() -> None:
        # Blocking open for read; returns once the writer opens for write.
        with open(fifo_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    received.append(line)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    # Give the reader a moment to block-open the FIFO so our nonblocking
    # writer open succeeds rather than hitting ENXIO.
    notif = {"method": "item/agentMessage/delta", "params": {"delta": "hi"}}
    deadline = threading.Event()
    # Retry the write a few times: the reader's open may not have landed yet.
    for _ in range(50):
        tee.write_notification(notif)
        if received:
            break
        deadline.wait(0.05)

    tee.close()
    reader_thread.join(timeout=2.0)

    assert received, "expected at least one notification to reach the reader"
    # The verbatim App Server shape round-trips through the pipe.
    parsed = json.loads(received[0])
    assert parsed["method"] == "item/agentMessage/delta"
    assert parsed["params"]["delta"] == "hi"


def test_preexisting_node_is_not_unlinked_on_close(tmp_path):
    """A FIFO node the tee did not create (e.g. shim-made) survives close()."""
    fifo_path = str(tmp_path / "shim-made.fifo")
    os.mkfifo(fifo_path)

    tee = _EventFifoTee(fifo_path)
    tee.ensure_fifo()  # node already exists -> tee does not claim ownership
    tee.write_notification({"method": "turn/completed"})
    tee.close()

    # We must not unlink a node we did not create.
    assert os.path.exists(fifo_path)
    os.unlink(fifo_path)


def test_mkfifo_failure_disables_tee_without_raising(tmp_path):
    """If the FIFO node can't be created, the tee disables itself silently."""
    # Point at a path whose parent does not exist -> os.mkfifo raises OSError,
    # which ensure_fifo() must swallow and then disable the tee.
    fifo_path = str(tmp_path / "missing-dir" / "viewer.fifo")
    tee = _EventFifoTee(fifo_path)
    tee.ensure_fifo()
    assert tee.enabled is False
    tee.write_notification({"method": "turn/started"})
    tee.close()
