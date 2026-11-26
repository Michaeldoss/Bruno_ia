"""Drive the club's persistent, idempotent invoice cursor while the site is closed."""
import asyncio
import logging
import os

import httpx

log = logging.getLogger(__name__)
SYNC = "https://clube.dossgroup.com.br/api/uniplus-sync"


async def watch_invoices():
    key = os.getenv("DOSS_CLUBE_FISCAL_KEY", "")
    if len(key) < 32:
        return
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        while True:
            started = asyncio.get_running_loop().time()
            has_more = False
            try:
                reply = await client.post(SYNC, headers={"Authorization":"Bearer " + key})
                reply.raise_for_status()
                result = reply.json()
                if result.get("directFailure") or result.get("state") == "unavailable":
                    log.warning("Consulta de notas do clube pendente para nova tentativa.")
                elif any(page.get("hasMore") for page in result.get("direct", [])):
                    has_more = True
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                log.warning("Consulta de notas do clube indisponível. Nova tentativa no próximo ciclo.")
            elapsed = asyncio.get_running_loop().time() - started
            # Start each regular check one minute after the previous start. Drain
            # a multi-page day faster, without overlapping requests.
            await asyncio.sleep(5 if has_more else max(0, 60 - elapsed))
