"""Check the identity/order of retained messages, without sending them to an LLM."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

EMPTY_DIGEST = hashlib.sha256(b'crm-history-ids-v1').hexdigest()


def message_key(row):
    date = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Message date requires timezone')
    return date.astimezone(timezone.utc).isoformat(), str(UUID(row['id']))


def extend_digest(digest, rows):
    for row in rows:
        encoded = json.dumps(message_key(row), separators=(',', ':')).encode()
        digest = hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest()
    return digest


async def retained_prefix_digest(client, url, headers, org_id, conversation_id, checkpoint):
    """Scan only IDs/dates through the saved cursor; never infer completeness from a short page."""
    end_date, end_id = message_key({'created_at': checkpoint['last_message_at'],
                                    'id': checkpoint['last_message_id']})
    after = None
    digest = EMPTY_DIGEST
    count = 0
    while True:
        params = {'org_id': f'eq.{org_id}', 'conversation_id': f'eq.{conversation_id}',
                  'is_internal_note': 'eq.false', 'deleted_at': 'is.null',
                  'select': 'id,created_at', 'order': 'created_at.asc,id.asc', 'limit': 500,
                  'or': f'(created_at.lt.{end_date},and(created_at.eq.{end_date},id.lte.{end_id}))'}
        if after:
            date, row_id = after
            params['and'] = f'(or(created_at.gt.{date},and(created_at.eq.{date},id.gt.{row_id})))'
        response = await client.get(f'{url}/rest/v1/messages', params=params, headers=headers)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError('Invalid reconciliation response')
        if not rows:
            return digest
        for row in rows:
            key = message_key(row)
            if (after and key <= after) or key > (end_date, end_id):
                raise ValueError('Reconciliation cursor did not advance in range')
            after = key
        count += len(rows)
        if count > 20000:
            raise ValueError('Reconciliation exceeds 20000 messages; server-side reconciliation required')
        digest = extend_digest(digest, rows)
