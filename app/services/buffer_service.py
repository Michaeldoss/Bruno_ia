import asyncio
import time
import logging
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


def _log_debug_evento(phone: str, evento: str):
    """Rastro leve pra diagnostico 14/08 -- ve por onde a mensagem passa
    sem precisar de acesso a log do Render."""
    try:
        from app.models.database import SessionLocal
        from sqlalchemy import text
        db2 = SessionLocal()
        db2.execute(text(
            "CREATE TABLE IF NOT EXISTS debug_errors "
            "(id SERIAL PRIMARY KEY, phone TEXT, erro TEXT, traceback TEXT, criado_em TIMESTAMP DEFAULT now())"
        ))
        db2.execute(
            text("INSERT INTO debug_errors (phone, erro, traceback) VALUES (:phone, :erro, :tb)"),
            {"phone": phone, "erro": f"[BUFFER] {evento}", "tb": None},
        )
        db2.commit()
        db2.close()
    except Exception:
        pass

class MessageBuffer:
    def __init__(self, debounce_seconds: float = 6.0):
        self.buffer: Dict[str, Dict[str, Any]] = {}
        self.debounce_seconds = debounce_seconds
        # FIX: asyncio.create_task(...) sem guardar referencia e risco
        # conhecido -- se o garbage collector rodar antes da task
        # terminar, ela morre no meio, sem log nenhum. Aqui e
        # especialmente grave: esse buffer processa TODA mensagem de
        # texto de TODO cliente. Se a task de _wait_and_trigger morrer
        # assim, a mensagem fica presa pra sempre (is_waiting nunca
        # volta a False), e o cliente simplesmente para de receber
        # resposta -- sem erro, sem retry, silencioso.
        self._background_tasks: set = set()

    def _fire_and_forget(self, coro):
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def add_message(self, phone: str, message: str, callback: Callable[[str, str], Any]):
        """
        Adiciona uma mensagem ao buffer de um telefone.
        Se não houver um 'waiter' ativo, inicia um em background (não bloqueia).
        """
        _log_debug_evento(phone, "add_message chamado")
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

        if not self.buffer[phone]["is_waiting"]:
            self.buffer[phone]["is_waiting"] = True
            self._fire_and_forget(self._wait_and_trigger(phone, callback))

    async def _wait_and_trigger(self, phone: str, callback: Callable):
        """
        Aguarda o silêncio (debounce) e dispara o processamento.
        """
        _log_debug_evento(phone, "_wait_and_trigger iniciado")
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

            _log_debug_evento(phone, f"debounce OK, chamando callback com {len(messages)} msgs")
            logger.info(f"Debounce finalizado para {phone}. Enviando {len(messages)} mensagens agrupadas.")
            await callback(phone, combined_message)
            _log_debug_evento(phone, "callback retornou sem excecao")

        except Exception as e:
            _log_debug_evento(phone, f"EXCECAO no _wait_and_trigger: {e}")
            logger.error(f"Erro no buffer para {phone}: {e}")
            if phone in self.buffer:
                del self.buffer[phone]

# Tempo de silencio (segundos) que o Bruno espera depois da ultima
# mensagem antes de processar o que o cliente mandou -- agrupa
# mensagens picadas em uma so troca com a IA.
message_buffer = MessageBuffer(debounce_seconds=6.0)
