import asyncio
import time
import logging
from typing import Dict, Optional, Callable, Any

logger = logging.getLogger(__name__)


class MessageBuffer:
    def __init__(self, debounce_seconds: float = 6.0):
        self.buffer: Dict[str, Dict[str, Any]] = {}
        self.debounce_seconds = debounce_seconds

    async def add_message(
        self,
        phone: str,
        message: str,
        callback: Callable[[str, str, Optional[dict]], Any],
        context: Optional[dict] = None,
    ):
        """Agrupa mensagens do mesmo telefone e preserva o contexto do webhook.

        O contexto contém SIDs e dados de atribuição. Assim o debounce não perde
        campanha/origem e cada evento pode ser marcado como processado ao final.
        """
        if phone not in self.buffer:
            self.buffer[phone] = {
                "messages": [],
                "contexts": [],
                "last_received": 0,
                "is_waiting": False,
            }
            logger.info("[BUFFER] Iniciando nova fila para %s", phone)

        self.buffer[phone]["messages"].append(message)
        if context:
            self.buffer[phone]["contexts"].append(context)
        self.buffer[phone]["last_received"] = time.time()
        logger.info("[BUFFER] Mensagem adicionada para %s. Total: %s", phone, len(self.buffer[phone]["messages"]))

        if not self.buffer[phone]["is_waiting"]:
            self.buffer[phone]["is_waiting"] = True
            asyncio.create_task(self._wait_and_trigger(phone, callback))

    async def _wait_and_trigger(self, phone: str, callback: Callable):
        try:
            while True:
                current = self.buffer.get(phone)
                if not current:
                    return
                elapsed = time.time() - current["last_received"]
                if elapsed >= self.debounce_seconds:
                    break
                await asyncio.sleep(max(0.5, self.debounce_seconds - elapsed))

            current = self.buffer.pop(phone, None)
            if not current:
                return

            messages = current["messages"]
            contexts = current["contexts"]
            combined_message = " ".join(messages)
            merged_context = {
                "message_sids": [
                    sid
                    for ctx in contexts
                    for sid in ctx.get("message_sids", [])
                    if sid
                ]
            }
            for ctx in contexts:
                for key, value in ctx.items():
                    if key != "message_sids" and value and not merged_context.get(key):
                        merged_context[key] = value

            logger.info("Debounce finalizado para %s. Enviando %s mensagens agrupadas.", phone, len(messages))
            await callback(phone, combined_message, merged_context)

        except Exception as exc:
            logger.error("Erro no buffer para %s: %s", phone, exc, exc_info=True)
            self.buffer.pop(phone, None)


message_buffer = MessageBuffer(debounce_seconds=6.0)
