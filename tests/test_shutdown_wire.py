from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import protocol_io as pio
from claude_teams import messaging as cs_messaging


def _lead_inbox(tmp_path: Path) -> Path:
    return tmp_path / "teams" / "t" / "inboxes" / "team-lead.json"


def test_send_shutdown_approved_emits_host_catalog_shape(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", tmp_path / "teams")

    pio.send_shutdown_approved("t", "worker", "shutdown-1")

    raw = json.loads(_lead_inbox(tmp_path).read_text())
    payload = json.loads(raw[0]["text"])
    assert payload["type"] == "shutdown_approved"
    assert payload["schema_version"] == 1
    assert payload["requestId"] == "shutdown-1"
    assert payload["from"] == "worker"
    assert "request_id" not in payload
    assert "approve" not in payload


def test_send_shutdown_rejected_emits_host_catalog_shape(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", tmp_path / "teams")

    pio.send_shutdown_rejected("t", "worker", "shutdown-2", reason="in-flight task #4")

    raw = json.loads(_lead_inbox(tmp_path).read_text())
    payload = json.loads(raw[0]["text"])
    assert payload == {
        "type": "shutdown_rejected",
        "schema_version": 1,
        "requestId": "shutdown-2",
        "from": "worker",
        "reason": "in-flight task #4",
        "timestamp": payload["timestamp"],
    }


def test_legacy_send_shutdown_response_alias_warns_and_emits_new_shape(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", tmp_path / "teams")

    pio.send_shutdown_response(
        "t",
        "worker",
        "shutdown-legacy",
        approve=False,
        feedback="busy",
    )

    err = capsys.readouterr().err
    assert "shutdown_response.deprecated_alias" in err
    raw = json.loads(_lead_inbox(tmp_path).read_text())
    payload = json.loads(raw[0]["text"])
    assert payload["type"] == "shutdown_rejected"
    assert payload["schema_version"] == 1
    assert payload["requestId"] == "shutdown-legacy"
    assert payload["reason"] == "busy"


def test_shutdown_helpers_pass_message_kind_when_transport_supports_it(monkeypatch):
    captured: list[str | None] = []

    def fake_send_plain_message(
        team,
        sender,
        to,
        body,
        *,
        summary,
        message_kind=None,
    ):
        captured.append(message_kind)

    monkeypatch.setattr(pio._m, "send_plain_message", fake_send_plain_message)

    pio.send_shutdown_approved("t", "worker", "shutdown-kind-1")
    pio.send_shutdown_rejected("t", "worker", "shutdown-kind-2", reason="busy")

    assert captured == ["shutdown_approved", "shutdown_rejected"]
