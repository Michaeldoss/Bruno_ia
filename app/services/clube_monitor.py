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
            delay = 120
            try:
                reply = await client.post(SYNC, headers={"Authorization":"Bearer " + key})
                reply.raise_for_status()
                result = reply.json()
                if result.get("directFailure") or result.get("state") == "unavailable":
                    log.warning("Consulta de notas do clube pendente para nova tentativa.")
                elif any(page.get("hasMore") for page in result.get("direct", [])):
                    delay = 5
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                log.warning("Consulta de notas do clube indisponível. Nova tentativa em dois minutos.")
            await asyncio.sleep(delay)
