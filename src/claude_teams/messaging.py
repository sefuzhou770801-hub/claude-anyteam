from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from claude_teams._filelock import file_lock
from claude_teams.models import (
    InboxAttachment,
    InboxMessage,
    ShutdownRequest,
    TaskAssignment,
    TaskFile,
)

TEAMS_DIR = Path.home() / ".claude" / "teams"
DEFAULT_ATTACHMENT_SPILL_CHARS = 4096
ATTACHMENT_SPILL_ENV_VARS = (
    "CLAUDE_ANYTEAM_INBOX_SPILL_CHARS",
    "CLAUDE_TEAMS_INBOX_SPILL_CHARS",
)


def _teams_dir(base_dir: Path | None = None) -> Path:
    return (base_dir / "teams") if base_dir else TEAMS_DIR


def now_iso() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def inbox_path(team_name: str, agent_name: str, base_dir: Path | None = None) -> Path:
    return _teams_dir(base_dir) / team_name / "inboxes" / f"{agent_name}.json"


def team_root(team_name: str, base_dir: Path | None = None) -> Path:
    return _teams_dir(base_dir) / team_name


def inbox_artifacts_dir(team_name: str, base_dir: Path | None = None) -> Path:
    return team_root(team_name, base_dir) / "artifacts" / "inbox"


def attachment_spill_chars() -> int:
    """Return the configured inbox body spill threshold.

    A non-positive value disables spilling. Invalid values fall back to the
    protocol default so one bad environment variable does not break mailbox
    delivery.
    """

    for name in ATTACHMENT_SPILL_ENV_VARS:
        raw = os.environ.get(name)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except ValueError:
            return DEFAULT_ATTACHMENT_SPILL_CHARS
    return DEFAULT_ATTACHMENT_SPILL_CHARS


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return cleaned[:64] or "unknown"


def _artifact_filename(message: InboxMessage, agent_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    sender = _safe_filename_part(message.from_)
    recipient = _safe_filename_part(agent_name)
    return f"{ts}-{sender}-to-{recipient}-{uuid4().hex}.txt"


def _attachment_path(
    team_name: str,
    relative_path: str | None,
    path: str,
    base_dir: Path | None = None,
) -> Path:
    root = team_root(team_name, base_dir).resolve()
    candidate = Path(relative_path) if relative_path else Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"attachment path escapes team root: {candidate}")
    return resolved


def read_attachment_text(
    team_name: str,
    attachment: InboxAttachment,
    base_dir: Path | None = None,
) -> str:
    """Read the full text body referenced by an inbox attachment."""

    path = _attachment_path(
        team_name,
        attachment.relative_path,
        attachment.path,
        base_dir=base_dir,
    )
    return path.read_text(encoding="utf-8")


def _spill_message_if_needed(
    team_name: str,
    agent_name: str,
    message: InboxMessage,
    base_dir: Path | None = None,
) -> InboxMessage:
    threshold = attachment_spill_chars()
    if threshold <= 0 or message.attachment is not None or len(message.text) <= threshold:
        return message

    artifacts_dir = inbox_artifacts_dir(team_name, base_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / _artifact_filename(message, agent_name)
    full_text = message.text
    path.write_text(full_text, encoding="utf-8")
    root = team_root(team_name, base_dir).resolve()
    relative_path = path.resolve().relative_to(root).as_posix()
    preview_body = full_text[:threshold].rstrip()
    preview = (
        f"{preview_body}\n\n"
        f"... [Full message ({len(full_text)} chars): {path}]"
    )
    attachment = InboxAttachment(
        path=str(path),
        relative_path=relative_path,
        char_count=len(full_text),
        preview_char_count=len(preview_body),
        sha256=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
    )
    return message.model_copy(update={"text": preview, "attachment": attachment})


def ensure_inbox(team_name: str, agent_name: str, base_dir: Path | None = None) -> Path:
    path = inbox_path(team_name, agent_name, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".lock"
    with file_lock(lock_path):
        if not path.exists():
            path.write_text("[]")
    return path


def read_inbox(
    team_name: str,
    agent_name: str,
    unread_only: bool = False,
    mark_as_read: bool = True,
    base_dir: Path | None = None,
) -> list[InboxMessage]:
    path = inbox_path(team_name, agent_name, base_dir)
    if not path.exists():
        return []

    if mark_as_read:
        lock_path = path.parent / ".lock"
        with file_lock(lock_path):
            raw_list = json.loads(path.read_text())
            all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]

            if unread_only:
                result = [m for m in all_msgs if not m.read]
            else:
                result = list(all_msgs)

            if result:
                for m in all_msgs:
                    if m in result:
                        m.read = True
                serialized = [m.model_dump(by_alias=True, exclude_none=True) for m in all_msgs]
                path.write_text(json.dumps(serialized))

            return result
    else:
        raw_list = json.loads(path.read_text())
        all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]

        if unread_only:
            return [m for m in all_msgs if not m.read]
        return list(all_msgs)


