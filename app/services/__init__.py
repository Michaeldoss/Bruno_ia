"""Inicializacao dos servicos de fundo do Bruno IA."""

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_started = False
_memory_thread = None

BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")
HORA_REVISAO = 19  # pedido 24/08: 1x/dia, as 19h, nao mais a cada N horas


def _segundos_ate_19h() -> float:
    agora = datetime.now(BRASILIA_TZ)
    hoje_19h = agora.replace(hour=HORA_REVISAO, minute=0, second=0, microsecond=0)
    alvo = hoje_19h if agora < hoje_19h else hoje_19h + timedelta(days=1)
    return (alvo - agora).total_seconds()


async def _memory_worker_loop() -> None:
    # Revisao diaria as 19h: novos lotes e historico pendente de qualquer data.
    # Conversas sem mensagens novas nao chamam a IA; limites sao checados no ciclo.
    while True:
        espera = _segundos_ate_19h()
        logger.info("[CRM MEMORY] Proxima revisao diaria em %.1fh (19h Brasilia).", espera / 3600)
        await asyncio.sleep(espera)
        try:
            from app.services.crm_memory_service import run_crm_memory_cycle, ORG_ID
            from app.services.memory_tenant import require_org
            # Server-managed allowlist; never accept company IDs from incoming messages.
            configured = os.getenv("CRM_MEMORY_ORG_IDS", ORG_ID)
            organizations = list(dict.fromkeys(require_org(value.strip()) for value in configured.split(',') if value.strip()))
            for org_id in organizations:
                try:
                    await run_crm_memory_cycle(org_id=org_id)
                except Exception:
                    logger.exception("[CRM MEMORY] Falha na organizacao %s", org_id)
        except Exception as exc:
            logger.exception("[CRM MEMORY] Falha na revisao diaria: %s", exc)
        # dorme um pouco alem de imediato pra nao rodar 2x se o calculo
        # de "ate 19h" cair exatamente em cima do segundo certo
        await asyncio.sleep(60)


def _memory_worker() -> None:
    # The pooled async provider client must stay on one event loop across nights
    # and organizations. Do not close its loop after each cycle.
    asyncio.run(_memory_worker_loop())


def _start_memory_worker_once() -> None:
    global _started, _memory_thread
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_memory_worker, name="crm-memory-worker", daemon=True)
    _memory_thread = thread
    thread.start()
    logger.info("[CRM MEMORY] Worker diario inicializado (revisao as 19h).")


def memory_worker_alive() -> bool:
    """Process liveness only; does not certify a successful nightly analysis."""
    return _memory_thread is not None and _memory_thread.is_alive()


_start_memory_worker_once()
