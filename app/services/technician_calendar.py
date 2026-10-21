"""Wake the authenticated calendar-notice backend; no calendar/message data in logs.

The database schedules polls and owns the send fence. A failed invocation is not
replayed here: only a newly claimed, validated lease can reach the backend.
"""
import asyncio
import logging
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)
CLAIM_ENDPOINT = "https://dojisexdgitoxluuawgc.supabase.co/rest/v1/rpc/crm_tech_calendar_work"
WORKER_ENDPOINT = "https://doss-crm.vercel.app/api/calendar/technician-worker"
_task = None
_state = {"state": "stopped", "release": "tech-calendar-1", "sent": 0, "synced": 0}
ALLOWED_STATES = frozenset({"sent", "synced", "held", "uncertain", "blocked", "resync_required"})


def valid_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


async def tick(client, secret):
    claim = await client.post(CLAIM_ENDPOINT,
        headers={"apikey": secret, "Authorization": "Bearer " + secret},
        json={"p_operation": "claim"}, timeout=15, follow_redirects=False)
    claim.raise_for_status()
    job = claim.json()
    if job is None:
        _state["state"] = "idle"
        return "idle"
    if not isinstance(job, dict) or not valid_uuid(job.get("id")) or not valid_uuid(job.get("lease")):
        raise ValueError("invalid_calendar_claim")
    # Caller-controlled URLs, recipients, message text and tokens are never forwarded.
    response = await client.post(WORKER_ENDPOINT,
        headers={"Authorization": "Bearer " + secret},
        json={"job": {"id": job["id"], "lease": job["lease"]}},
        timeout=130, follow_redirects=False)
    response.raise_for_status()
    result = response.json()
    state = result.get("state") if isinstance(result, dict) else None
    _state["state"] = state if state in ALLOWED_STATES else "unavailable"
    if state in ("sent", "synced"):
        _state[state] += 1
    return _state["state"]


async def _loop():
    from app.config import get_settings
    secret = get_settings().SUPABASE_SERVICE_ROLE_KEY
    if not secret or secret == "stub":
        _state["state"] = "configuration_required"
        return
    async with httpx.AsyncClient() as client:
        while True:
            delay = 5
            try:
                state = await tick(client, secret)
                if state in ("sent", "synced", "blocked", "held"):
                    delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _state["state"] = "unavailable"
                # Never log response bodies, URLs containing credentials, or event data.
                logger.warning("Technician calendar worker failed (%s)", type(error).__name__)
                delay = 30
            await asyncio.sleep(delay)


def start_technician_calendar():
    global _task
    if _task is None or _task.done():
        _state["state"] = "starting"
        _task = asyncio.create_task(_loop())


async def stop_technician_calendar():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _state["state"] = "stopped"


def technician_calendar_status():
    return {**_state, "task_alive": bool(_task and not _task.done())}
