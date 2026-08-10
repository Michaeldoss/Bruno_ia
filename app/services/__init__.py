"""Inicializacao dos servicos de fundo do Bruno IA."""

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)
_started = False


def _memory_worker() -> None:
    time.sleep(20)
    while True:
        try:
            from app.services.crm_memory_service import (
                run_crm_memory_cycle,
                _is_business_hours,
                _seconds_until_next_window,
                CYCLE_INTERVAL_SECONDS,
            )
            if not _is_business_hours():
                wait_s = _seconds_until_next_window()
                logger.info(
                    "[CRM MEMORY] Fora do horario comercial (seg-sex 08-12/13-18). "
                    "Proxima janela em %.1fh.",
                    wait_s / 3600,
                )
                time.sleep(max(wait_s, 60))
                continue
            asyncio.run(run_crm_memory_cycle())
        except Exception as exc:
            logger.exception("[CRM MEMORY] Falha no worker recorrente: %s", exc)
            time.sleep(CYCLE_INTERVAL_SECONDS)
            continue
        time.sleep(CYCLE_INTERVAL_SECONDS)


def _start_memory_worker_once() -> None:
    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_memory_worker, name="crm-memory-worker", daemon=True)
    thread.start()
    logger.info("[CRM MEMORY] Worker recorrente inicializado.")


_start_memory_worker_once()
