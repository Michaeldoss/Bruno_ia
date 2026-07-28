"""
Follow-up automático do Bruno IA.

Regra principal: o Bruno só faz follow-up enquanto o lead continua sob
responsabilidade dele. Se já existe card no pipeline, a conversa foi
transferida para humano ou foi encerrada no CRM, o ciclo é encerrado e
nenhuma nova mensagem é enviada.
"""

import asyncio
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import httpx
from anthropic import AsyncAnthropic

from app.config import get_settings
from app.models.database import SessionLocal, LeadState, Conversation, Lead
from app.services.twilio_client import twilio_service

settings = get_settings()
logger = logging.getLogger(__name__)

BRASILIA = timezone(timedelta(hours=-3))
ORG_ID = "dafa7ea5-08c4-44dd-886e-d58905fca38c"
BRUNO_AGENT_ID = "31b8f1f2-b509-4319-abfb-989954b3ba25"
SUPABASE_URL = getattr(settings, "SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

JANELAS_COMERCIAIS = [
    (time(8, 0), time(12, 0)),
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


def agora_brasilia() -> datetime:
    return datetime.now(BRASILIA).replace(tzinfo=None)


def utcnow() -> datetime:
    return datetime.utcnow()


def esta_em_horario_comercial(agora: Optional[datetime] = None) -> bool:
    atual = (agora or agora_brasilia()).time()
    return any(inicio <= atual <= fim for inicio, fim in JANELAS_COMERCIAIS)


def proxima_janela_comercial(agora: Optional[datetime] = None) -> datetime:
    agora = agora or agora_brasilia()
    atual = agora.time()
    for inicio, _ in JANELAS_COMERCIAIS:
        if atual < inicio:
            return agora.replace(hour=inicio.hour, minute=inicio.minute, second=0, microsecond=0)
    return (agora + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _normalizar_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or "").replace("whatsapp:", ""))


async def _lead_ja_entregue_ao_crm(phone: str) -> bool:
    """Retorna True quando o Bruno não deve mais fazer follow-up.

    Bloqueia quando:
    1. existe conversa encerrada/resolvida no CRM;
    2. a conversa está atribuída a agente humano;
    3. já existe card ativo ou concluído no pipeline para o contato.

    Em falha de rede, não envia follow-up por segurança. É melhor atrasar
    uma mensagem do que incomodar um cliente já transferido.
    """
    phone_clean = _normalizar_phone(phone)
    if not phone_clean or not SUPABASE_URL or not SUPABASE_KEY:
        return False

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            contatos = await client.get(
                f"{SUPABASE_URL}/rest/v1/contacts",
                params={
                    "org_id": f"eq.{ORG_ID}",
                    "phone": f"eq.{phone_clean}",
                    "select": "id",
                    "limit": 1,
                },
                headers=_headers(),
            )
            if contatos.status_code != 200:
                logger.error("[FOLLOWUP] CRM indisponível ao consultar contato %s", phone_clean)
                return True

            contato_rows = contatos.json() if isinstance(contatos.json(), list) else []
            contact_id = contato_rows[0].get("id") if contato_rows else None

            conversas = await client.get(
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={
                    "org_id": f"eq.{ORG_ID}",
                    "whatsapp_phone": f"eq.{phone_clean}",
                    "select": "id,status,agent_id,whatsapp_instance",
                    "order": "created_at.desc",
                    "limit": 10,
                },
                headers=_headers(),
            )
            if conversas.status_code != 200:
                logger.error("[FOLLOWUP] CRM indisponível ao consultar conversa %s", phone_clean)
                return True

            rows = conversas.json() if isinstance(conversas.json(), list) else []
            for conv in rows:
                status = str(conv.get("status") or "").lower()
                agent_id = conv.get("agent_id")
                if status in {"resolved", "closed", "finalized", "finalizado"}:
                    logger.info("[FOLLOWUP] %s já encerrado no CRM", phone_clean)
                    return True
                if agent_id and agent_id != BRUNO_AGENT_ID:
                    logger.info("[FOLLOWUP] %s já atribuído a agente humano", phone_clean)
                    return True

            if contact_id:
                pipeline = await client.get(
                    f"{SUPABASE_URL}/rest/v1/pipeline_leads",
                    params={
                        "contact_id": f"eq.{contact_id}",
                        "select": "id,status,owner_id",
                        "limit": 10,
                    },
                    headers=_headers(),
                )
                if pipeline.status_code != 200:
                    logger.error("[FOLLOWUP] CRM indisponível ao consultar pipeline %s", phone_clean)
                    return True

                cards = pipeline.json() if isinstance(pipeline.json(), list) else []
                if cards:
                    logger.info("[FOLLOWUP] %s já possui card no pipeline; ciclo encerrado", phone_clean)
                    return True

            return False
    except Exception as exc:
        logger.error("[FOLLOWUP] Falha ao validar handoff de %s: %s", phone_clean, exc)
        return True


INSTRUCOES_POR_STEP = {
    1: "30 minutos sem resposta. Faça uma pergunta curta e útil, sem repetir a última pergunta.",
    2: "2 horas sem resposta. Traga um ponto novo e concreto do histórico, sem inventar dados.",
    3: "5 horas sem resposta. Abra um novo ângulo e termine com uma pergunta curta.",
    4: "24 horas sem resposta. Reengaje de forma humana, direta e sem pressão.",
    5: "48 horas sem resposta. Última tentativa, curta e respeitosa.",
}

FALLBACKS = {
    1: "Ficou alguma dúvida que eu possa esclarecer?",
    2: "Posso detalhar melhor algum ponto para ajudar na sua decisão?",
    3: "Existe alguma informação que ainda falta para você avançar?",
    4: "Quer que eu retome este atendimento com você?",
    5: "Caso ainda faça sentido, me responda por aqui e retomamos.",
}


async def _gerar_mensagem_followup(step: int, nome: str, produto: str, historico: str) -> str:
    if not _anthropic:
        return FALLBACKS.get(step, FALLBACKS[1])

    system = (
        "Você é Bruno, consultor comercial da Doss Group. "
        "Escreva somente a mensagem de WhatsApp, sem título, sem marcador interno e sem explicação. "
        "Não use travessão. Não repita perguntas já feitas. Não diga que vai transferir se isso já foi dito. "
        "Não faça follow-up de atendimento qualificado ou entregue ao vendedor. "
        "Use apenas informações explícitas do histórico. Máximo de duas frases."
    )
    user = (
        f"Nome: {nome or 'não informado'}\n"
        f"Produto: {produto or 'não identificado'}\n"
        f"Objetivo deste follow-up: {INSTRUCOES_POR_STEP.get(step)}\n\n"
        f"Histórico:\n{historico[-1400:] if historico else '(sem histórico)'}"
    )

    try:
        response = await asyncio.wait_for(
            _anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=15.0,
        )
        texto = response.content[0].text.strip()
        return texto or FALLBACKS.get(step, FALLBACKS[1])
    except Exception as exc:
        logger.error("[FOLLOWUP] Erro ao gerar step %s: %s", step, exc)
        return FALLBACKS.get(step, FALLBACKS[1])


async def _processar_lead_followup(db, lead_state: LeadState):
    if lead_state.stage in ("closed", "followup_closed"):
        return

    if await _lead_ja_entregue_ao_crm(lead_state.phone):
        lead_state.stage = "closed"
        lead_state.followup_sent_at = None
        db.commit()
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

    agora_utc = utcnow()
    minutos_inativo = (agora_utc - ultima_msg_cliente.created_at).total_seconds() / 60
    step_atual = lead_state.followup_step or 0

    if minutos_inativo >= MINUTOS_FECHAR and step_atual >= len(FOLLOWUP_MINUTOS):
        lead_state.stage = "followup_closed"
        db.commit()
        return
    if step_atual >= len(FOLLOWUP_MINUTOS):
        return
    if minutos_inativo < FOLLOWUP_MINUTOS[step_atual]:
        return
    if lead_state.followup_sent_at:
        desde_ultimo = (agora_utc - lead_state.followup_sent_at).total_seconds() / 60
        if desde_ultimo < INTERVALO_MINIMO_ENTRE_STEPS:
            return
    if not esta_em_horario_comercial():
        return

    lead = db.query(Lead).filter(Lead.phone == lead_state.phone).first()
    nome = (lead.name or "") if lead else ""

    historico_msgs = (
        db.query(Conversation)
        .filter(Conversation.phone == lead_state.phone)
        .order_by(Conversation.created_at.asc())
        .limit(30)
        .all()
    )
    prefixos_ignorar = ("[SISTEMA", "[CAMPANHA", "[FOLLOWUP")
    historico = "\n".join(
        f"{'Cliente' if m.role == 'user' else 'Bruno'}: {m.content[:250]}"
        for m in historico_msgs
        if m.content and not any(m.content.startswith(p) for p in prefixos_ignorar)
    )

    produto = ""
    hist_lower = historico.lower()
    produto_map = {
        "dgtex": "Tinta DGtex",
        "dtf uv": "DTF UV",
        "dtf": "DTF Têxtil",
        "eco solvente": "Eco Solvente",
        "sublimacao": "Sublimática",
        "plotter": "Plotter",
    }
    for termo, descricao in produto_map.items():
        if termo in hist_lower:
            produto = descricao
            break

    step_numero = step_atual + 1
    mensagem = await _gerar_mensagem_followup(step_numero, nome, produto, historico)

    # Verificação final imediatamente antes do envio. Evita corrida entre
    # a transferência no CRM e o ciclo de cinco minutos do follow-up.
    if await _lead_ja_entregue_ao_crm(lead_state.phone):
        lead_state.stage = "closed"
        lead_state.followup_sent_at = None
        db.commit()
        return

    await twilio_service.send_whatsapp_message(lead_state.phone, mensagem)
    db.add(Conversation(
        phone=lead_state.phone,
        role="assistant",
        content=f"[FOLLOWUP-{step_numero}] {mensagem}",
    ))
    lead_state.followup_step = step_numero
    lead_state.followup_sent_at = agora_utc
    db.commit()


async def _loop_followup():
    logger.info("[FOLLOWUP] Serviço iniciado com trava de handoff do CRM.")
    while True:
        try:
            db = SessionLocal()
            try:
                leads_ativos = (
                    db.query(LeadState)
                    .filter(LeadState.stage.notin_(["closed", "followup_closed"]))
                    .all()
                )
                for lead_state in leads_ativos:
                    try:
                        await _processar_lead_followup(db, lead_state)
                    except Exception as exc:
                        logger.error("[FOLLOWUP] Erro no lead %s: %s", lead_state.phone, exc)
            finally:
                db.close()
        except Exception as exc:
            logger.error("[FOLLOWUP] Erro no loop: %s", exc)
        await asyncio.sleep(300)


def resetar_followup(phone: str):
    db = SessionLocal()
    try:
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if lead_state and (lead_state.followup_step or 0) > 0:
            lead_state.followup_step = 0
            lead_state.followup_sent_at = None
            if lead_state.stage == "followup_closed":
                lead_state.stage = "active"
            db.commit()
    except Exception as exc:
        logger.error("[FOLLOWUP] Erro ao resetar %s: %s", phone, exc)
    finally:
        db.close()


followup_service_task: asyncio.Task = None


def start_followup_service():
    global followup_service_task
    followup_service_task = asyncio.create_task(_loop_followup())
    logger.info("[FOLLOWUP] Task iniciada com sucesso.")
