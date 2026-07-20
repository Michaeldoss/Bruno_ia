"""
Espelha as mensagens do Bruno (recebidas do cliente e enviadas pelo
Bruno) pro Inbox do Doss CRM em tempo real.

Antes disso: o CRM so ficava sabendo de uma conversa do Bruno quando
ele decidia ESCALAR pra um humano (ver doss_crm_client.escalate_to_human)
-- um "dump" de uma vez so, no final. Enquanto o Bruno ainda estava
conversando, ninguem via nada no CRM.

Agora: cada mensagem (nos dois sentidos) e espelhada na hora, numa
conversa marcada com whatsapp_instance='bruno-ia' e atribuida ao
perfil "Bruno IA" (criado na tela de Administracao do CRM, com
role=agent) -- ele aparece no Inbox exatamente como um agente humano
apareceria.

Reaproveita a MESMA credencial (SUPABASE_SERVICE_ROLE_KEY) que ja
existe no .env do Bruno pra Pesquisa de Satisfacao -- nao precisa de
chave nova.

Design deliberado: nunca derruba o fluxo de atendimento do Bruno se o
espelhamento falhar. So loga o erro. O cliente no WhatsApp nunca deve
notar problema aqui.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY
ORG_ID = "dafa7ea5-08c4-44dd-886e-d58905fca38c"

# Perfil dedicado do Bruno no Doss CRM (Administracao > usuarios).
# Se esse perfil for apagado e recriado, atualizar o id aqui.
BRUNO_AGENT_ID = "31b8f1f2-b509-4319-abfb-989954b3ba25"

# Mesma marca ja usada em doss_crm_client.escalate_to_human -- nao e
# uma instancia Evolution de verdade, so identifica a origem.
WHATSAPP_INSTANCE = "bruno-ia"


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r"[^\d]", "", raw.replace("whatsapp:", ""))


async def _get_or_create_contact(client: httpx.AsyncClient, phone: str, nome: Optional[str]) -> Optional[str]:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/contacts",
        params={"phone": f"eq.{phone}", "org_id": f"eq.{ORG_ID}", "select": "id", "limit": 1},
        headers=_headers(),
    )
    rows = r.json() if r.status_code == 200 else []
    if isinstance(rows, list) and rows:
        return rows[0]["id"]

    r = await client.post(
        f"{SUPABASE_URL}/rest/v1/contacts",
        headers=_headers(),
        json={"org_id": ORG_ID, "name": nome or phone, "phone": phone, "origin": "Bruno IA"},
    )
    if r.status_code >= 300:
        logger.error(f"[CRM Inbox] Falha ao criar contato: {r.status_code} {r.text[:300]}")
        return None
    data = r.json()
    return (data[0]["id"] if isinstance(data, list) else data.get("id")) if data else None


async def _get_or_create_conversation(client: httpx.AsyncClient, phone: str, contact_id: str) -> tuple:
    """Retorna (conversation_id, unread_count_atual)."""
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/conversations",
        params={
            "whatsapp_phone": f"eq.{phone}",
            "org_id": f"eq.{ORG_ID}",
            "whatsapp_instance": f"eq.{WHATSAPP_INSTANCE}",
            "status": "eq.open",
            "select": "id,unread_count",
            "order": "created_at.desc",
            "limit": 1,
        },
        headers=_headers(),
    )
    rows = r.json() if r.status_code == 200 else []
    if isinstance(rows, list) and rows:
        return rows[0]["id"], rows[0].get("unread_count") or 0

    r = await client.post(
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
    if r.status_code >= 300:
        # FIX: agora que o indice unico no banco tambem considera a
        # instancia (whatsapp_phone + org_id + whatsapp_instance), um
        # 409 aqui so acontece por corrida real (dois webhooks quase
        # simultaneos pro Bruno) -- nao mais porque outro agente ja
        # tinha conversa aberta com esse telefone. Por isso o fallback
        # agora TAMBEM filtra por instancia, senao volta a roubar a
        # conversa de outro agente (foi o que causou a saudacao do
        # Bruno aparecer como se fosse do Michael/Assistencia).
        r2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/conversations",
            params={"whatsapp_phone": f"eq.{phone}", "org_id": f"eq.{ORG_ID}", "whatsapp_instance": f"eq.{WHATSAPP_INSTANCE}", "status": "eq.open", "select": "id,unread_count", "limit": 1},
            headers=_headers(),
        )
        rows2 = r2.json() if r2.status_code == 200 else []
        if isinstance(rows2, list) and rows2:
            return rows2[0]["id"], rows2[0].get("unread_count") or 0
        logger.error(f"[CRM Inbox] Falha ao criar conversa: {r.status_code} {r.text[:300]}")
        return None, 0
    data = r.json()
    conv_id = (data[0]["id"] if isinstance(data, list) else data.get("id")) if data else None
    return conv_id, 0


async def log_message(
    phone: str,
    content: str,
    is_from_contact: bool,
    nome: Optional[str] = None,
    msg_type: str = "text",
    media_url: Optional[str] = None,
    whatsapp_id: Optional[str] = None,
) -> None:
    """Espelha uma mensagem (do cliente ou do Bruno) pro Inbox do CRM.

    Chamar isso NUNCA deve travar nem atrasar de forma perceptivel o
    atendimento real via Twilio -- qualquer falha aqui so vira log.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return

    phone_clean = _normalize_phone(phone)
    if not phone_clean or not content:
        return

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            contact_id = await _get_or_create_contact(client, phone_clean, nome)
            if not contact_id:
                return
            conversation_id, current_unread = await _get_or_create_conversation(client, phone_clean, contact_id)
            if not conversation_id:
                return

            await client.post(
                f"{SUPABASE_URL}/rest/v1/messages",
                headers=_headers(),
                json={
                    "org_id": ORG_ID,
                    "conversation_id": conversation_id,
                    "content": content[:4000],
                    "is_from_contact": is_from_contact,
                    "type": msg_type,
                    "media_url": media_url,
                    "whatsapp_id": whatsapp_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            patch_body = {
                "last_message": content[:300],
                "last_message_at": datetime.now(timezone.utc).isoformat(),
            }
            if is_from_contact:
                patch_body["unread_count"] = current_unread + 1

            await client.patch(
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={"id": f"eq.{conversation_id}"},
                headers=_headers(),
                json=patch_body,
            )
    except Exception as e:
        # De proposito: nunca propaga. Espelhamento no CRM e "nice to
        # have", nao pode derrubar o atendimento real do cliente.
        logger.error(f"[CRM Inbox] Falha ao espelhar mensagem ({phone}): {e}")
