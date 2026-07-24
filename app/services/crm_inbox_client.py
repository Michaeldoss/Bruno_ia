"""Espelha em tempo real as mensagens do Bruno no Inbox do Doss CRM."""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY
ORG_ID = "dafa7ea5-08c4-44dd-886e-d58905fca38c"
BRUNO_AGENT_ID = "31b8f1f2-b509-4319-abfb-989954b3ba25"
WHATSAPP_INSTANCE = "bruno-ia"
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _headers(prefer_representation: bool = True) -> dict:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer_representation:
        headers["Prefer"] = "return=representation"
    return headers


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", str(raw or "").replace("whatsapp:", ""))
    if not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, attempts: int = 4, **kwargs) -> httpx.Response:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code not in _RETRYABLE_STATUS:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc

        logger.warning("[CRM Inbox] tentativa %s/%s falhou: %s", attempt, attempts, last_error)
        if attempt < attempts:
            await asyncio.sleep(min(6.0, 1.0 * (2 ** (attempt - 1))))
    raise RuntimeError(f"Falha definitiva no CRM Inbox: {last_error}") from last_error


async def _get_or_create_contact(client: httpx.AsyncClient, phone: str, nome: Optional[str]) -> Optional[str]:
    response = await _request_with_retry(
        client,
        "GET",
        f"{SUPABASE_URL}/rest/v1/contacts",
        params={"phone": f"eq.{phone}", "org_id": f"eq.{ORG_ID}", "select": "id", "limit": 1},
        headers=_headers(),
    )
    rows = response.json() if response.status_code == 200 else []
    if isinstance(rows, list) and rows:
        return rows[0]["id"]

    response = await _request_with_retry(
        client,
        "POST",
        f"{SUPABASE_URL}/rest/v1/contacts",
        headers=_headers(),
        json={"org_id": ORG_ID, "name": nome or phone, "phone": phone, "origin": "Bruno IA"},
    )
    if response.status_code >= 300:
        retry = await _request_with_retry(
            client,
            "GET",
            f"{SUPABASE_URL}/rest/v1/contacts",
            params={"phone": f"eq.{phone}", "org_id": f"eq.{ORG_ID}", "select": "id", "limit": 1},
            headers=_headers(),
        )
        retry_rows = retry.json() if retry.status_code == 200 else []
        if isinstance(retry_rows, list) and retry_rows:
            return retry_rows[0]["id"]
        return None

    data = response.json()
    return (data[0]["id"] if isinstance(data, list) else data.get("id")) if data else None


async def _get_or_create_conversation(client: httpx.AsyncClient, phone: str, contact_id: str) -> tuple:
    params = {
        "whatsapp_phone": f"eq.{phone}",
        "org_id": f"eq.{ORG_ID}",
        "whatsapp_instance": f"eq.{WHATSAPP_INSTANCE}",
        "status": "eq.open",
        "select": "id,unread_count",
        "order": "created_at.desc",
        "limit": 1,
    }
    response = await _request_with_retry(
        client, "GET", f"{SUPABASE_URL}/rest/v1/conversations", params=params, headers=_headers()
    )
    rows = response.json() if response.status_code == 200 else []
    if isinstance(rows, list) and rows:
        return rows[0]["id"], rows[0].get("unread_count") or 0

    response = await _request_with_retry(
        client,
        "POST",
        f"{SUPABASE_URL}/rest/v1/conversations",
        headers=_headers(),
        json={
            "org_id": ORG_ID,
            "contact_id": contact_id,
            "whatsapp_phone": phone,
            "whatsapp_instance": WHATSAPP_INSTANCE,
            "agent_id": BRUNO_AGENT_ID,
            "status": "open",
            "unread_count": 0,
        },
    )
    if response.status_code >= 300:
        fallback = await _request_with_retry(
            client, "GET", f"{SUPABASE_URL}/rest/v1/conversations", params=params, headers=_headers()
        )
        fallback_rows = fallback.json() if fallback.status_code == 200 else []
        if isinstance(fallback_rows, list) and fallback_rows:
            return fallback_rows[0]["id"], fallback_rows[0].get("unread_count") or 0
        return None, 0

    data = response.json()
    conv_id = (data[0]["id"] if isinstance(data, list) else data.get("id")) if data else None
    return conv_id, 0


