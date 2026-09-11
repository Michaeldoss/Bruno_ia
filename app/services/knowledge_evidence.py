"""Recheck conversation-derived knowledge without returning private source text."""
import hashlib
import json
from uuid import UUID

from app.services.memory_tenant import require_org


async def evidence_is_current(client, url, headers, row, *, org_id):
    org_id = require_org(org_id)
    if row.get("org_id") != org_id:
        return False
    if row.get("source_type") != "conversation_candidate":
        return True  # Manually maintained sources follow the existing approval policy.
    try:
        source = json.loads(row.get("source_reference") or "")
        if not isinstance(source, dict) or source.get("version") != 1 or source.get("org_id") != org_id:
            return False
        conversation_id = str(UUID(source["conversation_id"]))
        evidence = source["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 400:
            return False
        expected = {}
        for entry in evidence:
            message_id = str(UUID(entry["id"]))
            digest = entry["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                return False
            if message_id in expected:
                return False
            expected[message_id] = digest
    except (ValueError, TypeError, KeyError, AttributeError):
        return False

    response = await client.get(f"{url}/rest/v1/conversations", headers=headers,
        params={"org_id": f"eq.{org_id}", "id": f"eq.{conversation_id}", "select": "id", "limit": 1})
    response.raise_for_status()
    conversations = response.json()
    if not isinstance(conversations, list) or len(conversations) != 1 or conversations[0].get("id") != conversation_id:
        return False

    ids = list(expected)
    for offset in range(0, len(ids), 80):
        batch = ids[offset:offset + 80]
        response = await client.get(f"{url}/rest/v1/messages", headers=headers, params={
            "org_id": f"eq.{org_id}", "conversation_id": f"eq.{conversation_id}",
            "id": f"in.({','.join(batch)})", "deleted_at": "is.null",
            "is_internal_note": "eq.false", "select": "id,content", "limit": len(batch)})
        response.raise_for_status()
        messages = response.json()
        if not isinstance(messages, list) or len(messages) != len(batch):
            return False
        if any(not isinstance(message, dict) for message in messages):
            return False
        if {message.get("id") for message in messages} != set(batch):
            return False
        for message in messages:
            content = message.get("content") or ""
            if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != expected[message["id"]]:
                return False
    return True
