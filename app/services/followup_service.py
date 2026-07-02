"""
Follow-up automático do Bruno IA
─────────────────────────────────
  Step 1 →  30min  → verifica dúvida
  Step 2 →   2h    → argumento técnico novo
  Step 3 →   5h    → novo ângulo
  Step 4 →  24h    → reengajamento
  Step 5 →  48h    → última tentativa
  Encerra →  72h   → fecha por inatividade

Regras:
- Follow-ups APENAS dentro do horário comercial: 08:00-12:00 / 13:30-18:00
- Horário baseado em Brasília (UTC-3)
- Se o cliente responder, ciclo reinicia do zero
- Cada step só dispara após intervalo mínimo desde o STEP ANTERIOR
"""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from anthropic import AsyncAnthropic

from app.config import get_settings
from app.models.database import SessionLocal, LeadState, Conversation, Lead
from app.services.twilio_client import twilio_service

settings = get_settings()
logger = logging.getLogger(__name__)

BRASILIA = timezone(timedelta(hours=-3))

def agora_brasilia() -> datetime:
    return datetime.now(BRASILIA).replace(tzinfo=None)

def utcnow() -> datetime:
    return datetime.utcnow()

JANELAS_COMERCIAIS = [
    (time(8, 0),   time(12, 0)),
    (time(13, 30), time(18, 0)),
]

FOLLOWUP_MINUTOS = [30, 120, 300, 1440, 2880]
INTERVALO_MINIMO_ENTRE_STEPS = 25
MINUTOS_FECHAR = 4320

_anthropic = (
    AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    if getattr(settings, "ANTHROPIC_API_KEY", "stub") != "stub"
    else None
)


def esta_em_horario_comercial(agora: Optional[datetime] = None) -> bool:
    t = (agora or agora_brasilia()).time()
    return any(ini <= t <= fim for ini, fim in JANELAS_COMERCIAIS)


