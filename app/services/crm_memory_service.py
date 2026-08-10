"""Memoria operacional incremental do Doss CRM.

Regras de custo e qualidade:
- somente conversas abertas sao consideradas;
- uma conversa sem mensagem nova nunca chama a IA;
- a primeira analise usa um historico limitado;
- as proximas analises usam o resumo salvo + somente mensagens novas;
- tempo parado e urgencia continuam sendo atualizados sem IA;
- todo uso Anthropic e registrado no rastreador existente;
- o ciclo respeita um teto mensal conservador em reais.

Este servico apenas analisa e recomenda. Nao envia mensagens, nao fecha
conversas e nao altera o pipeline automaticamente durante a fase de testes.
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

import httpx
from anthropic import AsyncAnthropic
from sqlalchemy import func

from app.config import get_settings
from app.models.database import SessionLocal, UsageLog
from app.services.usage_tracker import registrar_uso_anthropic

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY
ORG_ID = "dafa7ea5-08c4-44dd-886e-d58905fca38c"
ANTHROPIC_KEY = getattr(settings, "ANTHROPIC_API_KEY", "")
OPENAI_KEY = getattr(settings, "OPENAI_API_KEY", "")

MODEL = "claude-haiku-4-5-20251001"
MONTHLY_BUDGET_BRL = float(os.getenv("CRM_AI_MONTHLY_BUDGET_BRL", "250"))
USD_BRL_SAFETY_RATE = float(os.getenv("CRM_AI_USD_BRL", "6.00"))

# ---------------------------------------------------------------------------
# Janela de execucao (pedido 10/08): reduzir custo do ciclo de memoria
# rodando so em horario comercial, dias uteis, a cada 2h em vez de 1h/24-7.
# Reduz de 168 execucoes/semana para ~25/semana (~85% menos chamadas).
# ---------------------------------------------------------------------------
BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")
CYCLE_INTERVAL_SECONDS = 2 * 3600  # 2 em 2 horas
BUSINESS_WINDOWS = [(8, 12), (13, 18)]  # (inicio_incl, fim_excl), hora local


def _is_business_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(BRASILIA_TZ)
    if now.weekday() >= 5:  # 5=sabado, 6=domingo
        return False
    hour = now.hour
    return any(start <= hour < end for start, end in BUSINESS_WINDOWS)


def _seconds_until_next_window(now: Optional[datetime] = None) -> float:
    """Quantos segundos faltam ate o proximo horario comercial valido,
    usado quando o ciclo acorda fora da janela -- evita ficar checando
    de 2 em 2h sem necessidade."""
    now = now or datetime.now(BRASILIA_TZ)
    for delta_days in range(0, 8):
        day = now if delta_days == 0 else now.replace(hour=0, minute=0, second=0, microsecond=0)
        if delta_days > 0:
            from datetime import timedelta
            day = day + timedelta(days=delta_days)
        if day.weekday() >= 5:
            continue
        for start, _end in BUSINESS_WINDOWS:
            window_start = day.replace(hour=start, minute=0, second=0, microsecond=0)
            if window_start > now:
                return (window_start - now).total_seconds()
        if delta_days == 0:
            continue
    return CYCLE_INTERVAL_SECONDS
MAX_ANALYSES_PER_CYCLE = int(os.getenv("CRM_AI_MAX_ANALYSES_PER_CYCLE", "60"))
MAX_INITIAL_MESSAGES = int(os.getenv("CRM_AI_MAX_INITIAL_MESSAGES", "40"))
MAX_DELTA_MESSAGES = int(os.getenv("CRM_AI_MAX_DELTA_MESSAGES", "40"))
CONTEXT_MESSAGES = int(os.getenv("CRM_AI_CONTEXT_MESSAGES", "6"))
MAX_OUTPUT_TOKENS = int(os.getenv("CRM_AI_MAX_OUTPUT_TOKENS", "650"))

_anthropic = (
    AsyncAnthropic(api_key=ANTHROPIC_KEY)
    if ANTHROPIC_KEY and ANTHROPIC_KEY != "stub"
    else None
)


def _headers(prefer: str = "return=representation") -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _safe_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _same_timestamp(a: Optional[str], b: Optional[str]) -> bool:
    da = _parse_datetime(a)
    db = _parse_datetime(b)
    if da and db:
        return da == db
    return (a or "") == (b or "")


def _monthly_usage_brl() -> float:
    """Retorna custo mensal da camada crm_memory usando cambio conservador."""
    db = None
    try:
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        db = SessionLocal()
        value = (
            db.query(func.coalesce(func.sum(UsageLog.custo_usd), 0.0))
            .filter(
                UsageLog.agente == "crm_memory",
                UsageLog.servico == "anthropic",
                UsageLog.created_at >= month_start,
            )
            .scalar()
        )
        return round(float(value or 0.0) * USD_BRL_SAFETY_RATE, 2)
    except Exception as exc:
        # Em caso de falha no medidor, o ciclo continua limitado por quantidade
        # e por delta; nunca volta ao comportamento de reler tudo.
        logger.error("[CRM MEMORY] Falha ao consultar custo mensal: %s", exc)
        return 0.0
    finally:
        if db:
            db.close()


def _budget_available() -> bool:
    used = _monthly_usage_brl()
    if used >= MONTHLY_BUDGET_BRL:
        logger.error(
            "[CRM MEMORY] Teto mensal atingido: R$ %.2f de R$ %.2f. IA pausada.",
            used,
            MONTHLY_BUDGET_BRL,
        )
        return False
    return True


def _response_metrics(messages: List[dict]) -> Tuple[Optional[int], Optional[int]]:
    waits: List[int] = []
    pending_customer_at: Optional[datetime] = None
    for msg in messages:
        created = _parse_datetime(msg.get("created_at"))
        if not created:
            continue
        if msg.get("is_from_contact"):
            pending_customer_at = created
        elif pending_customer_at:
            seconds = int((created - pending_customer_at).total_seconds())
            if seconds >= 0:
                waits.append(seconds)
            pending_customer_at = None
    if not waits:
        return None, None
    return int(sum(waits) / len(waits)), max(waits)


def _decode_base64_media(value: str) -> Optional[Tuple[bytes, str, str]]:
    raw = (value or "").strip()
    if not raw or "base64," not in raw:
        return None
    header, encoded = raw.split("base64,", 1)
    encoded = re.sub(r"\s+", "", encoded)
    if not encoded:
        return None
    encoded += "=" * (-len(encoded) % 4)
    try:
        audio_bytes = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(audio_bytes) < 32:
        return None
    header = header.lower()
    if "mpeg" in header or "mp3" in header:
        return audio_bytes, "audio/mpeg", "mp3"
    if "wav" in header:
        return audio_bytes, "audio/wav", "wav"
    if "mp4" in header or "m4a" in header:
        return audio_bytes, "audio/mp4", "m4a"
    if "webm" in header:
        return audio_bytes, "audio/webm", "webm"
    return audio_bytes, "audio/ogg", "ogg"


async def _load_audio_bytes(
    client: httpx.AsyncClient, media_value: str
) -> Tuple[bytes, str, str]:
    decoded = _decode_base64_media(media_value)
    if decoded:
        return decoded
    if not re.match(r"^https?://", media_value or "", flags=re.I):
        raise ValueError("midia sem URL HTTP valida e sem Base64 reconhecivel")
    if len(media_value) > 8000:
        raise ValueError("URL de midia excede o limite seguro")
    response = await client.get(media_value, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    if not response.content or len(response.content) < 32:
        raise ValueError("arquivo de audio vazio")
    content_type = (response.headers.get("content-type") or "audio/ogg").split(";")[0]
    extension = {
        "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav",
        "audio/x-wav": "wav", "audio/mp4": "m4a", "audio/webm": "webm",
        "audio/ogg": "ogg",
    }.get(content_type.lower(), "ogg")
    return response.content, content_type, extension


async def _transcribe_new_audio(
    client: httpx.AsyncClient, conversation_id: str, messages: List[dict]
) -> Dict[str, str]:
    transcripts: Dict[str, str] = {}
    if not OPENAI_KEY or OPENAI_KEY == "stub":
        return transcripts

    for msg in messages:
        msg_id = msg.get("id")
        msg_type = str(msg.get("type") or "").lower()
        media_value = msg.get("media_url")
        if not msg_id or not media_value or msg_type not in {
            "audio", "ptt", "voice", "audiomessage"
        }:
            continue

        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
            params={
                "message_id": f"eq.{msg_id}",
                "select": "transcription,status",
                "limit": 1,
            },
            headers=_headers(),
        )
        rows = existing.json() if existing.status_code == 200 else []
        if isinstance(rows, list) and rows:
            row = rows[0]
            if row.get("transcription"):
                transcripts[msg_id] = row["transcription"]
            if row.get("status") in {"completed", "processing", "invalid_media", "unsupported"}:
                continue

        await client.post(
            f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "org_id": ORG_ID,
                "message_id": msg_id,
                "conversation_id": conversation_id,
                "media_url": media_value if len(media_value) <= 8000 else None,
                "status": "processing",
                "error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        try:
            audio_bytes, content_type, extension = await _load_audio_bytes(client, media_value)
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files={"file": (f"audio-{msg_id}.{extension}", audio_bytes, content_type)},
                data={"model": "whisper-1", "response_format": "json", "language": "pt"},
                timeout=90.0,
            )
            response.raise_for_status()
            text = (response.json().get("text") or "").strip()
            if not text:
                raise ValueError("transcricao retornou vazia")
            transcripts[msg_id] = text
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={
                    "transcription": text,
                    "language": "pt",
                    "status": "completed",
                    "error": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except ValueError as exc:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={"status": "invalid_media", "error": str(exc)[:500], "updated_at": datetime.now(timezone.utc).isoformat()},
            )
        except Exception as exc:
            logger.error("[CRM MEMORY] Falha ao transcrever audio %s: %s", msg_id, exc)
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={"status": "failed", "error": str(exc)[:500], "updated_at": datetime.now(timezone.utc).isoformat()},
            )
    return transcripts


def _normalize_result(
    result: Dict[str, Any], last_speaker: str, inactive_minutes: Optional[int]
) -> Dict[str, Any]:
    normalized = dict(result or {})
    normalized["last_speaker"] = last_speaker
    status = str(normalized.get("analysis_status") or "").lower()
    protected = {"order_authorized", "critical", "support"}

    if last_speaker == "customer":
        # FIX: antes, sempre que a ULTIMA mensagem era do cliente, o
        # codigo forcava needs_agent_reply=True e should_close=False na
        # marca, IGNORANDO o que a propria IA ja tinha determinado. Isso
        # quebrava exatamente o caso "obrigado, ate mais" -- o modelo ja
        # reconhecia certo que era um fechamento genuino (should_close:
        # true no JSON dele), mas essa regra mecanica sobrescrevia de
        # volta pra "precisa responder", e a conversa nunca saia do
        # status "aguardando agente" -- ficava gerando consulta/acao
        # pra sempre numa conversa que ja tinha acabado.
        # Agora respeita o should_close da IA quando o cliente encerrou
        # de verdade (nao esta em status protegido tipo pedido
        # autorizado/critico/suporte, que merecem revisao humana mesmo
        # com uma despedida no final).
        if normalized.get("should_close") and status not in protected:
            normalized["needs_agent_reply"] = False
            normalized["needs_followup"] = False
            normalized["analysis_status"] = "ready_to_close"
        else:
            normalized["needs_agent_reply"] = True
            normalized["needs_followup"] = False
            normalized["should_close"] = False
            if status not in protected:
                normalized["analysis_status"] = "awaiting_agent"
            if (inactive_minutes or 0) >= 60 and normalized.get("priority") in {None, "", "low", "normal"}:
                normalized["priority"] = "high"
    elif last_speaker == "agent":
        normalized["needs_agent_reply"] = False
        if normalized.get("needs_followup"):
            normalized["analysis_status"] = "followup_recommended"
            normalized["should_close"] = False
        elif normalized.get("should_close"):
            normalized["analysis_status"] = "ready_to_close"
        elif status == "awaiting_agent":
            normalized["analysis_status"] = "awaiting_customer"

    if normalized.get("should_close"):
        normalized["needs_agent_reply"] = False
        normalized["needs_followup"] = False
    if normalized.get("needs_agent_reply"):
        normalized["needs_followup"] = False
        normalized["should_close"] = False
    return normalized


def _format_messages(messages: List[dict], transcripts: Dict[str, str]) -> str:
    lines: List[str] = []
    for msg in messages:
        role = "CLIENTE" if msg.get("is_from_contact") else "AGENTE"
        content = (msg.get("content") or "").strip()
        if msg.get("id") in transcripts:
            content = f"[AUDIO TRANSCRITO] {transcripts[msg['id']]}"
        if not content:
            content = f"[{str(msg.get('type') or 'mensagem').upper()} SEM TEXTO]"
        lines.append(f"{role} | {msg.get('created_at')}: {content[:900]}")
    return "\n".join(lines)


async def _analyze_incremental(
    conversation: dict,
    contact: dict,
    agent: dict,
    all_messages: List[dict],
    prompt_messages: List[dict],
    transcripts: Dict[str, str],
    previous: dict,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_message_at = conversation.get("last_message_at") or all_messages[-1].get("created_at")
    last_dt = _parse_datetime(last_message_at)
    inactive_minutes = max(0, int((now - last_dt).total_seconds() / 60)) if last_dt else None
    last_speaker = "customer" if all_messages[-1].get("is_from_contact") else "agent"
    avg_response, max_response = _response_metrics(all_messages)

    # Fallback (IA indisponivel/sem historico) tambem reconhece despedida
    # simples por palavra-chave -- rede de seguranca leve, o caminho
    # principal (IA) e quem faz a analise de verdade.
    _ultimo_texto = (all_messages[-1].get("content") or "").strip().lower()
    _DESPEDIDAS_SIMPLES = {
        "obrigado", "obrigada", "obg", "vlw", "valeu", "blz", "beleza",
        "ok obrigado", "ok obrigada", "até mais", "ate mais", "falou",
        "tchau", "👍", "🙏",
    }
    _e_despedida_simples = last_speaker == "customer" and any(
        _ultimo_texto == d or _ultimo_texto.startswith(d + " ") or _ultimo_texto.startswith(d + ",")
        for d in _DESPEDIDAS_SIMPLES
    )

    fallback: Dict[str, Any] = {
        "analysis_status": "ready_to_close" if _e_despedida_simples else ("awaiting_agent" if last_speaker == "customer" else "awaiting_customer"),
        "subject": previous.get("subject") or "",
        "customer_intent": previous.get("customer_intent") or "",
        "last_speaker": last_speaker,
        "pending_question": last_speaker == "customer" and not _e_despedida_simples,
        "needs_agent_reply": last_speaker == "customer" and not _e_despedida_simples,
        "needs_followup": False,
        "should_close": _e_despedida_simples,
        "priority": "high" if last_speaker == "customer" and not _e_despedida_simples and (inactive_minutes or 0) >= 60 else "normal",
        "summary": previous.get("summary") or "Classificacao baseada na ultima mensagem.",
        "recommended_action": "Nenhuma ação necessária, cliente encerrou a conversa" if _e_despedida_simples else ("Responder o cliente" if last_speaker == "customer" else "Aguardar resposta do cliente"),
        "memory": previous.get("memory") if isinstance(previous.get("memory"), dict) else {},
    }

    history = _format_messages(prompt_messages, transcripts)
    if not _anthropic or not history:
        result = fallback
        usage_data = {"input_tokens": 0, "output_tokens": 0, "model": MODEL, "used_ai": False}
    else:
        previous_context = {
            "summary": (previous.get("summary") or "")[:1800],
            "subject": previous.get("subject"),
            "customer_intent": previous.get("customer_intent"),
            "analysis_status": previous.get("analysis_status"),
            "memory": previous.get("memory") if isinstance(previous.get("memory"), dict) else {},
        }
        system = """Voce e o supervisor comercial do Doss CRM. Atualize a analise usando o resumo anterior e as mensagens novas. Responda APENAS JSON valido.
