"""Consume operational order notices, independently of the AI pilot and its pauses."""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)
_task = None
_state = {"state": "stopped", "sent": 0}
CLAIM_ENDPOINT = "https://dojisexdgitoxluuawgc.supabase.co/rest/v1/rpc/crm_order_notification_work"
SEND_ENDPOINT = "https://doss-crm.vercel.app/api/orders/notification-worker"


async def tick(client, secret):
    headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}
    claim = await client.post(CLAIM_ENDPOINT, headers=headers, json={"p_operation": "claim"}, timeout=15, follow_redirects=False)
    claim.raise_for_status()
    job = claim.json()
    if not job:
        _state["state"] = "idle"
        return
    response = await client.post(SEND_ENDPOINT, headers={"Authorization": f"Bearer {secret}"}, json={"job": job}, timeout=100, follow_redirects=False)
    response.raise_for_status()
    state = response.json().get("state")
    _state["state"] = state if state in {"sent", "held", "uncertain", "blocked"} else "unavailable"
    if state == "sent":
        _state["sent"] += 1


async def _loop():
    from app.config import get_settings
    secret = get_settings().SUPABASE_SERVICE_ROLE_KEY
    if not secret or secret == "stub":
        _state["state"] = "configuration_required"
        return
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await tick(client, secret)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _state["state"] = "unavailable"
                logger.warning("Order notification tick failed (%s)", type(error).__name__)
            await asyncio.sleep(3)


def start_order_notifications():
    global _task
    if _task is None or _task.done():
        _state["state"] = "starting"
        _task = asyncio.create_task(_loop())


async def stop_order_notifications():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _state["state"] = "stopped"


def order_notifications_status():
    return {**_state, "task_alive": bool(_task and not _task.done())}
