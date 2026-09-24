"""Persistent conversation history with a compact snapshot and append journal."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_SESSIONS = 30
SESSION_MAX_MESSAGES = 200
JOURNAL_MAX_BYTES = 512 * 1024
SNAPSHOT_COMPACT_BYTES = 4 * 1024 * 1024
TITLE_CHARS = 28
SCHEMA = 2

_DROP_FIELDS = ("image", "html")
_TEXT_LIMITS = {
    "user": 2000,
    "assistant": 4000,
    "tool": 1600,
    "error": 2000,
    "info": 800,
}
_FIELD_LIMITS = {
    "detail": 1200,
    "recovery": 800,
    "report": 1600,
    "imageNote": 240,
}
_TRUNCATION_MARK = "…（历史已截断）"


@dataclass
class Session:
    id: str = ""
    started: float = 0.0
    updated: float = 0.0
    title: str = ""
    messages: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "started": self.started,
            "updated": self.updated,
            "title": self.title,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, raw) -> "Session":
        if not isinstance(raw, dict):
            return cls()
        messages = raw.get("messages")
        return cls(
            id=str(raw.get("id") or ""),
            started=float(raw.get("started") or 0.0),
            updated=float(raw.get("updated") or 0.0),
            title=str(raw.get("title") or ""),
            messages=[_trim_message(item) for item in messages
                      if isinstance(item, dict)]
            if isinstance(messages, list) else [],
        )

    def preview(self) -> str:
        for item in self.messages:
            if item.get("role") == "user":
                text = " ".join(str(item.get("text") or "").split())
                if text:
                    return text[:60]
        return "（没有对话内容）"


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    keep = max(0, limit - len(_TRUNCATION_MARK))
    return value[:keep] + _TRUNCATION_MARK


def _trim_message(item: dict) -> dict:
    out = {key: value for key, value in (item or {}).items()
           if key not in _DROP_FIELDS}
    role = str(out.get("role") or "")
    if isinstance(out.get("text"), str):
        out["text"] = _clip(out["text"], _TEXT_LIMITS.get(role, 1200))
    for key, limit in _FIELD_LIMITS.items():
        if isinstance(out.get(key), str):
            out[key] = _clip(out[key], limit)
    if out.get("plain") == out.get("text"):
        out.pop("plain", None)
    for key in ("detail", "recovery", "report", "imageNote"):
        if not out.get(key):
            out.pop(key, None)
    return out


def make_title(messages: list) -> str:
    for item in messages:
        if item.get("role") == "user":
            text = " ".join(str(item.get("text") or "").split())
            if text:
                return text[:TITLE_CHARS]
    return "新对话"


def new_session(messages: list | None = None) -> Session:
    now = time.time()
    return Session(
        id=f"s{int(now * 1000)}",
        started=now,
        updated=now,
        title=make_title(messages or []),
        messages=[_trim_message(item) for item in (messages or [])],
    )


def default_path(store_path) -> Path:
    return Path(store_path).parent / "conversations.json"


def journal_path(path: Path) -> Path:
    return Path(path).with_suffix(".jsonl")


def _read_snapshot(path: Path) -> list[Session]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), list):
        return []
    sessions = []
    for item in raw["sessions"]:
        session = Session.from_dict(item)
        if session.id and session.messages:
            sessions.append(session)
    return sessions


def _apply_journal(sessions: list[Session], path: Path) -> list[Session]:
    by_id = {session.id: session for session in sessions}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                operation = record.get("op")
                if operation == "upsert":
                    session = Session.from_dict(record.get("session"))
                    if session.id and session.messages:
                        by_id[session.id] = session
                elif operation == "append":
                    session_id = str(record.get("id") or "")
                    raw_message = record.get("message")
                    if not session_id or not isinstance(raw_message, dict):
                        continue
                    message = _trim_message(raw_message)
                    if not message:
                        continue
                    try:
                        started = float(record.get("started") or time.time())
                        updated = float(record.get("updated") or started)
                    except (TypeError, ValueError):
                        continue
                    session = by_id.get(session_id)
                    if session is None:
                        session = Session(
                            id=session_id,
                            started=started,
                            updated=updated,
                            title=str(record.get("title") or ""),
                            messages=[],
                        )
                        by_id[session_id] = session
                    message_id = str(message.get("id") or "")
                    if message_id and any(
                            str(item.get("id") or "") == message_id
                            for item in session.messages):
                        continue
                    session.messages.append(message)
                    session.messages = session.messages[-SESSION_MAX_MESSAGES:]
                    session.updated = updated
                    if not session.title:
                        session.title = make_title(session.messages)
                elif operation == "delete":
                    by_id.pop(str(record.get("id") or ""), None)
                elif operation == "clear":
                    by_id.clear()
    except OSError:
        pass
    return sorted(by_id.values(), key=lambda item: item.updated, reverse=True)


def load(path: Path) -> list[Session]:
    path = Path(path)
    return _apply_journal(_read_snapshot(path), journal_path(path))


def _prepare_sessions(sessions: list[Session]) -> list[Session]:
    trimmed: list[Session] = []
    for session in sorted(sessions, key=lambda item: item.updated, reverse=True):
        if not session.messages:
            continue
        trimmed.append(Session(
            id=session.id,
            started=session.started,
            updated=session.updated,
            title=session.title,
            messages=[_trim_message(item)
                      for item in session.messages[-SESSION_MAX_MESSAGES:]],
        ))
        if len(trimmed) >= MAX_SESSIONS:
            break
    return trimmed


def _snapshot_payload(sessions: list[Session]) -> dict:
    return {
        "schema": SCHEMA,
        "sessions": [session.as_dict()
                     for session in _prepare_sessions(sessions)],
    }


def _write_snapshot(path: Path, sessions: list[Session]) -> None:
    payload = json.dumps(_snapshot_payload(sessions), ensure_ascii=False, indent=1)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def save(path: Path, sessions: list[Session]) -> tuple[bool, str]:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_snapshot(path, sessions)
        try:
            journal_path(path).unlink()
        except FileNotFoundError:
            pass
        return True, "已保存"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"写不进 {path.name}：{exc}"


def append_session(path: Path, session: Session) -> tuple[bool, str]:
    path = Path(path)
    journal = journal_path(path)
    try:
        prepared = _prepare_sessions([session])
        if not prepared:
            return False, "没有可保存的会话"
        if not path.exists() and not journal.exists():
            return save(path, prepared)
        record = {"op": "upsert", "session": prepared[0].as_dict()}
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False,
                                    separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if journal.stat().st_size >= JOURNAL_MAX_BYTES:
            compact(path)
        return True, "已保存"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"写不进 {path.name}：{exc}"


def append_messages(path: Path, session: Session, messages: list[dict]) -> tuple[bool, str]:
    """按消息追加当前会话，避免每次重复写整段会话正文。"""
    path = Path(path)
    journal = journal_path(path)
    prepared = [_trim_message(item) for item in messages
                if isinstance(item, dict)]
    if not session.id or not prepared:
        return True, "没有新增消息"

    try:
        journal.parent.mkdir(parents=True, exist_ok=True)
        # 第一次写入先建立完整快照。这样用户目录始终有一个可直接备份、
        # 导出的 conversations.json；后续更新才追加到 jsonl，避免每轮重写
        # 整个会话。崩溃发生在第一次写入时也不会只留下一个不完整的日志文件。
        if not path.exists() and not journal.exists():
            return save(path, [Session(
                id=session.id,
                started=session.started,
                updated=session.updated,
                title=session.title,
                messages=prepared,
            )])

        with journal.open("a", encoding="utf-8", newline="\n") as handle:
            for message in prepared:
                record = {
                    "op": "append",
                    "id": session.id,
                    "started": session.started,
                    "updated": session.updated,
                    "title": session.title,
                    "message": message,
                }
                handle.write(json.dumps(record, ensure_ascii=False,
                                        separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if journal.stat().st_size >= JOURNAL_MAX_BYTES:
            compact(path)
        return True, "已保存"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"写不进 {path.name}：{exc}"


def compact(path: Path) -> tuple[bool, str]:
    return save(path, load(path))


def compact_if_needed(path: Path, sessions: list[Session]) -> tuple[bool, str]:
    path = Path(path)
    journal = journal_path(path)
    try:
        snapshot_large = path.stat().st_size >= SNAPSHOT_COMPACT_BYTES
        journal_large = journal.stat().st_size >= JOURNAL_MAX_BYTES
    except OSError:
        return True, "无需整理"
    if snapshot_large or journal_large:
        return save(path, sessions)
    return True, "无需整理"


def total_messages(sessions: list[Session]) -> int:
    return sum(len(session.messages) for session in sessions)
