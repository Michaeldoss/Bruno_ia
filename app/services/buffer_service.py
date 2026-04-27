import asyncio
import time
import logging
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)

class MessageBuffer:
    def __init__(self, debounce_seconds: float = 6.0):  # FIX: era 8.0, reduzido para 3.0
        self.buffer: Dict[str, Dict[str, Any]] = {}
        self.debounce_seconds = debounce_seconds

    async def add_message(self, phone: str, message: str, callback: Callable[[str, str], Any]):
        """
        Adiciona uma mensagem ao buffer de um telefone.
        Se não houver um 'waiter' ativo, inicia um em background (não bloqueia).
        """
        if phone not in self.buffer:
            self.buffer[phone] = {
                "messages": [],
                "last_received": 0,
                "is_waiting": False
            }
            logger.info(f"[BUFFER] Iniciando nova fila para {phone}")

        self.buffer[phone]["messages"].append(message)
        self.buffer[phone]["last_received"] = time.time()
        logger.info(f"[BUFFER] Mensagem adicionada para {phone}. Total: {len(self.buffer[phone]['messages'])}")

        # FIX: usar asyncio.create_task para não bloquear o caller
        if not self.buffer[phone]["is_waiting"]:
            self.buffer[phone]["is_waiting"] = True
            asyncio.create_task(self._wait_and_trigger(phone, callback))

    async def _wait_and_trigger(self, phone: str, callback: Callable):
        """
        Aguarda o silêncio (debounce) e dispara o processamento.
        """
        try:
            while True:
                elapsed = time.time() - self.buffer[phone]["last_received"]
                if elapsed >= self.debounce_seconds:
                    break
                wait_time = max(0.5, self.debounce_seconds - elapsed)
                await asyncio.sleep(wait_time)

            # Tempo de silêncio atingido
            messages = self.buffer[phone]["messages"]
            combined_message = " ".join(messages)

            # Limpa o buffer ANTES de disparar
            del self.buffer[phone]

            logger.info(f"Debounce finalizado para {phone}. Enviando {len(messages)} mensagens agrupadas.")
            await callback(phone, combined_message)

        except Exception as e:
            logger.error(f"Erro no buffer para {phone}: {e}")
            if phone in self.buffer:
                del self.buffer[phone]

# FIX: debounce_seconds=3.0 (era 8.0 — causava sensação de Bruno "parado")
message_buffer = MessageBuffer(debounce_seconds=6.0)
