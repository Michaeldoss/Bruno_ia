"""Follow-up automático do Bruno IA, com trava de handoff e janela WhatsApp."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.models.database import SessionLocal, LeadState, Conversation, Lead
from app.services.twilio_client import twilio_service
from app.services.crm_inbox_client import human_active_recently
from app.services.followup_pipeline_client import enviar_para_pipeline_followup

settings = get_settings()
logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo(getattr(settings, "TIMEZONE", "America/Sao_Paulo"))


def agora_local() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def utcnow() -> datetime:
    return datetime.utcnow()


JANELAS_COMERCIAIS = [(time(8, 0), time(12, 0)), (time(13, 30), time(18, 0))]
FOLLOWUP_MINUTOS = [30, 120, 300, 1440, 2880]
INTERVALO_MINIMO_ENTRE_STEPS = 25
MINUTOS_FECHAR = 4320

_anthropic = (
    AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    if getattr(settings, "ANTHROPIC_API_KEY", "stub") != "stub"
    else None
)


def esta_em_horario_comercial(agora: Optional[datetime] = None) -> bool:
    atual = agora or agora_local()
    if atual.weekday() >= 5:
        return False
    return any(inicio <= atual.time() <= fim for inicio, fim in JANELAS_COMERCIAIS)


def proxima_janela_comercial(agora: Optional[datetime] = None) -> datetime:
    atual = agora or agora_local()
    while atual.weekday() >= 5:
        atual = (atual + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    for inicio, _ in JANELAS_COMERCIAIS:
        if atual.time() < inicio:
            return atual.replace(hour=inicio.hour, minute=inicio.minute, second=0, microsecond=0)
    proximo = (atual + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    while proximo.weekday() >= 5:
        proximo += timedelta(days=1)
    return proximo


INSTRUCOES_POR_STEP = {
    1: "Step 1 — 30 minutos sem resposta. Tom leve, natural, zero pressão. Faça uma pergunta direta. Máximo 2 linhas.",
    2: "Step 2 — 2 horas sem resposta. Traga um dado real do histórico, sem inventar. Máximo 3 linhas e CTA direto.",
    3: "Step 3 — 5 horas sem resposta. Abra um novo ângulo baseado no histórico. Máximo 3 linhas e termine com pergunta.",
    4: "Step 4 — 24 horas sem resposta. Reengaje de forma humana e direta. Máximo 2 linhas.",
    5: "Step 5 — 48 horas sem resposta. Última tentativa, sem pressão. Máximo 2 linhas.",
}

FALLBACKS = {
    1: "Ficou alguma dúvida sobre o que conversamos?",
    2: "Posso detalhar melhor algum ponto da nossa conversa?",
    3: "Tem algum outro aspecto que você gostaria de entender antes de decidir?",
    4: "Só passando para saber se ainda posso ajudar com alguma informação.",
    5: "Vou deixar em aberto. Quando quiser retomar, é só chamar.",
}


async def _gerar_mensagem_followup(step: int, nome: str, produto: str, historico: str) -> str:
    if not _anthropic:
        return FALLBACKS.get(step, FALLBACKS[1])
    system = (
        "Você é Bruno, consultor comercial da Doss Group. Escreva apenas a mensagem de WhatsApp. "
        "Sem emojis, sem traços, sem inventar dados, números, marcas ou resultados. Use apenas o histórico."
    )
    user = (
        f"Nome: {nome or 'não informado'}\nProduto: {produto or 'não identificado'}\n"
        f"Instrução: {INSTRUCOES_POR_STEP.get(step)}\nHistórico:\n{historico[-1200:] or '(vazio)'}"
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
        texto = response.content[0].text.strip() if response.content else ""
        return texto or FALLBACKS.get(step, FALLBACKS[1])
    except Exception as exc:
        logger.error("[FOLLOWUP] Falha ao gerar step %s: %s", step, exc)
        return FALLBACKS.get(step, FALLBACKS[1])


async def _enviar_followup(phone: str, step: int, mensagem: str) -> str:
    """Retorna SID real. Steps 4/5 exigem template aprovado."""
    if step == 4:
        content_sid = getattr(settings, "FOLLOWUP_TEMPLATE_STEP_4", "")
        if not content_sid:
            raise RuntimeError("FOLLOWUP_TEMPLATE_STEP_4 não configurado")
        return await twilio_service.send_whatsapp_template_message(
            phone, content_sid, {"1": mensagem}
        )
    if step == 5:
        content_sid = getattr(settings, "FOLLOWUP_TEMPLATE_STEP_5", "")
        if not content_sid:
            raise RuntimeError("FOLLOWUP_TEMPLATE_STEP_5 não configurado")
        return await twilio_service.send_whatsapp_template_message(
            phone, content_sid, {"1": mensagem}
        )
    return await twilio_service.send_whatsapp_message(phone, mensagem)


def _montar_contexto_followup(db, lead_state: LeadState) -> tuple:
    lead = db.query(Lead).filter(Lead.phone == lead_state.phone).first()
    nome = (lead.name or "") if lead else ""
    if nome == lead_state.phone:
        nome = ""

    historico_msgs = (
        db.query(Conversation)
        .filter(Conversation.phone == lead_state.phone)
        .order_by(Conversation.created_at.asc())
        .limit(100)
        .all()
    )
    prefixos = ("[SISTEMA", "[CAMPANHA")
    historico_txt = "\n".join(
        f"{'Cliente' if m.role == 'user' else 'Bruno'}: {m.content[:250]}"
        for m in historico_msgs
        if m.content and not any(m.content.startswith(p) for p in prefixos)
    )

    produto_map = {
        "1908": "Plotter DG 1908i", "3204": "Plotter DG 3204i",
        "3202": "Plotter DG 3202i", "1904": "Plotter DG 1904i",
        "1802": "Plotter DG 1802i", "1801": "Plotter DG 1801i",
        "dtf uv": "DTF UV", "dtf": "DTF Têxtil", "flatbed": "Flatbed UV",
        "jinka": "Plotter de Recorte", "laser": "Laser",
        "eco solvente": "Eco Solvente", "sublimacao": "Sublimática",
        "dgtex": "Tinta DGtex", "dgeco": "Tinta DGeco",
    }
    hist_lower = historico_txt.lower()
    produto = next((v for k, v in produto_map.items() if k in hist_lower), "")
    mensagens = [
        {
            "content": m.content,
            "is_from_contact": m.role == "user",
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in historico_msgs
        if m.content
    ]
    return nome, produto, historico_txt, mensagens


async def _encaminhar_followup_encerrado(db, lead_state: LeadState):
    nome, produto, historico_txt, mensagens = _montar_contexto_followup(db, lead_state)
    resumo = (
        "Cliente não respondeu à régua automática completa do Bruno.\n\n"
        f"Etapa alcançada: FU-{lead_state.followup_step or 0}.\n"
        f"Histórico recente:\n{historico_txt[-2500:]}"
    )
    resultado = await enviar_para_pipeline_followup(
        phone=lead_state.phone,
        nome=nome,
        produto=produto,
        resumo=resumo,
        mensagens=mensagens,
        ultimo_followup_em=(
            lead_state.followup_sent_at.isoformat()
            if lead_state.followup_sent_at
            else None
        ),
    )
    if not resultado.get("success"):
        raise RuntimeError("CRM não confirmou movimentação para a etapa Follow-up")

    lead_state.stage = "followup_closed"
    db.commit()
    logger.info(
        "[FOLLOWUP] %s movido para Pipeline/Follow-up após 72h sem resposta. card=%s",
        lead_state.phone,
        resultado.get("pipeline_lead_id"),
    )


async def _processar_lead_followup(db, lead_state: LeadState):
    agora_utc = utcnow()
    if lead_state.stage in ("closed", "followup_closed"):
        return

    if await human_active_recently(lead_state.phone):
        logger.info("[FOLLOWUP] Humano ativo para %s; ciclo suspenso.", lead_state.phone)
        lead_state.stage = "closed"
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

    minutos_inativo = (agora_utc - ultima_msg_cliente.created_at).total_seconds() / 60
    step_atual = lead_state.followup_step or 0

    if minutos_inativo >= MINUTOS_FECHAR and step_atual >= len(FOLLOWUP_MINUTOS):
        await _encaminhar_followup_encerrado(db, lead_state)
        return
    if step_atual >= len(FOLLOWUP_MINUTOS) or minutos_inativo < FOLLOWUP_MINUTOS[step_atual]:
        return
    if lead_state.followup_sent_at:
        desde_ultimo = (agora_utc - lead_state.followup_sent_at).total_seconds() / 60
        if desde_ultimo < INTERVALO_MINIMO_ENTRE_STEPS:
            return
    if not esta_em_horario_comercial():
        return

    nome, produto, historico_txt, _ = _montar_contexto_followup(db, lead_state)
    step_numero = step_atual + 1
    mensagem = await _gerar_mensagem_followup(step_numero, nome, produto, historico_txt)
    sid = await _enviar_followup(lead_state.phone, step_numero, mensagem)
    if not sid:
        raise RuntimeError(f"Twilio não confirmou envio do follow-up step {step_numero}")

    db.add(Conversation(
        phone=lead_state.phone,
        role="assistant",
        content=f"[FOLLOWUP-{step_numero}] {mensagem}",
    ))
    lead_state.followup_step = step_numero
    lead_state.followup_sent_at = agora_utc
    db.commit()
    logger.info("[FOLLOWUP] Step %s enviado para %s. SID=%s", step_numero, lead_state.phone, sid)


async def _loop_followup():
    logger.info("[FOLLOWUP] Serviço iniciado.")
    while True:
        try:
            db = SessionLocal()
            try:
                leads_ativos = db.query(LeadState).filter(
                    LeadState.stage.notin_(["closed", "followup_closed"])
                ).all()
                for lead_state in leads_ativos:
                    try:
                        await _processar_lead_followup(db, lead_state)
                    except Exception as exc:
                        db.rollback()
                        logger.error("[FOLLOWUP] Erro no lead %s: %s", lead_state.phone, exc, exc_info=True)
            finally:
                db.close()
        except Exception as exc:
            logger.error("[FOLLOWUP] Erro no loop: %s", exc, exc_info=True)
        await asyncio.sleep(300)


def resetar_followup(phone: str):
    db = SessionLocal()
    try:
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if not lead_state:
            return
        lead_state.followup_step = 0
        lead_state.followup_sent_at = None
        if lead_state.stage == "followup_closed":
            lead_state.stage = "active"
        db.commit()
        logger.info("[FOLLOWUP] Ciclo reiniciado para %s.", phone)
    except Exception as exc:
        db.rollback()
        logger.error("[FOLLOWUP] Erro ao resetar %s: %s", phone, exc)
    finally:
        db.close()


followup_service_task: asyncio.Task = None


def start_followup_service():
    global followup_service_task
    if followup_service_task and not followup_service_task.done():
        logger.info("[FOLLOWUP] Task já está ativa; não será duplicada.")
        return
    followup_service_task = asyncio.create_task(_loop_followup())
    logger.info("[FOLLOWUP] Task iniciada com sucesso.")