async def human_active_recently(phone: str, window_hours: int = 12) -> bool:
    """Retorna True apenas quando existe takeover humano real.

    Uma mensagem humana antiga nao pode bloquear uma nova resposta do Bruno.
    O takeover e considerado real quando:
    1. existe conversa aberta atualmente atribuida a um agente diferente do Bruno; ou
    2. existe mensagem humana enviada depois da mensagem mais recente do cliente.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return False

    phone_clean = _normalize_phone(phone)
    if not phone_clean:
        return False

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0)) as client:
            response = await _request_with_retry(
                client,
                "GET",
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={
                    "whatsapp_phone": f"eq.{phone_clean}",
                    "org_id": f"eq.{ORG_ID}",
                    "select": "id,agent_id,whatsapp_instance,status",
                    "order": "created_at.desc",
                },
                headers=_headers(),
                attempts=2,
            )
            if response.status_code != 200:
                return False

            conversations = response.json() if isinstance(response.json(), list) else []
            if not conversations:
                return False

            # Transferencia explicita: conversa aberta ja pertence a humano.
            for conv in conversations:
                agent_id = conv.get("agent_id")
                instance = conv.get("whatsapp_instance")
                if (
                    conv.get("status") == "open"
                    and agent_id
                    and agent_id != BRUNO_AGENT_ID
                    and instance != WHATSAPP_INSTANCE
                ):
                    logger.info("[HANDOFF] Conversa aberta atribuida a humano para %s", phone_clean)
                    return True

            conv_ids = [row["id"] for row in conversations if row.get("id")]
            ids_filter = "in.(" + ",".join(conv_ids) + ")"

            # Descobre a mensagem mais recente do cliente. Mensagens humanas
            # anteriores a ela sao historico e nao bloqueiam a nova resposta.
            inbound_response = await _request_with_retry(
                client,
                "GET",
                f"{SUPABASE_URL}/rest/v1/messages",
                params={
                    "conversation_id": ids_filter,
                    "is_from_contact": "eq.true",
                    "created_at": f"gte.{cutoff}",
                    "select": "created_at",
                    "order": "created_at.desc",
                    "limit": 1,
                },
                headers=_headers(),
                attempts=2,
            )
            inbound_rows = inbound_response.json() if inbound_response.status_code == 200 else []
            latest_inbound = inbound_rows[0].get("created_at") if isinstance(inbound_rows, list) and inbound_rows else None
            if not latest_inbound:
                return False

            human_response = await _request_with_retry(
                client,
                "GET",
                f"{SUPABASE_URL}/rest/v1/messages",
                params={
                    "conversation_id": ids_filter,
                    "is_from_contact": "eq.false",
                    "sender_id": f"neq.{BRUNO_AGENT_ID}",
                    "created_at": f"gt.{latest_inbound}",
                    "select": "id,created_at,sender_id",
                    "order": "created_at.desc",
                    "limit": 1,
                },
                headers=_headers(),
                attempts=2,
            )
            human_rows = human_response.json() if human_response.status_code == 200 else []
            found = isinstance(human_rows, list) and bool(human_rows)
            if found:
                logger.info("[HANDOFF] Humano respondeu depois do cliente para %s", phone_clean)
            return found
    except Exception as exc:
        logger.error("[CRM Inbox] Falha ao checar humano ativo (%s): %s", phone, exc)
        return False


async def log_message(
    phone: str,
    content: str,
    is_from_contact: bool,
    nome: Optional[str] = None,
    msg_type: str = "text",
    media_url: Optional[str] = None,
    whatsapp_id: Optional[str] = None,
) -> bool:
    """Espelha uma mensagem e identifica Bruno como agente nas mensagens de saida."""
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        logger.warning("[CRM Inbox] SUPABASE_SERVICE_ROLE_KEY ausente; espelhamento ignorado")
        return False

    phone_clean = _normalize_phone(phone)
    if not phone_clean or not content:
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            contact_id = await _get_or_create_contact(client, phone_clean, nome)
            if not contact_id:
                raise RuntimeError("contato nao criado/localizado")

            conversation_id, current_unread = await _get_or_create_conversation(client, phone_clean, contact_id)
            if not conversation_id:
                raise RuntimeError("conversa nao criada/localizada")

            message_payload = {
                "org_id": ORG_ID,
                "conversation_id": conversation_id,
                "content": content[:4000],
                "is_from_contact": is_from_contact,
                "type": msg_type,
                "media_url": media_url,
                "whatsapp_id": whatsapp_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if not is_from_contact:
                message_payload["sender_id"] = BRUNO_AGENT_ID

            message_response = await _request_with_retry(
                client,
                "POST",
                f"{SUPABASE_URL}/rest/v1/messages",
                headers=_headers(),
                json=message_payload,
            )
            if message_response.status_code >= 300 and message_response.status_code != 409:
                raise RuntimeError(f"mensagem recusada: HTTP {message_response.status_code} {message_response.text[:300]}")

            patch_body = {
                "last_message": content[:300],
                "last_message_at": datetime.now(timezone.utc).isoformat(),
                "whatsapp_instance": WHATSAPP_INSTANCE,
                "agent_id": BRUNO_AGENT_ID,
            }
            if is_from_contact:
                patch_body["unread_count"] = current_unread + 1

            patch_response = await _request_with_retry(
                client,
                "PATCH",
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={"id": f"eq.{conversation_id}"},
                headers=_headers(prefer_representation=False),
                json=patch_body,
            )
            if patch_response.status_code >= 300:
                raise RuntimeError(f"conversa nao atualizada: HTTP {patch_response.status_code}")
            return True

    except Exception as exc:
        logger.error("[CRM Inbox] Falha ao espelhar mensagem (%s): %s", phone, exc, exc_info=True)
        return False
