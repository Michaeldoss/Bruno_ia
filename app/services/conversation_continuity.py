"""Detect conversational dead ends before sending a draft.

Only a confirmed, durable system operation may justify a future-action promise.
"""
import re

UNTRACKED_PROMISE = re.compile(
    r"\b(?:vou\s+(?:consultar|verificar|conferir|buscar|preparar)"
    r"|(?:j[aá]\s+)?te\s+retorno"
    r"|(?:depois|em\s+seguida|j[aá])\s+te\s+passo"
    r"|vou\s+(?:te\s+)?(?:retornar|avisar)"
    r"|(?:verifico|consulto|confiro)\s+.{0,80}?\b(?:retorno|aviso|passo))\b",
    re.IGNORECASE,
)


def ensure_continuity(reply: str, *, action_confirmed: bool = False) -> str:
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("empty_sdr_reply")
    if not action_confirmed and UNTRACKED_PROMISE.search(reply):
        raise ValueError("untracked_future_action")
    return reply.strip()

