"""Wake the native CRM agent; all routing and effects stay in the authenticated CRM backend."""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)
_task = None
_state = {"state": "stopped"}
ENDPOINT = "https://doss-crm.vercel.app/api/ai/agent-worker"
CLAIM_ENDPOINT = "https://dojisexdgitoxluuawgc.supabase.co/rest/v1/rpc/crm_agent_work"


async def tick(client, secret):
    # Fixed origin, no redirects: the integration credential must never follow a URL from customer data.
    claim = await client.post(CLAIM_ENDPOINT, headers={"apikey": secret, "Authorization": f"Bearer {secret}"},
                              json={"p_operation": "claim"}, timeout=15, follow_redirects=False)
    claim.raise_for_status()
    job = claim.json()
    if not job:
        _state["state"] = "idle"
        return
    response = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {secret}"},
                                 json={"job": {"id": job["id"], "lease": job["lease"]}}, timeout=125, follow_redirects=False)
    response.raise_for_status()
    body = response.json()
    state = body.get("state")
    _state["state"] = state if state in {"idle", "held", "completed", "failed", "uncertain"} else "unavailable"


async def _loop():
    from app.config import get_settings
    secret = get_settings().SUPABASE_SERVICE_ROLE_KEY
    if not secret or secret == "stub":
        _state["state"] = "configuration_required"
        return
    async with httpx.AsyncClient() as client:
        while not await verify_model(client, secret):
            await asyncio.sleep(30)
        await asyncio.gather(*(_consume(client, secret) for _ in range(3)))


async def verify_model(client, secret):
    try:
        probe = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {secret}"},
                                  json={"probe": True}, timeout=100, follow_redirects=False)
        probe.raise_for_status()
        _state["model_verified"] = probe.json().get("state") == "ready"
        return _state["model_verified"]
    except httpx.HTTPStatusError as error:
        body = error.response.json() if error.response.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code") or body.get("state") or "unknown"
        safe_code = code if isinstance(code, str) and len(code) <= 90 and code.replace("_", "").isalnum() else "unknown"
        _state.update(state="unavailable", model_verified=False, verification_code=safe_code)
        logger.warning("CRM agent startup check failed (http=%s code=%s)", error.response.status_code, safe_code)
    except Exception as error:
        _state.update(state="unavailable", model_verified=False)
        logger.warning("CRM agent startup check failed (%s)", type(error).__name__)
    return False


async def _consume(client, secret):
    while True:
        try:
            await tick(client, secret)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _state["state"] = "unavailable"
            logger.warning("CRM agent tick failed (%s)", type(error).__name__)
        await asyncio.sleep(5)


def start_agent_worker():
    global _task
    if _task is None or _task.done():
        _state["state"] = "starting"
        _task = asyncio.create_task(_loop())


async def stop_agent_worker():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _state["state"] = "stopped"


def agent_worker_status():
    return {**_state, "task_alive": bool(_task and not _task.done())}
