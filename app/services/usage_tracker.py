"""
Rastreamento de custo de API — Bruno IA
───────────────────────────────────────────────────
Cobre: Anthropic (Claude), Twilio (WhatsApp), OpenAI (Whisper).
Render/hospedagem e custo fixo, configurado manualmente (ver CUSTOS_FIXOS_MENSAIS_USD).
"""

import os
import logging
from datetime import datetime, timezone
from app.models.database import SessionLocal, UsageLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TETO MENSAL DE GASTO ANTHROPIC — pedido 24/08: maximo US$20/mes.
# Degrada em 2 estagios em vez de cortar tudo de uma vez: perto do teto
# (80%) forca Haiku pra tudo (bem mais barato que Sonnet, ~5x); no teto
# (100%) para de gerar resposta por IA e entrega pra humano, com aviso.
# ---------------------------------------------------------------------------
TETO_MENSAL_ANTHROPIC_USD = float(os.getenv("TETO_MENSAL_ANTHROPIC_USD", "20.0"))
LIMIAR_ECONOMIA_PCT = float(os.getenv("LIMIAR_ECONOMIA_PCT", "0.80"))  # 80% do teto


_cache_orcamento = {"gasto": 0.0, "quando": None}


def custo_anthropic_mes_atual(usar_cache: bool = True) -> float:
    """Soma o custo real (calculado por token, nao estimado) de todas as
    chamadas Anthropic desde o dia 1 do mes corrente (UTC). Cacheado por
    2 minutos -- checagem roda em toda mensagem, nao precisa ser
    perfeitamente em tempo real pra um teto mensal."""
    agora = datetime.now(timezone.utc)
    if usar_cache and _cache_orcamento["quando"]:
        if (agora - _cache_orcamento["quando"]).total_seconds() < 120:
            return _cache_orcamento["gasto"]

    db = SessionLocal()
    try:
        inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = (
            db.query(UsageLog)
            .filter(UsageLog.servico == "anthropic", UsageLog.created_at >= inicio_mes)
            .with_entities(UsageLog.custo_usd)
            .all()
        )
        gasto = round(sum(c[0] or 0 for c in total), 4)
        _cache_orcamento["gasto"] = gasto
        _cache_orcamento["quando"] = agora
        return gasto
    except Exception as e:
        logger.error(f"[USAGE] Erro ao calcular custo mensal (assumindo 0, falha aberta): {e}")
        return 0.0
    finally:
        db.close()


def status_orcamento_mensal() -> str:
    """Retorna 'normal', 'economia' ou 'estourado' de acordo com o gasto
    Anthropic acumulado no mes atual vs TETO_MENSAL_ANTHROPIC_USD."""
    gasto = custo_anthropic_mes_atual()
    if gasto >= TETO_MENSAL_ANTHROPIC_USD:
        return "estourado"
    if gasto >= TETO_MENSAL_ANTHROPIC_USD * LIMIAR_ECONOMIA_PCT:
        return "economia"
    return "normal"

# ---------------------------------------------------------------------------
# PRECOS — Anthropic (USD por milhao de tokens), Junho/2026
# ---------------------------------------------------------------------------
PRECOS_ANTHROPIC = {
    "claude-haiku-4-5-20251001": {
        "input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10,
    },
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30,
    },
}

# ---------------------------------------------------------------------------
# PRECOS — Twilio WhatsApp (USD por mensagem, Brasil)
# Valor aproximado por mensagem enviada via WhatsApp Business API no Brasil.
# Ajustar se a tarifa real for diferente.
# ---------------------------------------------------------------------------
PRECO_TWILIO_MSG_USD = 0.0085

# ---------------------------------------------------------------------------
# PRECOS — OpenAI Whisper (USD por minuto de audio transcrito)
# ---------------------------------------------------------------------------
PRECO_WHISPER_USD_MIN = 0.006

# ---------------------------------------------------------------------------
# CUSTOS FIXOS MENSAIS — configurado manualmente, dividido por dia para
# entrar na simulacao em tempo real. Ajuste estes valores quando o plano mudar.
# ---------------------------------------------------------------------------
CUSTOS_FIXOS_MENSAIS_USD = {
    "render": 7.00,
    "github": 0.00,
}


def calcular_custo_anthropic(model: str, input_tokens: int, output_tokens: int,
                              cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    precos = PRECOS_ANTHROPIC.get(model)
    if not precos:
        logger.warning(f"[USAGE] Modelo Anthropic desconhecido: {model}")
        return 0.0
    custo = (
        (input_tokens / 1_000_000) * precos["input"] +
        (output_tokens / 1_000_000) * precos["output"] +
        (cache_creation_tokens / 1_000_000) * precos["cache_write"] +
        (cache_read_tokens / 1_000_000) * precos["cache_read"]
    )
    return round(custo, 6)


def registrar_uso_anthropic(model: str, usage, agente: str = "bruno"):
    """Registra uso de tokens Claude. Nunca quebra o fluxo principal (falha silenciosa)."""
    db = None
    try:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        custo = calcular_custo_anthropic(model, input_tokens, output_tokens, cache_creation, cache_read)

        db = SessionLocal()
        db.add(UsageLog(
            agente=agente,
            servico="anthropic",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            quantidade=0,
            custo_usd=custo,
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[USAGE] Erro ao registrar uso Anthropic (nao bloqueante): {e}")
    finally:
        if db:
            db.close()


def registrar_uso_twilio(agente: str = "bruno", quantidade_mensagens: int = 1):
    """Registra envio de mensagem(ns) via Twilio WhatsApp."""
    db = None
    try:
        custo = round(PRECO_TWILIO_MSG_USD * quantidade_mensagens, 6)
        db = SessionLocal()
        db.add(UsageLog(
            agente=agente,
            servico="twilio",
            model="whatsapp",
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            quantidade=quantidade_mensagens,
            custo_usd=custo,
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[USAGE] Erro ao registrar uso Twilio (nao bloqueante): {e}")
    finally:
        if db:
            db.close()


def registrar_uso_whisper(agente: str = "bruno", segundos_audio: float = 0.0):
    """Registra transcricao de audio via Whisper."""
    db = None
    try:
        minutos = segundos_audio / 60.0
        custo = round(PRECO_WHISPER_USD_MIN * minutos, 6)
        db = SessionLocal()
        db.add(UsageLog(
            agente=agente,
            servico="whisper",
            model="whisper-1",
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            quantidade=round(minutos, 4),
            custo_usd=custo,
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[USAGE] Erro ao registrar uso Whisper (nao bloqueante): {e}")
    finally:
        if db:
            db.close()


# Alias retrocompativel
registrar_uso = registrar_uso_anthropic
