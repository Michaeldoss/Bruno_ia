"""Distribute nightly memory work across agents without changing access scope."""
from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256


def fair_memory_order(conversations, memories, day):
    reviewed = {row['conversation_id']: row.get('analyzed_at') for row in memories}

    def priority(row):
        value = reviewed.get(row['id'])
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            age = date.timestamp()
        except (AttributeError, ValueError, TypeError):
            age = float('-inf')
        # Rotate ties each day so permanently failing rows do not always lead.
        tie = sha256(f"{day}:{row['id']}".encode()).hexdigest()
        return age, tie

    grouped = defaultdict(list)
    for row in conversations:
        grouped[row.get('agent_id') or 'unassigned'].append(row)
    queues = [deque(sorted(rows, key=priority)) for rows in grouped.values()]
    queues.sort(key=lambda rows: priority(rows[0]))
    active = deque(queues)
    while active:
        queue = active.popleft()
        yield queue.popleft()
        if queue:
            active.append(queue)