def proxima_janela_comercial(agora: Optional[datetime] = None) -> datetime:
    agora = agora or agora_brasilia()
    t = agora.time()
    for ini, fim in JANELAS_COMERCIAIS:
        if t < ini:
            return agora.replace(hour=ini.hour, minute=ini.minute, second=0, microsecond=0)
    return (agora + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


INSTRUCOES_POR_STEP = {
    1: """Step 1 — 30 minutos sem resposta.
Tom: leve, natural, zero pressão.
Objetivo: verificar se ficou alguma dúvida.
Formato: 1 pergunta direta relacionada ao que foi discutido.
Máximo 2 linhas.""",

    2: """Step 2 — 2 horas sem resposta.
Tom: consultivo, agrega valor.
Objetivo: trazer UM dado concreto do histórico — use apenas números que o cliente mencionou.
Não invente valores. Se o cliente não mencionou volume ou preço, não calcule nada.
Máximo 3 linhas. Termina com CTA direto.""",

    3: """Step 3 — 5 horas sem resposta.
Tom: consultivo, abre nova possibilidade.
Objetivo: novo ângulo baseado no que foi discutido.
Máximo 3 linhas. Termina com uma pergunta.""",

    4: """Step 4 — 24 horas sem resposta.
Tom: humano, direto, sem pressão.
Objetivo: reengajar de forma genuína.
Máximo 2 linhas.""",

    5: """Step 5 — 48 horas sem resposta. Última tentativa.
Tom: direto, humano.
Objetivo: perguntar se ainda faz sentido conversar.
Máximo 2 linhas.""",
}

FALLBACKS = {
    1: "Ficou alguma dúvida sobre o que conversamos?",
    2: "Se quiser retomar, posso detalhar melhor qualquer ponto da nossa conversa.",
    3: "Tem algum outro aspecto que você gostaria de entender melhor antes de decidir?",
    4: "Só passando para ver se posso ajudar com mais alguma informação.",
    5: "Vou deixar em aberto. Quando quiser retomar, é só chamar.",
}


async def _gerar_mensagem_followup(step: int, nome: str, produto: str, historico: str) -> str:
    if not _anthropic:
        return FALLBACKS.get(step, FALLBACKS[1])

    instrucao   = INSTRUCOES_POR_STEP.get(step, INSTRUCOES_POR_STEP[1])
    nome_str    = f"O nome do cliente é {nome}. " if nome else ""
    produto_str = f"O produto discutido foi {produto}. " if produto else ""

    system = (
        "Você é Bruno, consultor comercial da Doss Group (equipamentos de impressão digital). "
        "Escreva APENAS o texto da mensagem de WhatsApp — sem aspas, sem explicação, sem introdução. "
        "ZERO emojis. ZERO traços (—). ZERO 'estou à disposição'. "
        "REGRA CRÍTICA: NUNCA invente dados, números, marcas, especificações ou percentuais. "
        "Use APENAS informações que aparecem explicitamente no histórico abaixo. "
        "Se o cliente não mencionou um dado, não o mencione. Prefira mensagem genérica a dado inventado. "
        "NUNCA mencione marcas de equipamentos que não apareçam explicitamente no histórico. "
        "NUNCA invente resultados de outros clientes ou regiões. "
        "Cada follow-up deve ter abordagem diferente dos anteriores — não repita o que já foi dito."
    )

    user = (
        f"{nome_str}{produto_str}\n"
        f"INSTRUÇÃO DO STEP:\n{instrucao}\n\n"
        f"HISTÓRICO DA CONVERSA:\n"
        f"{historico[-1200:] if historico else '(sem histórico disponível)'}"
    )

    try:
        response = await asyncio.wait_for(
            _anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=120,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=15.0,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"[FOLLOWUP] Erro ao gerar step {step}: {e}")
        return FALLBACKS.get(step, FALLBACKS[1])


async def _processar_lead_followup(db, lead_state: LeadState):
    agora_utc = utcnow()

    if lead_state.stage in ("closed", "followup_closed"):
        return

    ultima_msg_cliente = (
        db.query(Conversation)
        .filter(
            Conversation.phone == lead_state.phone,
            Conversation.role == "user",
            ~Conversation.content.like("[%"),
        )
        .order_by(Conversation.created_at.desc())
        .first()
    )

    if not ultima_msg_cliente:
        return

    minutos_inativo = (agora_utc - ultima_msg_cliente.created_at).total_seconds() / 60
    step_atual = lead_state.followup_step or 0

    if minutos_inativo >= MINUTOS_FECHAR and step_atual >= len(FOLLOWUP_MINUTOS):
        lead_state.stage = "followup_closed"
        db.commit()
        logger.info(f"[FOLLOWUP] {lead_state.phone} encerrado (72h).")
        return

    if step_atual >= len(FOLLOWUP_MINUTOS):
        return

    if minutos_inativo < FOLLOWUP_MINUTOS[step_atual]:
        return

    if lead_state.followup_sent_at:
        minutos_desde_ultimo = (agora_utc - lead_state.followup_sent_at).total_seconds() / 60
        if minutos_desde_ultimo < INTERVALO_MINIMO_ENTRE_STEPS:
            return

    if not esta_em_horario_comercial(agora_brasilia()):
        return

    lead = db.query(Lead).filter(Lead.phone == lead_state.phone).first()

    # Parser de nome — palavras que NÃO são nomes
    PALAVRAS_NAO_NOME = {
        "oi","ola","olá","opa","eae","sim","nao","ok","tudo","bom","boa",
        "olha","hey","posso","mais","sobre","isso","gostaria","queria",
        "preciso","quero","tenho","busco","vim","sou","meu","minha",
        "sublimacao","dtf","plotter","maquina","tinta","impressora","eco",
        "pode","como","qual","quando","onde","quanto","que","para","com",
        "uma","um","ter","informações","informacoes","informacao",
    }

    nome = (lead.name or "") if lead else ""
    if not nome or nome == lead_state.phone:
        primeiras = (
            db.query(Conversation)
            .filter(Conversation.phone == lead_state.phone, Conversation.role == "user")
            .order_by(Conversation.created_at.asc())
            .limit(8)
            .all()
        )
        for msg in primeiras:
            txt = msg.content.strip()
            if txt.startswith("["):
                continue
            palavras = txt.split()
            for palavra in palavras:
                p = palavra.strip("!?,.:;").lower()
                if (len(p) > 2
                    and p.replace("-","").isalpha()
                    and p not in PALAVRAS_NAO_NOME
                    and palavra[0].isupper()):
                    nome = palavra.strip("!?,.:;").capitalize()
                    break
            if nome:
                break

    historico_msgs = (
        db.query(Conversation)
        .filter(Conversation.phone == lead_state.phone)
        .order_by(Conversation.created_at.asc())
        .limit(30)
        .all()
    )
    PREFIXOS_IGNORAR = ("[SISTEMA", "[CAMPANHA", "[FOLLOWUP")
    historico_txt = "\n".join(
        f"{'Cliente' if m.role == 'user' else 'Bruno'}: {m.content[:250]}"
        for m in historico_msgs
        if not any(m.content.startswith(p) for p in PREFIXOS_IGNORAR)
    )

    PRODUTO_MAP = {
        "1908": "Plotter DG 1908i", "3204": "Plotter DG 3204i",
        "3202": "Plotter DG 3202i", "1904": "Plotter DG 1904i",
        "1802": "Plotter DG 1802i", "1801": "Plotter DG 1801i",
        "dtf uv": "DTF UV",         "dtf": "DTF Têxtil",
        "flatbed": "Flatbed UV",    "jinka": "Plotter de Recorte",
        "laser": "Laser",           "eco solvente": "Eco Solvente",
        "sublimacao": "Sublimática", "dgtex": "Tinta DGtex",
        "dgeco": "Tinta DGeco",
    }
    produto = ""
    hist_lower = historico_txt.lower()
    for kw, prod in PRODUTO_MAP.items():
        if kw in hist_lower:
            produto = prod
            break

    step_numero = step_atual + 1
    logger.info(f"[FOLLOWUP] Step {step_numero} para {lead_state.phone} ({minutos_inativo:.0f}min inativo)")

    mensagem = await _gerar_mensagem_followup(step_numero, nome, produto, historico_txt)
    await twilio_service.send_whatsapp_message(lead_state.phone, mensagem)

    db.add(Conversation(
        phone=lead_state.phone,
        role="assistant",
        content=f"[FOLLOWUP-{step_numero}] {mensagem}"
    ))

    lead_state.followup_step    = step_numero
    lead_state.followup_sent_at = agora_utc
    db.commit()

    logger.info(f"[FOLLOWUP] Step {step_numero} enviado para {lead_state.phone}: {mensagem[:80]}...")


async def _loop_followup():
    logger.info("[FOLLOWUP] Serviço iniciado.")
    while True:
        try:
            db = SessionLocal()
            try:
                leads_ativos = (
                    db.query(LeadState)
                    .filter(LeadState.stage.notin_(["closed", "followup_closed"]))
                    .all()
                )
                for ls in leads_ativos:
                    try:
                        await _processar_lead_followup(db, ls)
                    except Exception as e:
                        logger.error(f"[FOLLOWUP] Erro no lead {ls.phone}: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[FOLLOWUP] Erro no loop: {e}")

        await asyncio.sleep(300)


def resetar_followup(phone: str):
    db = SessionLocal()
    try:
        ls = db.query(LeadState).filter(LeadState.phone == phone).first()
        if ls and (ls.followup_step or 0) > 0:
            logger.info(f"[FOLLOWUP] {phone} retornou. Ciclo reiniciado.")
            ls.followup_step    = 0
            ls.followup_sent_at = None
            if ls.stage == "followup_closed":
                ls.stage = "active"
            db.commit()
    except Exception as e:
        logger.error(f"[FOLLOWUP] Erro ao resetar {phone}: {e}")
    finally:
        db.close()


followup_service_task: asyncio.Task = None


def start_followup_service():
    global followup_service_task
    followup_service_task = asyncio.create_task(_loop_followup())
    logger.info("[FOLLOWUP] Task iniciada com sucesso.")