def read_inbox_filtered(
    team_name: str,
    agent_name: str,
    sender_filter: str,
    unread_only: bool = True,
    mark_as_read: bool = True,
    limit: int | None = None,
    base_dir: Path | None = None,
) -> list[InboxMessage]:
    """Read inbox messages filtered by sender.

    When mark_as_read=True, only messages matching the sender_filter
    (and unread_only criteria) are marked as read. Other messages in
    the inbox are left untouched.

    Args:
        team_name: Team name.
        agent_name: Whose inbox to read (e.g. "team-lead").
        sender_filter: Only return messages where from_ == sender_filter.
        unread_only: If True, skip already-read messages.
        mark_as_read: If True, mark returned messages as read on disk.
        limit: Max messages to return (newest N if set). Returns chronological order.
        base_dir: Override base directory for testing.
    """
    path = inbox_path(team_name, agent_name, base_dir)
    if not path.exists():
        return []

    if mark_as_read:
        lock_path = path.parent / ".lock"
        with file_lock(lock_path):
            raw_list = json.loads(path.read_text())
            all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]

            selected_indices = []
            for i, m in enumerate(all_msgs):
                if m.from_ != sender_filter:
                    continue
                if unread_only and m.read:
                    continue
                selected_indices.append(i)

            if limit is not None and len(selected_indices) > limit:
                selected_indices = selected_indices[-limit:]

            result = [all_msgs[i] for i in selected_indices]
            if result:
                for i in selected_indices:
                    all_msgs[i].read = True
                serialized = [m.model_dump(by_alias=True, exclude_none=True) for m in all_msgs]
                path.write_text(json.dumps(serialized))

            return result
    else:
        raw_list = json.loads(path.read_text())
        all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]

        filtered = [m for m in all_msgs if m.from_ == sender_filter]
        if unread_only:
            filtered = [m for m in filtered if not m.read]
        if limit is not None and len(filtered) > limit:
            filtered = filtered[-limit:]
        return filtered


def append_message(
    team_name: str,
    agent_name: str,
    message: InboxMessage,
    base_dir: Path | None = None,
) -> InboxMessage:
    path = ensure_inbox(team_name, agent_name, base_dir)
    lock_path = path.parent / ".lock"

    with file_lock(lock_path):
        stored_message = _spill_message_if_needed(
            team_name,
            agent_name,
            message,
            base_dir=base_dir,
        )
        raw_list = json.loads(path.read_text())
        raw_list.append(stored_message.model_dump(by_alias=True, exclude_none=True))
        path.write_text(json.dumps(raw_list))
        return stored_message


def send_plain_message(
    team_name: str,
    from_name: str,
    to_name: str,
    text: str,
    summary: str,
    color: str | None = None,
    base_dir: Path | None = None,
    message_kind: str | None = None,
) -> None:
    msg = InboxMessage(
        from_=from_name,
        text=text,
        timestamp=now_iso(),
        read=False,
        summary=summary,
        color=color,
        message_kind=message_kind or "peer_dm",
    )
    append_message(team_name, to_name, msg, base_dir)


def send_structured_message(
    team_name: str,
    from_name: str,
    to_name: str,
    payload: BaseModel,
    color: str | None = None,
    base_dir: Path | None = None,
) -> None:
    serialized = payload.model_dump_json(by_alias=True)
    msg = InboxMessage(
        from_=from_name,
        text=serialized,
        timestamp=now_iso(),
        read=False,
        color=color,
    )
    append_message(team_name, to_name, msg, base_dir)


def send_task_assignment(
    team_name: str,
    task: TaskFile,
    assigned_by: str,
    base_dir: Path | None = None,
) -> None:
    payload = TaskAssignment(
        task_id=task.id,
        subject=task.subject,
        description=task.description,
        assigned_by=assigned_by,
        timestamp=now_iso(),
        coupling=task.coupling,
    )
    send_structured_message(team_name, assigned_by, task.owner, payload, base_dir=base_dir)


def send_shutdown_request(
    team_name: str,
    recipient: str,
    reason: str = "",
    base_dir: Path | None = None,
) -> str:
    request_id = f"shutdown-{int(time.time() * 1000)}@{recipient}"
    payload = ShutdownRequest(
        request_id=request_id,
        from_="team-lead",
        reason=reason,
        timestamp=now_iso(),
    )
    send_structured_message(team_name, "team-lead", recipient, payload, base_dir=base_dir)
    return request_id