Regras:
- nao invente fatos e nao remova fatos anteriores sem contradicao explicita;
- cliente por ultimo: needs_agent_reply=true, needs_followup=false, should_close=false;
- follow-up somente se o agente falou por ultimo e aguarda o cliente;
- should_close somente com assunto realmente concluido, sem pergunta, promessa ou acao pendente;
- pedido autorizado deve ser high ou critical;
- summary deve consolidar o historico anterior com as novidades, de forma curta e operacional;
- memory deve conter somente fatos explicitos e uteis;
- recommended_action e o campo mais importante pro agente humano que vai ler isso -- NUNCA use frase generica
  como "Responder o cliente" ou "Aguardar resposta" quando houver informacao especifica disponivel no
  historico. Sempre que possivel, a acao deve dizer: o que fazer (ex: cobrar confirmacao, enviar proposta,
  ligar), sobre o que exatamente (produto/valor/prazo combinado, citando numero quando existir), e o que
  o cliente esta esperando do agente. Exemplo ruim: "Responder o cliente". Exemplo bom: "Cobrar confirmacao
  da proposta de R$950/rolo (0,30x25) enviada em 28/07 -- cliente ainda nao respondeu se aceita". So use uma
  frase genuinamente generica quando o historico realmente nao tiver nenhum detalhe concreto pra citar.
