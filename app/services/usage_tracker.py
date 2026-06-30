"""
Rastreamento de custo de API Anthropic — Bruno IA
───────────────────────────────────────────────────
Preços oficiais por milhão de tokens (USD), Junho/2026:
  Haiku 4.5:  input $1.00  | output $5.00  | cache write $1.25 | cache read $0.10
  Sonnet 4.6: input $3.00  | output $15.00 | cache write $3.75 | cache read $0.30

Cache write usa multiplicador 1.25x sobre o input (cache de 5 minutos, que e o usado no Bruno).
Cache read usa 0.1x sobre o input (10% do preco normal).
"""

import logging
from app.models.database import SessionLocal, UsageLog

logger = logging.getLogger(__name__)

PRECOS = {
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
}


def calcular_custo_usd(model: str, input_tokens: int, output_tokens: int,
                        cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Calcula custo em USD de uma chamada, dado o modelo e contagem de tokens."""
    precos = PRECOS.get(model)
    if not precos:
        logger.warning(f"[USAGE] Modelo desconhecido para precificacao: {model}")
        return 0.0

    custo = (
        (input_tokens / 1_000_000) * precos["input"] +
        (output_tokens / 1_000_000) * precos["output"] +
        (cache_creation_tokens / 1_000_000) * precos["cache_write"] +
        (cache_read_tokens / 1_000_000) * precos["cache_read"]
    )
    return round(custo, 6)


def registrar_uso(model: str, usage, agente: str = "bruno"):
    """
    Registra o uso de tokens de uma resposta da API Anthropic no banco.
    'usage' e o objeto response.usage retornado pelo client.messages.create().
    Chamar isso logo apos cada resposta bem sucedida do Claude.
    Falhas aqui NUNCA devem quebrar o fluxo principal do Bruno — sempre silencioso.
    """
    db = None
    try:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        custo = calcular_custo_usd(model, input_tokens, output_tokens, cache_creation, cache_read)

        db = SessionLocal()
        db.add(UsageLog(
            agente=agente,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            custo_usd=custo,
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[USAGE] Erro ao registrar uso (nao bloqueante): {e}")
    finally:
        if db:
            db.close()
