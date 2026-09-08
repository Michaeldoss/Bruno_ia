"""Versioned processing checkpoints. No database schema or API keys live here."""
from datetime import datetime
from uuid import UUID

VERSION = 2
MEMORY_FIELDS = ("facts", "preferences", "products", "objections", "promises", "next_steps")


def checkpoint_of(previous):
    raw = previous.get("raw_analysis") or {}
    checkpoint = raw.get("coverage") if isinstance(raw, dict) else None
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != VERSION:
        return None
    try:
        UUID(str(checkpoint["last_message_id"]))
        datetime.fromisoformat(checkpoint["last_message_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError):
        return None
    return checkpoint


def cursor_filter(checkpoint):
    if not checkpoint:
        return None
    date, message_id = checkpoint["last_message_at"], checkpoint["last_message_id"]
    return f"(created_at.gt.{date},and(created_at.eq.{date},id.gt.{message_id}))"


def valid_analysis(value):
    return (isinstance(value, dict) and isinstance(value.get("summary"), str)
            and bool(value["summary"].strip()) and isinstance(value.get("memory"), dict)
            and all(isinstance(value["memory"].get(key, []), list) for key in MEMORY_FIELDS))


def make_coverage(messages, has_more, target, participants):
    last = messages[-1]
    return {
        "version": VERSION,
        "last_message_id": last["id"],
        "last_message_at": last["created_at"],
        "complete": not has_more,
        "observed_conversation_last_message_at": target,
        "batch_message_ids": [row["id"] for row in messages],
        "participants": participants,
        "internal_notes_included": False,
        "media_analysis": "text_and_available_audio_transcriptions_only",
    }