JSON:
{"analysis_status":"awaiting_agent|awaiting_customer|negotiation_active|proposal_pending|order_authorized|support|followup_recommended|ready_to_close|critical","subject":"texto curto","customer_intent":"texto curto","last_speaker":"customer|agent","pending_question":true,"needs_agent_reply":true,"needs_followup":false,"should_close":false,"priority":"low|normal|high|critical","summary":"resumo consolidado","recommended_action":"acao especifica e concreta, citando o que fazer + sobre o que + o que o cliente espera (ver regra acima)","memory":{"facts":[],"products":[],"objections":[],"promises":[],"next_steps":[],"preferences":[]}}"""
        user = f"""CLIENTE: {contact.get('name') or contact.get('phone') or ''}
EMPRESA: {contact.get('company') or ''}
AGENTE: {agent.get('name') or ''}
STATUS CRM: {conversation.get('status')}
ULTIMO INTERLOCUTOR CALCULADO: {last_speaker}
TEMPO INATIVO MIN: {inactive_minutes}
CONTEXTO ANTERIOR: {json.dumps(previous_context, ensure_ascii=False)[:4200]}

MENSAGENS NOVAS E CONTEXTO IMEDIATO:
{history}"""
        try:
            response = await asyncio.wait_for(
                _anthropic.messages.create(
                    model=MODEL,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0.0,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ),
                timeout=45.0,
            )
            registrar_uso_anthropic(MODEL, response.usage, agente="crm_memory")
            result = _safe_json(response.content[0].text) or fallback
            usage_data = {
                "input_tokens": int(getattr(response.usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(response.usage, "output_tokens", 0) or 0),
                "cache_creation_tokens": int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0),
                "cache_read_tokens": int(getattr(response.usage, "cache_read_input_tokens", 0) or 0),
                "model": MODEL,
                "used_ai": True,
            }
        except Exception as exc:
            logger.error("[CRM MEMORY] IA falhou na conversa %s: %s", conversation.get("id"), exc)
            result = fallback
            usage_data = {"input_tokens": 0, "output_tokens": 0, "model": MODEL, "used_ai": False, "error": str(exc)[:300]}

    result = _normalize_result(result, last_speaker, inactive_minutes)
    result.update({
        "avg_response_seconds": avg_response,
        "max_response_seconds": max_response,
        "inactive_minutes": inactive_minutes,
        "last_message_at": last_message_at,
        "usage": usage_data,
        "processing_mode": "initial" if not previous else "incremental",
        "messages_sent_to_ai": len(prompt_messages),
    })
    return result


async def _refresh_unchanged_without_ai(
    client: httpx.AsyncClient, conversation: dict, previous: dict
) -> None:
    last_dt = _parse_datetime(conversation.get("last_message_at"))
    if not last_dt:
        return
    inactive = max(0, int((datetime.now(timezone.utc) - last_dt).total_seconds() / 60))
    priority = previous.get("priority") or "normal"
    if previous.get("last_speaker") == "customer" and inactive >= 60 and priority in {"low", "normal"}:
        priority = "high"
    if inactive == previous.get("inactive_minutes") and priority == previous.get("priority"):
        return
    now = datetime.now(timezone.utc).isoformat()
    await client.patch(
        f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
        params={"conversation_id": f"eq.{conversation['id']}"},
        headers=_headers("return=minimal"),
        json={"inactive_minutes": inactive, "priority": priority, "updated_at": now},
    )


async def _process_conversation(client: httpx.AsyncClient, conversation: dict) -> bool:
    conversation_id = conversation["id"]
    prev_res = await client.get(
        f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
        params={"conversation_id": f"eq.{conversation_id}", "select": "*", "limit": 1},
        headers=_headers(),
    )
    prev_rows = prev_res.json() if prev_res.status_code == 200 else []
    previous = prev_rows[0] if isinstance(prev_rows, list) and prev_rows else {}

    # Trava principal: timestamp igual significa zero chamada de IA.
    if previous and _same_timestamp(previous.get("last_message_at"), conversation.get("last_message_at")):
        await _refresh_unchanged_without_ai(client, conversation, previous)
        return False

    messages_res = await client.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        params={
            "conversation_id": f"eq.{conversation_id}",
            "is_internal_note": "eq.false",
            "deleted_at": "is.null",
            "select": "id,content,type,media_url,is_from_contact,created_at,sender_id",
            "order": "created_at.desc",
            "limit": 120,
        },
        headers=_headers(),
    )
    recent_desc = messages_res.json() if messages_res.status_code == 200 and isinstance(messages_res.json(), list) else []
    all_messages = list(reversed(recent_desc))
    if not all_messages:
        return False

    cursor = _parse_datetime(previous.get("last_message_at")) if previous else None
    if cursor:
        new_indexes = [i for i, msg in enumerate(all_messages) if (_parse_datetime(msg.get("created_at")) or cursor) > cursor]
        if not new_indexes:
            await _refresh_unchanged_without_ai(client, conversation, previous)
            return False
        first_new = new_indexes[0]
        new_messages = all_messages[first_new:][-MAX_DELTA_MESSAGES:]
        context_start = max(0, first_new - CONTEXT_MESSAGES)
        prompt_messages = all_messages[context_start:first_new] + new_messages
    else:
        new_messages = all_messages[-MAX_INITIAL_MESSAGES:]
        prompt_messages = new_messages

    transcripts = await _transcribe_new_audio(client, conversation_id, new_messages)
    analysis = await _analyze_incremental(
        conversation,
        conversation.get("contacts") or {},
        conversation.get("profiles") or {},
        all_messages,
        prompt_messages,
        transcripts,
        previous,
    )
    now = datetime.now(timezone.utc).isoformat()
    memory = analysis.get("memory") if isinstance(analysis.get("memory"), dict) else {}

    payload = {
        "org_id": conversation.get("org_id") or ORG_ID,
        "conversation_id": conversation_id,
        "contact_id": conversation.get("contact_id"),
        "agent_id": conversation.get("agent_id"),
        "analysis_status": analysis.get("analysis_status") or "unknown",
        "subject": analysis.get("subject"),
        "customer_intent": analysis.get("customer_intent"),
        "last_speaker": analysis.get("last_speaker"),
        "pending_question": bool(analysis.get("pending_question")),
        "needs_agent_reply": bool(analysis.get("needs_agent_reply")),
        "needs_followup": bool(analysis.get("needs_followup")),
        "should_close": bool(analysis.get("should_close")),
        "priority": analysis.get("priority") or "normal",
        "avg_response_seconds": analysis.get("avg_response_seconds"),
        "max_response_seconds": analysis.get("max_response_seconds"),
        "inactive_minutes": analysis.get("inactive_minutes"),
        "summary": analysis.get("summary") or "",
        "recommended_action": analysis.get("recommended_action"),
        "next_review_at": None,
        "memory": memory,
        "raw_analysis": analysis,
        "last_message_at": analysis.get("last_message_at"),
        "analyzed_at": now,
        "updated_at": now,
    }
    await client.post(
        f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
        params={"on_conflict": "conversation_id"},
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
        json=payload,
    )

    contact_id = conversation.get("contact_id")
    if contact_id:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/customer_ai_memory",
            params={"on_conflict": "org_id,contact_id"},
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "org_id": conversation.get("org_id") or ORG_ID,
                "contact_id": contact_id,
                "summary": analysis.get("summary") or "",
                "facts": memory.get("facts", []),
                "preferences": memory.get("preferences", []),
                "products": memory.get("products", []),
                "objections": memory.get("objections", []),
                "promises": memory.get("promises", []),
                "next_steps": memory.get("next_steps", []),
                "last_conversation_id": conversation_id,
                "last_interaction_at": analysis.get("last_message_at"),
                "generated_at": now,
                "updated_at": now,
            },
        )
    return bool(analysis.get("usage", {}).get("used_ai"))


async def run_crm_memory_cycle() -> None:
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        logger.warning("[CRM MEMORY] Supabase nao configurado.")
        return

    logger.info(
        "[CRM MEMORY] Ciclo incremental iniciado. Custo mensal estimado: R$ %.2f / R$ %.2f",
        _monthly_usage_brl(),
        MONTHLY_BUDGET_BRL,
    )
    analyzed = 0
    skipped = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        conv_res = await client.get(
            f"{SUPABASE_URL}/rest/v1/conversations",
            params={
                "org_id": f"eq.{ORG_ID}",
                "status": "eq.open",
                "select": "id,org_id,contact_id,agent_id,status,last_message,last_message_at,created_at,whatsapp_phone,whatsapp_instance,contacts(id,name,company,phone,email,address_city,address_state),profiles(id,name,email)",
                "order": "last_message_at.asc.nullslast",
                "limit": 500,
            },
            headers=_headers(),
        )
        if conv_res.status_code != 200:
            logger.error("[CRM MEMORY] Falha ao buscar conversas: %s %s", conv_res.status_code, conv_res.text[:300])
            return

        conversations = conv_res.json() if isinstance(conv_res.json(), list) else []
        for conversation in conversations:
            if analyzed >= MAX_ANALYSES_PER_CYCLE:
                logger.warning("[CRM MEMORY] Limite de %s analises no ciclo atingido.", MAX_ANALYSES_PER_CYCLE)
                break
            if not _budget_available():
                break
            try:
                used_ai = await _process_conversation(client, conversation)
                if used_ai:
                    analyzed += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.exception("[CRM MEMORY] Erro na conversa %s: %s", conversation.get("id"), exc)

    logger.info(
        "[CRM MEMORY] Ciclo finalizado: %s analisadas com IA, %s ignoradas/atualizadas sem IA. Custo mensal: R$ %.2f.",
        analyzed,
        skipped,
        _monthly_usage_brl(),
    )


async def _loop() -> None:
    await asyncio.sleep(20)
    while True:
        if not _is_business_hours():
            wait_s = _seconds_until_next_window()
            logger.info(
                "[CRM MEMORY] Fora do horario comercial (seg-sex 08-12/13-18). "
                "Proxima janela em %.1fh.",
                wait_s / 3600,
            )
            await asyncio.sleep(max(wait_s, 60))
            continue
        try:
            await run_crm_memory_cycle()
        except Exception as exc:
            logger.exception("[CRM MEMORY] Erro geral do ciclo: %s", exc)
        await asyncio.sleep(CYCLE_INTERVAL_SECONDS)


crm_memory_task: Optional[asyncio.Task] = None


def start_crm_memory_service() -> None:
    global crm_memory_task
    if crm_memory_task and not crm_memory_task.done():
        return
    crm_memory_task = asyncio.create_task(_loop())
    logger.info("[CRM MEMORY] Servico incremental iniciado.")
