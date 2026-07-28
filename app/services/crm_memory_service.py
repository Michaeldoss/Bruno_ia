"""Memoria operacional recorrente do Doss CRM.

A cada hora:
- le conversas abertas e suas mensagens em ordem;
- transcreve audios pendentes quando houver media_url;
- calcula tempos reais de resposta;
- usa a IA para classificar situacao, pendencias e proxima acao;
- atualiza memoria por conversa e por cliente no Supabase.

O servico nao envia mensagens nem fecha conversas automaticamente.
Ele apenas recomenda a acao, evitando automacoes perigosas sem revisao humana.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

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

_anthropic = AsyncAnthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY and ANTHROPIC_KEY != "stub" else None


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
        text = text[start:end + 1]
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _response_metrics(messages: List[dict]) -> tuple[Optional[int], Optional[int]]:
    waits: List[int] = []
    pending_customer_at: Optional[datetime] = None
    for msg in messages:
        created_raw = msg.get("created_at")
        if not created_raw:
            continue
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except Exception:
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


async def _transcribe_pending_audio(client: httpx.AsyncClient, conversation_id: str, messages: List[dict]) -> Dict[str, str]:
    transcripts: Dict[str, str] = {}
    if not OPENAI_KEY or OPENAI_KEY == "stub":
        return transcripts

    for msg in messages:
        msg_id = msg.get("id")
        msg_type = str(msg.get("type") or "").lower()
        media_url = msg.get("media_url")
        if not msg_id or not media_url or msg_type not in {"audio", "ptt", "voice", "audioMessage".lower()}:
            continue

        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
            params={"message_id": f"eq.{msg_id}", "select": "transcription,status", "limit": 1},
            headers=_headers(),
        )
        rows = existing.json() if existing.status_code == 200 else []
        if isinstance(rows, list) and rows:
            if rows[0].get("transcription"):
                transcripts[msg_id] = rows[0]["transcription"]
            if rows[0].get("status") in {"processing", "completed"}:
                continue

        await client.post(
            f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "org_id": ORG_ID,
                "message_id": msg_id,
                "conversation_id": conversation_id,
                "media_url": media_url,
                "status": "processing",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        try:
            audio = await client.get(media_url, timeout=30.0)
            audio.raise_for_status()
            filename = f"audio-{msg_id}.ogg"
            files = {"file": (filename, audio.content, audio.headers.get("content-type", "audio/ogg"))}
            data = {"model": "whisper-1", "response_format": "json", "language": "pt"}
            tr = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files=files,
                data=data,
                timeout=60.0,
            )
            tr.raise_for_status()
            text = (tr.json().get("text") or "").strip()
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
        except Exception as exc:
            logger.error("[CRM MEMORY] Falha ao transcrever audio %s: %s", msg_id, exc)
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audio_transcriptions",
                params={"message_id": f"eq.{msg_id}"},
                headers=_headers("return=minimal"),
                json={"status": "failed", "error": str(exc)[:500], "updated_at": datetime.now(timezone.utc).isoformat()},
            )
    return transcripts


async def _analyze_with_ai(conversation: dict, contact: dict, agent: dict, messages: List[dict], transcripts: Dict[str, str], previous: dict) -> Dict[str, Any]:
    avg_response, max_response = _response_metrics(messages)
    now = datetime.now(timezone.utc)
    last_message_at = conversation.get("last_message_at") or (messages[-1].get("created_at") if messages else None)
    inactive_minutes = None
    if last_message_at:
        try:
            last_dt = datetime.fromisoformat(last_message_at.replace("Z", "+00:00"))
            inactive_minutes = max(0, int((now - last_dt).total_seconds() / 60))
        except Exception:
            pass

    last_speaker = "unknown"
    if messages:
        last_speaker = "customer" if messages[-1].get("is_from_contact") else "agent"

    history_lines = []
    for msg in messages[-80:]:
        role = "CLIENTE" if msg.get("is_from_contact") else "AGENTE"
        content = (msg.get("content") or "").strip()
        if msg.get("id") in transcripts:
            content = f"[AUDIO TRANSCRITO] {transcripts[msg['id']]}"
        history_lines.append(f"{role} | {msg.get('created_at')}: {content[:1200]}")
    history = "\n".join(history_lines)

    fallback = {
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
Classifique com rigor operacional. Nao invente fatos.
Regras:
- Se o cliente falou por ultimo e ha pedido, pergunta, autorizacao ou pendencia, needs_agent_reply=true e needs_followup=false.
- Follow-up somente quando o agente falou por ultimo e deixou pergunta ou proposta aguardando o cliente.
- should_close somente quando o assunto estiver realmente concluido e nao houver promessa ou pergunta pendente.
- Se o cliente autorizou compra/pedido, prioridade deve ser high ou critical.
- Memoria deve conter apenas fatos explicitos da conversa.
JSON obrigatorio:
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
  "memory":{
    "facts":[],"products":[],"objections":[],"promises":[],"next_steps":[],"preferences":[]
  }
}"""
        user = f"""CLIENTE: {contact.get('name') or contact.get('phone') or ''}
EMPRESA: {contact.get('company') or ''}
AGENTE: {agent.get('name') or ''}
STATUS CRM: {conversation.get('status')}
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
                ), timeout=45.0,
            )
            result = _safe_json(response.content[0].text)
            if not result:
                result = fallback
        except Exception as exc:
            logger.error("[CRM MEMORY] IA falhou na conversa %s: %s", conversation.get("id"), exc)
            result = fallback

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
        }, headers=_headers(),
    )
    messages = messages_res.json() if messages_res.status_code == 200 and isinstance(messages_res.json(), list) else []
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

    # Nao reanalisa se nada mudou e a ultima analise tem menos de 50 minutos.
    if previous and previous.get("last_message_at") == conversation.get("last_message_at"):
        try:
            analyzed = datetime.fromisoformat(previous["analyzed_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - analyzed < timedelta(minutes=50):
                return
        except Exception:
            pass

    transcripts = await _transcribe_pending_audio(client, conversation_id, messages)
    analysis = await _analyze_with_ai(conversation, contact, agent, messages, transcripts, previous)
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
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
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
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
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
            }, headers=_headers(),
        )
        if conv_res.status_code != 200:
            logger.error("[CRM MEMORY] Falha ao buscar conversas: %s %s", conv_res.status_code, conv_res.text[:300])
            return
        conversations = conv_res.json() if isinstance(conv_res.json(), list) else []
        for conversation in conversations:
            try:
                await _process_conversation(client, conversation)
            except Exception as exc:
                logger.exception("[CRM MEMORY] Erro na conversa %s: %s", conversation.get("id"), exc)
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
