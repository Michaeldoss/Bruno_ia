"""
Follow-up automático do Bruno IA
─────────────────────────────────
Todos os tempos contados a partir da ÚLTIMA mensagem do cliente:

  Step 1 →  30min  → leve, verifica se ficou dúvida
  Step 2 →   2h    → argumento técnico novo (custo/m², velocidade, ROI)
  Step 3 →   5h    → mostrar outro produto ou aprofundar conversa
  Step 4 →  24h    → reengajamento consultivo com perspectiva nova
  Step 5 →  48h    → última tentativa, direta e humana
  Encerra →  72h   → fecha por inatividade

Regras:
- Follow-ups APENAS dentro do horário comercial: 08:00-12:00 / 13:30-18:00
- Horário baseado em Brasília (UTC-3) independente do servidor
- Se o cliente responder, ciclo reinicia do zero
- Cada step só dispara após intervalo mínimo desde o STEP ANTERIOR (evita cascata)
- Cada mensagem é gerada pelo Claude Haiku com base no histórico real
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

# ── Timezone Brasília ──────────────────────────────────────────────────────
BRASILIA = timezone(timedelta(hours=-3))

def agora_brasilia() -> datetime:
    """Retorna datetime atual no horário de Brasília, sem timezone info."""
    return datetime.now(BRASILIA).replace(tzinfo=None)

def utcnow() -> datetime:
    """Retorna datetime UTC atual para comparação com banco."""
    return datetime.utcnow()

# ── Horário comercial ──────────────────────────────────────────────────────
JANELAS_COMERCIAIS = [
    (time(8, 0),   time(12, 0)),   # 08:00 - 12:00
    (time(13, 30), time(18, 0)),   # 13:30 - 18:00
]

# ── Intervalos por step (em minutos desde última msg do CLIENTE) ───────────
FOLLOWUP_MINUTOS = [30, 120, 300, 1440, 2880]   # 30min, 2h, 5h, 24h, 48h

# ── Intervalo mínimo entre steps (evita cascata em reinicializações) ───────
# Cada step só dispara se passou pelo menos este tempo desde o step anterior
INTERVALO_MINIMO_ENTRE_STEPS = 25  # minutos

MINUTOS_FECHAR = 4320  # 72h após última msg = encerra

# ── Cliente Anthropic ──────────────────────────────────────────────────────
_anthropic = (
    AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    if getattr(settings, "ANTHROPIC_API_KEY", "stub") != "stub"
    else None
)


# ── Helpers de horário ─────────────────────────────────────────────────────

def esta_em_horario_comercial(agora: Optional[datetime] = None) -> bool:
    """Verifica se está em horário comercial usando horário de Brasília."""
    t = (agora or agora_brasilia()).time()
    return any(ini <= t <= fim for ini, fim in JANELAS_COMERCIAIS)


def proxima_janela_comercial(agora: Optional[datetime] = None) -> datetime:
    agora = agora or agora_brasilia()
    t = agora.time()
    for ini, fim in JANELAS_COMERCIAIS:
        if t < ini:
            return agora.replace(hour=ini.hour, minute=ini.minute, second=0, microsecond=0)
    return (agora + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


# ── Instruções por step ────────────────────────────────────────────────────

INSTRUCOES_POR_STEP = {
    1: """Step 1 — 30 minutos sem resposta.
Tom: leve, natural, zero pressão.
Objetivo: verificar se ficou alguma dúvida sem mencionar que o cliente sumiu.
Formato: 1 pergunta direta relacionada ao que foi discutido.
Máximo 2 linhas.""",

    2: """Step 2 — 2 horas sem resposta.
Tom: consultivo, técnico, agrega valor.
Objetivo: trazer UM dado concreto que o cliente ainda não tem — custo por m², payback, velocidade, margem por peça.
Use os números da conversa se o cliente mencionou volume ou preço.
Não repita o que já foi dito. Traga informação nova.
Máximo 3 linhas. Termina com CTA direto.""",

    3: """Step 3 — 5 horas sem resposta.
