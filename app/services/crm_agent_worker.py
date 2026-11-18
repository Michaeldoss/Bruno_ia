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
    settings = get_settings()
    secret = settings.SUPABASE_SERVICE_ROLE_KEY
    consumer_count = max(1, min(int(settings.CRM_AGENT_CONSUMERS), 3))
    _state["consumer_count"] = consumer_count
    if not secret or secret == "stub":
        _state["state"] = "configuration_required"
        return
    async with httpx.AsyncClient() as client:
        while not await verify_model(client, secret):
            await asyncio.sleep(30)
        await asyncio.gather(*(_consume(client, secret) for _ in range(consumer_count)))


async def verify_model(client, secret):
    def body_from(response):
        try:
            body = response.json()
            return body if isinstance(body, dict) else {}
        except (ValueError, TypeError):
            return {}

    def diagnostic_code(body, fallback):
        code = body.get("code") or body.get("state")
        return (code if isinstance(code, str) and 0 < len(code) <= 90
                and code.replace("_", "").isalnum() else fallback)

    try:
        probe = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {secret}"},
                                  json={"probe": True}, timeout=100, follow_redirects=False)
        probe.raise_for_status()
        body = body_from(probe)
        verified = body.get("state") == "ready"
        _state.update(state="ready" if verified else "unavailable", model_verified=verified,
                      verification_http_status=probe.status_code,
                      verification_code="ready" if verified else diagnostic_code(body, "invalid_probe_response"))
        return verified
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        fallback = {401: "worker_auth_check_failed", 403: "worker_forbidden", 429: "worker_rate_limited"}.get(
            status, "worker_redirected" if 300 <= status < 400 else f"worker_http_{status}")
        # Error bodies may be HTML, malformed JSON, or a plain `error` string.
        # Never copy that string, headers, tokens or URLs into public diagnostics.
        safe_code = diagnostic_code(body_from(error.response), fallback)
        _state.update(state="unavailable", model_verified=False, verification_code=safe_code,
                      verification_http_status=status)
        logger.warning("CRM agent startup check failed (http=%s code=%s)", status, safe_code)
    except Exception as error:
        safe_code = "worker_probe_timeout" if isinstance(error, httpx.TimeoutException) else "worker_probe_unavailable"
        _state.update(state="unavailable", model_verified=False,
                      verification_http_status=None, verification_code=safe_code)
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
        # Three consumers used to poll every 5s even while idle. A 15s cadence
        # keeps lead automation responsive while cutting idle database traffic by ~67%.
        await asyncio.sleep(15)


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

