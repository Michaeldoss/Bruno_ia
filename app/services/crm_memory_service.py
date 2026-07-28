"""Memoria operacional recorrente do Doss CRM.

O servico le conversas abertas, calcula tempos reais de resposta, transcreve
audios quando possivel e grava recomendacoes. Ele nao envia mensagens, nao
fecha conversas e nao altera o pipeline automaticamente.
"""

import asyncio
import base64
import binascii
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from anthropic import AsyncAnthropic

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY
ORG_ID = "dafa7ea5-08c4-44dd-886e-d58905fca38c"
ANTHROPIC_KEY = getattr(settings, "ANTHROPIC_API_KEY", "")
OPENAI_KEY = getattr(settings, "OPENAI_API_KEY", "")

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


def _response_metrics(messages: List[dict]) -> Tuple[Optional[int], Optional[int]]:
    """Mede o tempo entre a ultima mensagem do cliente e a primeira resposta.

    Mensagens consecutivas do cliente formam um unico bloco; o relogio comeca
    na ultima mensagem desse bloco, evitando inflar o tempo de resposta.
    """
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
    """Decodifica data URI e os formatos legados salvos pelo webhook."""
    raw = (value or "").strip()
    if not raw or "base64," not in raw:
        return None

    header, encoded = raw.split("base64,", 1)
    encoded = re.sub(r"\s+", "", encoded)
    if not encoded:
        return None

    # Corrige padding ausente sem aceitar conteudo arbitrario.
    encoded += "=" * (-len(encoded) % 4)
    try:
        audio_bytes = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return None

    if len(audio_bytes) < 32:
        return None

    header_lower = header.lower()
    if "mpeg" in header_lower or "mp3" in header_lower:
        return audio_bytes, "audio/mpeg", "mp3"
    if "wav" in header_lower:
        return audio_bytes, "audio/wav", "wav"
    if "mp4" in header_lower or "m4a" in header_lower:
        return audio_bytes, "audio/mp4", "m4a"
    if "webm" in header_lower:
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
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mp4": "m4a",
        "audio/webm": "webm",
        "audio/ogg": "ogg",
    }.get(content_type.lower(), "ogg")
    return response.content, content_type, extension