Tom: consultivo, abre nova possibilidade.
Objetivo: mostrar outro ângulo — produto complementar, aplicação diferente, ou pergunta sobre negócio ainda não feita.
Não force a venda. Abra conversa nova dentro do mesmo contexto.
Máximo 3 linhas. Termina com uma pergunta.""",

    4: """Step 4 — 24 horas sem resposta.
Tom: humano, direto, sem pressão.
Objetivo: reengajar com algo genuinamente útil.
Não mencione que faz 24h. Escreva como mensagem natural de acompanhamento.
Máximo 2 linhas.""",

    5: """Step 5 — 48 horas sem resposta. Última tentativa.
Tom: direto, humano, sem desespero.
Objetivo: perguntar de forma simples se ainda faz sentido conversar.
Diga que vai deixar em aberto.
Não use "urgente" ou "última chance".
Máximo 2 linhas.""",
}

FALLBACKS = {
    1: "Ficou alguma dúvida sobre o que conversamos? Pode me perguntar.",
    2: "Só um dado: a maioria dos nossos clientes recupera o investimento em menos de 12 meses com produção interna. Quer que eu simule para o seu volume?",
    3: "Além do equipamento que conversamos, temos outras soluções que podem complementar sua operação. Qual é o produto que você mais produz hoje?",
    4: "Só passando para ver como você está. Se quiser retomar de onde paramos, pode me chamar.",
    5: "Vou deixar em aberto. Quando quiser retomar, é só chamar — ou posso pedir para nosso consultor entrar em contato.",
}


async def _gerar_mensagem_followup(step: int, nome: str, produto: str, historico: str) -> str:
    if not _anthropic:
        msg = FALLBACKS.get(step, FALLBACKS[1])
        if nome and step in (4, 5):
            msg = f"{nome}, " + msg[0].lower() + msg[1:]
        return msg

    instrucao   = INSTRUCOES_POR_STEP.get(step, INSTRUCOES_POR_STEP[1])
    nome_str    = f"O nome do cliente é {nome}. " if nome else ""
    produto_str = f"O produto de interesse discutido foi {produto}. " if produto else ""

    system = (
        "Você é Bruno, consultor comercial sênior da Doss Group (equipamentos de impressão digital). "
        "Escreva APENAS o texto da mensagem de WhatsApp — sem aspas, sem explicação, sem introdução. "
        "ZERO emojis. ZERO 'boa pergunta'. ZERO 'estou à disposição'. "
        "Cada follow-up deve ter abordagem DIFERENTE dos anteriores. "
        "Use o histórico para personalizar — mencione algo específico do que foi discutido."
    )

    user = (
        f"{nome_str}{produto_str}\n"
        f"INSTRUÇÃO DO STEP:\n{instrucao}\n\n"
        f"HISTÓRICO DA CONVERSA (use para personalizar):\n"
        f"{historico[-1000:] if historico else '(sem histórico disponível)'}"
    )

    try:
        response = await asyncio.wait_for(
            _anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=120,
                temperature=0.6,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=15.0,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"[FOLLOWUP] Erro ao gerar mensagem step {step}: {e}")
        return FALLBACKS.get(step, FALLBACKS[1])


# ── Lógica principal ───────────────────────────────────────────────────────

async def _processar_lead_followup(db, lead_state: LeadState):
    """Verifica e dispara follow-up para um lead específico."""

    # Usa UTC para comparação com banco (que salva em UTC)
    agora_utc = utcnow()

    if lead_state.stage in ("closed", "followup_closed"):
        return

    # Busca última mensagem real do cliente
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

    # Verifica se encerra (72h)
    if minutos_inativo >= MINUTOS_FECHAR and step_atual >= len(FOLLOWUP_MINUTOS):
        lead_state.stage = "followup_closed"
        db.commit()
        logger.info(f"[FOLLOWUP] Lead {lead_state.phone} encerrado por inatividade (72h).")
        return

    # Todos os steps já foram enviados?
    if step_atual >= len(FOLLOWUP_MINUTOS):
        return

    # Verifica se é hora do próximo step pelo tempo de inatividade do cliente
    minutos_necessarios = FOLLOWUP_MINUTOS[step_atual]
    if minutos_inativo < minutos_necessarios:
        return

    # ── ANTI-CASCATA: verifica intervalo mínimo desde o último followup ───
    # Evita que reinicializações do servidor disparem todos os steps de uma vez
    if lead_state.followup_sent_at:
        minutos_desde_ultimo_followup = (agora_utc - lead_state.followup_sent_at).total_seconds() / 60
        if minutos_desde_ultimo_followup < INTERVALO_MINIMO_ENTRE_STEPS:
            logger.debug(
                f"[FOLLOWUP] Anti-cascata: {lead_state.phone} — "
                f"último followup há {minutos_desde_ultimo_followup:.0f}min. Aguardando."
            )
            return

    # ── HORÁRIO COMERCIAL — usa horário de Brasília ────────────────────────
    if not esta_em_horario_comercial(agora_brasilia()):
        logger.debug(f"[FOLLOWUP] Fora do horário comercial (Brasília) para {lead_state.phone}.")
        return

    # Busca dados do lead
    lead = db.query(Lead).filter(Lead.phone == lead_state.phone).first()
    nome = (lead.name or "") if lead else ""

    # Busca nome nas primeiras mensagens se não estiver no banco
    if not nome:
        PALAVRAS_NAO_NOME = {
            "oi","ola","opa","sim","nao","ok","tudo","bom","boa","olha","hey",
            "sublimacao","dtf","plotter","maquina","tinta","impressora","eco",
            "quero","tenho","preciso","busco","procuro","estou","sou","meu","vim",
        }
        primeiras = (
            db.query(Conversation)
            .filter(Conversation.phone == lead_state.phone, Conversation.role == "user")
            .order_by(Conversation.created_at.asc())
            .limit(6)
            .all()
        )
        for msg in primeiras:
            txt = msg.content.strip()
            if 2 < len(txt) < 40 and not txt.startswith("["):
                primeiro = txt.split()[0]
                if len(primeiro) > 2 and primeiro.replace("-","").isalpha() and primeiro.lower() not in PALAVRAS_NAO_NOME:
                    nome = primeiro.capitalize()
                    break

    # Busca histórico filtrado
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

    # Detecta produto
    PRODUTO_MAP = {
        "1908": "Plotter DG 1908i", "3204": "Plotter DG 3204i",
        "3202": "Plotter DG 3202i", "1904": "Plotter DG 1904i",
        "1802": "Plotter DG 1802i", "1801": "Plotter DG 1801i",
        "dtf uv": "DTF UV",         "dtf": "DTF Têxtil",
        "flatbed": "Flatbed UV",    "jinka": "Plotter de Recorte",
        "laser": "Laser",           "eco solvente": "Eco Solvente",
        "sublimacao": "Sublimática",
    }
    produto = ""
    hist_lower = historico_txt.lower()
    for kw, prod in PRODUTO_MAP.items():
        if kw in hist_lower:
            produto = prod
            break

    # Gera e envia
    step_numero = step_atual + 1
    logger.info(f"[FOLLOWUP] Step {step_numero} para {lead_state.phone} ({minutos_inativo:.0f}min inativo)")

    mensagem = await _gerar_mensagem_followup(step_numero, nome, produto, historico_txt)
    await twilio_service.send_whatsapp_message(lead_state.phone, mensagem)

    # Salva no histórico
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
    """Loop principal — verifica todos os leads a cada 5 minutos."""
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

        await asyncio.sleep(300)  # verifica a cada 5 minutos


def resetar_followup(phone: str):
    """Reseta o ciclo quando o cliente responde."""
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
    """Inicia o loop como task no startup do FastAPI."""
    global followup_service_task
    followup_service_task = asyncio.create_task(_loop_followup())
    logger.info("[FOLLOWUP] Task iniciada com sucesso.")
