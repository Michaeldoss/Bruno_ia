"""Inicializacao dos servicos de fundo do Bruno IA."""

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_started = False

BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")
HORA_REVISAO = 19  # pedido 24/08: 1x/dia, as 19h, nao mais a cada N horas


def _segundos_ate_19h() -> float:
    agora = datetime.now(BRASILIA_TZ)
    hoje_19h = agora.replace(hour=HORA_REVISAO, minute=0, second=0, microsecond=0)
    alvo = hoje_19h if agora < hoje_19h else hoje_19h + timedelta(days=1)
    return (alvo - agora).total_seconds()


def _memory_worker() -> None:
    # Revisao diaria as 19h: novos lotes e historico pendente de qualquer data.
    # Conversas sem mensagens novas nao chamam a IA; limites sao checados no ciclo.
    while True:
        espera = _segundos_ate_19h()
        logger.info("[CRM MEMORY] Proxima revisao diaria em %.1fh (19h Brasilia).", espera / 3600)
        time.sleep(espera)
        try:
            from app.services.crm_memory_service import run_crm_memory_cycle
            asyncio.run(run_crm_memory_cycle())
        except Exception as exc:
            logger.exception("[CRM MEMORY] Falha na revisao diaria: %s", exc)
        # dorme um pouco alem de imediato pra nao rodar 2x se o calculo
        # de "ate 19h" cair exatamente em cima do segundo certo
        time.sleep(60)


def _start_memory_worker_once() -> None:
    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_memory_worker, name="crm-memory-worker", daemon=True)
    thread.start()
    logger.info("[CRM MEMORY] Worker diario inicializado (revisao as 19h).")


_start_memory_worker_once()