async def _transcribe_pending_audio(
    client: httpx.AsyncClient, conversation_id: str, messages: List[dict]
) -> Dict[str, str]:
    transcripts: Dict[str, str] = {}
    if not OPENAI_KEY or OPENAI_KEY == "stub":
        return transcripts

    terminal_statuses = {"completed", "invalid_media", "unsupported"}

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
            if row.get("status") in terminal_statuses or row.get("status") == "processing":
                continue

        now = datetime.now(timezone.utc).isoformat()
        await client.post(
            f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
            headers={
                **_headers(),
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json={
                "org_id": ORG_ID,
                "message_id": msg_id,
                "conversation_id": conversation_id,
                # Nao duplica Base64 enorme na tabela de transcricoes.
                "media_url": media_value if len(media_value) <= 8000 else None,
                "status": "processing",
                "error": None,
                "updated_at": now,
            },
        )

        try:
            audio_bytes, content_type, extension = await _load_audio_bytes(client, media_value)
            filename = f"audio-{msg_id}.{extension}"
            files = {"file": (filename, audio_bytes, content_type)}
            data = {"model": "whisper-1", "response_format": "json", "language": "pt"}
            transcription_response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files=files,
                data=data,
                timeout=90.0,
            )
            transcription_response.raise_for_status()
            text = (transcription_response.json().get("text") or "").strip()
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
            logger.warning("[CRM MEMORY] Audio invalido %s: %s", msg_id, exc)
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={
                    "status": "invalid_media",
                    "error": str(exc)[:500],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as exc:
            logger.error("[CRM MEMORY] Falha ao transcrever audio %s: %s", msg_id, exc)
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={
                    "status": "failed",
                    "error": str(exc)[:500],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    return transcripts


def _normalize_operational_result(
    result: Dict[str, Any], last_speaker: str, inactive_minutes: Optional[int]
) -> Dict[str, Any]:
    """Impede contradicoes entre o ultimo interlocutor e a classificacao."""
    normalized = dict(result or {})
    normalized["last_speaker"] = last_speaker

    protected_statuses = {"order_authorized", "critical", "support"}
    status = str(normalized.get("analysis_status") or "").strip().lower()

    if last_speaker == "customer":
        normalized["needs_agent_reply"] = True
        normalized["needs_followup"] = False
        normalized["should_close"] = False
        if status not in protected_statuses:
            normalized["analysis_status"] = "awaiting_agent"
        if not normalized.get("recommended_action"):
            normalized["recommended_action"] = "Responder o cliente"
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


async def _analyze_with_ai(
    conversation: dict,
    contact: dict,
    agent: dict,
    messages: List[dict],
    transcripts: Dict[str, str],
    previous: dict,
) -> Dict[str, Any]:
    avg_response, max_response = _response_metrics(messages)
    now = datetime.now(timezone.utc)
    last_message_at = conversation.get("last_message_at") or (
        messages[-1].get("created_at") if messages else None
    )
    last_dt = _parse_datetime(last_message_at)
    inactive_minutes = max(0, int((now - last_dt).total_seconds() / 60)) if last_dt else None

    last_speaker = "unknown"
    if messages:
        last_speaker = "customer" if messages[-1].get("is_from_contact") else "agent"

    history_lines: List[str] = []
    for msg in messages[-80:]:
        role = "CLIENTE" if msg.get("is_from_contact") else "AGENTE"
        content = (msg.get("content") or "").strip()
        if msg.get("id") in transcripts:
            content = f"[AUDIO TRANSCRITO] {transcripts[msg['id']]}"
        history_lines.append(f"{role} | {msg.get('created_at')}: {content[:1200]}")
    history = "\n".join(history_lines)

    fallback: Dict[str, Any] = {
        "analysis_status": "awaiting_agent" if last_speaker == "customer" else "awaiting_customer",
        "subject": "",
        "customer_intent": "",
        "last_speaker": last_speaker,
        "pending_question": last_speaker == "customer",
        "needs_agent_reply": last_speaker == "customer",
        "needs_followup": False,
        "should_close": False,
        "priority": "high" if last_speaker == "customer" and (inactive_minutes or 0) >= 60 else "normal",
        "summary": "Analise semantica indisponivel; classificacao baseada na ultima mensagem.",
        "recommended_action": "Responder o cliente" if last_speaker == "customer" else "Aguardar resposta do cliente",
        "memory": {},
    }

    if not _anthropic or not history:
        result = fallback
    else:
        system = """Voce e o supervisor comercial do Doss CRM. Leia a conversa inteira e responda APENAS JSON valido.
Classifique com rigor operacional e nao invente fatos.
Regras obrigatorias:
- O campo last_speaker deve refletir a ultima mensagem real.
- Se o cliente falou por ultimo, needs_agent_reply=true, needs_followup=false e a conversa nao pode ser encerrada.
- Follow-up somente quando o agente falou por ultimo e aguarda retorno do cliente.
- should_close somente quando o assunto estiver concluido, sem pergunta, promessa ou acao pendente.
- Pedido autorizado deve ter prioridade high ou critical.
- Memoria deve conter somente fatos explicitos.
JSON:
{
  "analysis_status":"awaiting_agent|awaiting_customer|negotiation_active|proposal_pending|order_authorized|support|followup_recommended|ready_to_close|critical",
  "subject":"texto curto",
  "customer_intent":"texto curto",
  "last_speaker":"customer|agent",
  "pending_question":true,
  "needs_agent_reply":true,
  "needs_followup":false,
  "should_close":false,
  "priority":"low|normal|high|critical",
  "summary":"resumo operacional",
  "recommended_action":"acao objetiva",
  "memory":{"facts":[],"products":[],"objections":[],"promises":[],"next_steps":[],"preferences":[]}
}"""
        user = f"""CLIENTE: {contact.get('name') or contact.get('phone') or ''}
EMPRESA: {contact.get('company') or ''}
AGENTE: {agent.get('name') or ''}
STATUS CRM: {conversation.get('status')}
ULTIMO INTERLOCUTOR CALCULADO: {last_speaker}
TEMPO INATIVO MIN: {inactive_minutes}
ANALISE ANTERIOR: {json.dumps(previous or {}, ensure_ascii=False)[:3000]}

CONVERSA:
{history}"""
        try:
            response = await asyncio.wait_for(
                _anthropic.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1200,
                    temperature=0.0,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ),
                timeout=45.0,
            )
            result = _safe_json(response.content[0].text) or fallback
        except Exception as exc:
            logger.error("[CRM MEMORY] IA falhou na conversa %s: %s", conversation.get("id"), exc)
            result = fallback

    result = _normalize_operational_result(result, last_speaker, inactive_minutes)
    result["avg_response_seconds"] = avg_response
    result["max_response_seconds"] = max_response
    result["inactive_minutes"] = inactive_minutes
    result["last_message_at"] = last_message_at
    return result


async def _process_conversation(client: httpx.AsyncClient, conversation: dict) -> None:
    conversation_id = conversation["id"]
    messages_res = await client.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        params={
            "conversation_id": f"eq.{conversation_id}",
            "is_internal_note": "eq.false",
            "deleted_at": "is.null",
            "select": "id,content,type,media_url,is_from_contact,created_at,sender_id",
            "order": "created_at.asc",
            "limit": 500,
        },
        headers=_headers(),
    )
    messages = (
        messages_res.json()
        if messages_res.status_code == 200 and isinstance(messages_res.json(), list)
        else []
    )
    if not messages:
        return

    contact = conversation.get("contacts") or {}
    agent = conversation.get("profiles") or {}
    prev_res = await client.get(
        f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
        params={"conversation_id": f"eq.{conversation_id}", "select": "*", "limit": 1},
        headers=_headers(),
    )
    prev_rows = prev_res.json() if prev_res.status_code == 200 else []
    previous = prev_rows[0] if isinstance(prev_rows, list) and prev_rows else {}

    if previous and previous.get("last_message_at") == conversation.get("last_message_at"):
        analyzed = _parse_datetime(previous.get("analyzed_at"))
        if analyzed and datetime.now(timezone.utc) - analyzed < timedelta(minutes=50):
            return

    transcripts = await _transcribe_pending_audio(client, conversation_id, messages)
    analysis = await _analyze_with_ai(
        conversation, contact, agent, messages, transcripts, previous
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
        "next_review_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "memory": memory,
        "raw_analysis": analysis,
        "last_message_at": analysis.get("last_message_at"),
        "analyzed_at": now,
        "updated_at": now,
    }
    await client.post(
        f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
        params={"on_conflict": "conversation_id"},
        headers={
            **_headers(),
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        json=payload,
    )

    contact_id = conversation.get("contact_id")
    if contact_id:
        customer_payload = {
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
        }
        await client.post(
            f"{SUPABASE_URL}/rest/v1/customer_ai_memory",
            params={"on_conflict": "org_id,contact_id"},
            headers={
                **_headers(),
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json=customer_payload,
        )


async def run_crm_memory_cycle() -> None:
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        logger.warning("[CRM MEMORY] Supabase nao configurado.")
        return

    logger.info("[CRM MEMORY] Iniciando ciclo de analise recorrente.")
    async with httpx.AsyncClient(timeout=30.0) as client:
        conv_res = await client.get(
            f"{SUPABASE_URL}/rest/v1/conversations",
            params={
                "org_id": f"eq.{ORG_ID}",
                "status": "eq.open",
                "select": "id,org_id,contact_id,agent_id,status,last_message,last_message_at,created_at,whatsapp_phone,whatsapp_instance,contacts(id,name,company,phone,email,address_city,address_state),profiles(id,name,email)",
                "order": "last_message_at.desc.nullslast",
                "limit": 200,
            },
            headers=_headers(),
        )
        if conv_res.status_code != 200:
            logger.error(
                "[CRM MEMORY] Falha ao buscar conversas: %s %s",
                conv_res.status_code,
                conv_res.text[:300],
            )
            return

        conversations = conv_res.json() if isinstance(conv_res.json(), list) else []
        for conversation in conversations:
            try:
                await _process_conversation(client, conversation)
            except Exception as exc:
                logger.exception(
                    "[CRM MEMORY] Erro na conversa %s: %s",
                    conversation.get("id"),
                    exc,
                )
    logger.info("[CRM MEMORY] Ciclo finalizado.")


async def _loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_crm_memory_cycle()
        except Exception as exc:
            logger.exception("[CRM MEMORY] Erro geral do ciclo: %s", exc)
        await asyncio.sleep(3600)


crm_memory_task: Optional[asyncio.Task] = None


def start_crm_memory_service() -> None:
    global crm_memory_task
    if crm_memory_task and not crm_memory_task.done():
        return
    crm_memory_task = asyncio.create_task(_loop())
    logger.info("[CRM MEMORY] Servico iniciado.")
