"""Process-local cycle diagnostics. No messages, phones or exception details."""
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_cycles = {}


def begin_cycle(org_id):
    with _lock:
        _cycles[org_id] = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}


def finish_cycle(org_id, result):
    with _lock:
        _cycles[org_id] = {**_cycles.get(org_id, {}), **result,
                           "finished_at": datetime.now(timezone.utc).isoformat()}


def cycle_snapshot(org_id):
    with _lock:
        return deepcopy(_cycles.get(org_id, {"status": "not_observed_in_this_process"}))
