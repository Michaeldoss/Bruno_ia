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


async def human_active_recently(phone: str, window_hours: int = 12) -> bool:
    """Verifica se um AGENTE HUMANO (nao o Bruno) respondeu esse telefone
    recentemente em alguma conversa do CRM (qualquer whatsapp_instance,
    exceto 'bruno-ia').

    Existia so a trava de handoff FORTE (lead_state.stage == "closed"),
    setada pelo proprio Bruno quando ELE decide encerrar. Isso nao cobre
    o caso de um vendedor ou o Michael assumirem a conversa manualmente
    pelo CRM/WhatsApp direto -- o Bruno nao tinha como saber disso e
    continuava respondendo por cima (foi o que aconteceu com a Jucania:
    o Bruno tinha feito "handoff fraco" -- so retem o lead sem fechar --
    entao na proxima mensagem dela ele voltou a falar, inclusive se
    apresentando como "Michael", por cima do David que ja estava
    atendendo).

    Retorna True se achar mensagem is_from_contact=false, de uma
    conversa que NAO e do Bruno, dentro da janela -- ou seja, um humano
    respondeu de verdade recentemente. Falha aberta (retorna False, ou
    seja "pode responder") em qualquer erro de rede/API pra nao travar
    o atendimento por causa de uma instabilidade do CRM.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return False

    phone_clean = _normalize_phone(phone)
    if not phone_clean:
        return False

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            # Todas as conversas desse telefone, qualquer instancia --
            # o numero do Bruno (Twilio) e o MESMO canal usado quando um
            # humano responde manualmente pelo Inbox do CRM, entao nao da
            # pra filtrar por whatsapp_instance nem confiar no agent_id
            # da conversa (e um campo mutavel em nivel de conversa, nao
            # de mensagem, e pode ficar "contaminado" -- ex: uma conversa
            # criada pelo Bruno que depois teve o agent_id corrigido).
            #
            # O sinal confiavel e por MENSAGEM: sender_id so vem
            # preenchido quando um humano loga no CRM e manda a mensagem
            # por la (fica com o profile id de quem mandou). As mensagens
            # que o proprio Bruno espelha (log_message, acima) NUNCA
            # setam sender_id -- ficam sempre null. Entao "existe mensagem
            # is_from_contact=false com sender_id preenchido, recente" =
            # humano respondeu de verdade, nao interessa a instancia.
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={
                    "whatsapp_phone": f"eq.{phone_clean}",
                    "org_id": f"eq.{ORG_ID}",
                    "select": "id",
                },
                headers=_headers(),
            )
            if r.status_code != 200:
                return False
            conv_ids = [row["id"] for row in r.json()] if isinstance(r.json(), list) else []
            if not conv_ids:
                return False

            ids_filter = "(" + ",".join(conv_ids) + ")"
            r2 = await client.get(
                f"{SUPABASE_URL}/rest/v1/messages",
                params={
                    "conversation_id": f"in.{ids_filter}",
                    "is_from_contact": "eq.false",
                    "sender_id": "not.is.null",
                    "created_at": f"gte.{cutoff}",
                    "select": "id,created_at",
                    "limit": 1,
                },
                headers=_headers(),
            )
            if r2.status_code != 200:
                return False
            rows = r2.json()
            found = isinstance(rows, list) and len(rows) > 0
            if found:
                logger.info(f"[HANDOFF] Humano ativo recentemente pra {phone_clean} -- Bruno vai ficar quieto e so espelhar.")
            return found
    except Exception as e:
        logger.error(f"[CRM Inbox] Falha ao checar humano ativo ({phone}): {e}")
        return False


async def criar_lead_no_pipeline(
    phone: str,
    nome: Optional[str] = None,
    cidade: Optional[str] = None,
    email: Optional[str] = None,
    resumo: Optional[str] = None,
    finalizado: bool = False,
) -> bool:
    """Cria o card no funil do Doss CRM quando o Bruno qualifica o lead.

    Ate agora o Bruno so criava card no Arcca/Atenderbem (sistema antigo).
    O lead qualificado aparecia na Inbox mas nunca no pipeline, entao
    ninguem trabalhava -- foi exatamente a queixa do Michael.

    Tambem aproveita para gravar nome/cidade/e-mail no contato: sem isso
    o cliente fica salvo como o proprio numero ("554396044243") e o
    vendedor abre o card sem saber com quem esta falando.

    Nunca levanta excecao: falha aqui nao pode derrubar o atendimento.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return False

    phone_clean = _normalize_phone(phone)
    if not phone_clean:
        return False

    PIPELINE_COMERCIAL = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    STAGE_CONTATO_INICIADO = "05cdab10-c222-4feb-bdcf-a081c7392256"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            contact_id = await _get_or_create_contact(client, phone_clean, nome)
            if not contact_id:
                return False

            # completa o cadastro com o que o cliente informou na conversa
            patch = {}
            if nome and nome != phone_clean and len(str(nome).strip()) > 2:
                patch["name"] = str(nome).strip()
            if cidade:
                patch["address_city"] = str(cidade).strip()
            if email:
                patch["email"] = str(email).strip()
            if patch:
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/contacts",
                    params={"id": f"eq.{contact_id}"},
                    headers=_headers(),
                    json=patch,
                )

            # ja existe card para esse contato? nao duplica
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/pipeline_leads",
                params={"contact_id": f"eq.{contact_id}", "select": "id", "limit": 1},
                headers=_headers(),
            )
            if r.status_code == 200 and isinstance(r.json(), list) and r.json():
                return True

            # respeita remocao manual: se alguem tirou esse contato do
            # funil de proposito, o Bruno nao devolve por conta propria
            rem = await client.get(
                f"{SUPABASE_URL}/rest/v1/pipeline_leads_removidos",
                params={"contact_id": f"eq.{contact_id}", "select": "id", "limit": 1},
                headers=_headers(),
            )
            if rem.status_code == 200 and isinstance(rem.json(), list) and rem.json():
                logger.info(f"[CRM] {phone_clean} foi removido do funil manualmente -- Bruno nao recria.")
                return False

            # dono: o agente que ja atende essa conversa, se houver
            owner_id = None
            conv = await client.get(
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={
                    "whatsapp_phone": f"eq.{phone_clean}",
                    "org_id": f"eq.{ORG_ID}",
                    "select": "id,agent_id",
                    "order": "created_at.desc",
                    "limit": 1,
                },
                headers=_headers(),
            )
            conversation_id = None
            if conv.status_code == 200 and isinstance(conv.json(), list) and conv.json():
                conversation_id = conv.json()[0].get("id")
                owner_id = conv.json()[0].get("agent_id")

            titulo = (str(nome).strip() if nome and nome != phone_clean else phone_clean)
            if finalizado:
                titulo = f"{titulo} - pronto para vendedor"

            novo = await client.post(
                f"{SUPABASE_URL}/rest/v1/pipeline_leads",
                headers={**_headers(), "Prefer": "return=representation"},
                json={
                    "org_id": ORG_ID,
                    "contact_id": contact_id,
                    "conversation_id": conversation_id,
                    "pipeline_id": PIPELINE_COMERCIAL,
                    "stage_id": STAGE_CONTATO_INICIADO,
                    "owner_id": owner_id,
                    "status": "active",
                    "title": titulo,
                    "origin": "Bruno IA",
                },
            )
            if novo.status_code >= 300:
                logger.error(f"[CRM] Falha ao criar card ({novo.status_code}): {novo.text[:200]}")
                return False

            lead_id = novo.json()[0]["id"] if isinstance(novo.json(), list) and novo.json() else None

            # resumo da conversa vira nota no card, pro vendedor nao
            # precisar reler a Inbox inteira
            if lead_id and resumo:
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/lead_notes",
                    headers=_headers(),
                    json={"org_id": ORG_ID, "lead_id": lead_id, "content": resumo[:4000]},
                )

            logger.info(f"[CRM] Card criado no funil para {phone_clean} ({titulo})")
            return True
    except Exception as e:
        logger.error(f"[CRM] Erro ao criar card no funil ({phone}): {e}")
        return False


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
        # De proposito: nunca propaga pro atendimento real. Mas antes
        # so desistia na primeira falha -- se fosse um timeout de rede
        # passageiro, a mensagem do cliente sumia do CRM pra sempre,
        # sem ninguem saber, criando buraco na conversa (alguem olhando
        # o CRM via achar que o Bruno respondeu algo do nada). Agora
        # tenta mais uma vez antes de desistir de verdade.
        logger.error(f"[CRM Inbox] Falha ao espelhar mensagem ({phone}), tentando de novo: {e}")
        try:
            await asyncio.sleep(2)
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
        except Exception as e2:
            logger.error(f"[CRM Inbox] Falha definitiva ao espelhar mensagem ({phone}) mesmo na 2a tentativa: {e2}")

