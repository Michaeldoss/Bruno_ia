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
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.config import get_settings
from app.models.database import SessionLocal, CrmSyncQueue, PipelineSyncQueue

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
    etapa_nome: Optional[str] = None,
    produto: Optional[str] = None,
) -> bool:
    """Cria o card no funil do Doss CRM quando o Bruno qualifica o lead.

    Ate agora o Bruno so criava card no Arcca/Atenderbem (sistema antigo).
    O lead qualificado aparecia na Inbox mas nunca no pipeline, entao
    ninguem trabalhava -- foi exatamente a queixa do Michael.

    Tambem aproveita para gravar nome/cidade/e-mail no contato: sem isso
    o cliente fica salvo como o proprio numero ("554396044243") e o
    vendedor abre o card sem saber com quem esta falando.

    etapa_nome: se informado, move o card pra essa etapa do Funil
    Comercial -- MAS SO PRA FRENTE, nunca regride uma etapa que um
    humano ja avancou manualmente no board (compara pela posicao/ordem
    configurada da etapa, nao so pelo nome).

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

            # resolve o stage_id alvo (se pedido) buscando pela posicao,
            # pra poder comparar "pra frente ou pra tras" com a etapa atual
            etapa_alvo_id, etapa_alvo_pos = None, None
            if etapa_nome:
                st = await client.get(
                    f"{SUPABASE_URL}/rest/v1/pipeline_stages",
                    params={"pipeline_id": f"eq.{PIPELINE_COMERCIAL}", "name": f"eq.{etapa_nome}", "select": "id,position", "limit": 1},
                    headers=_headers(),
                )
                if st.status_code == 200 and isinstance(st.json(), list) and st.json():
                    etapa_alvo_id = st.json()[0]["id"]
                    etapa_alvo_pos = st.json()[0]["position"]

            # ja existe card para esse contato? enriquece em vez de so confirmar
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/pipeline_leads",
                params={"contact_id": f"eq.{contact_id}", "status": "eq.active", "select": "id,stage_id,product", "limit": 1},
                headers=_headers(),
            )
            if r.status_code == 200 and isinstance(r.json(), list) and r.json():
                card = r.json()[0]
                patch_card = {}
                if produto and not card.get("product"):
                    patch_card["product"] = produto
                if etapa_alvo_id:
                    st_atual = await client.get(
                        f"{SUPABASE_URL}/rest/v1/pipeline_stages",
                        params={"id": f"eq.{card['stage_id']}", "select": "position", "limit": 1},
                        headers=_headers(),
                    )
                    pos_atual = st_atual.json()[0]["position"] if st_atual.status_code == 200 and st_atual.json() else 0
                    if etapa_alvo_pos > pos_atual:
                        patch_card["stage_id"] = etapa_alvo_id
                if patch_card:
                    await client.patch(
                        f"{SUPABASE_URL}/rest/v1/pipeline_leads",
                        params={"id": f"eq.{card['id']}"},
                        headers=_headers(),
                        json=patch_card,
                    )
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
                    "stage_id": etapa_alvo_id or STAGE_CONTATO_INICIADO,
                    "owner_id": owner_id,
                    "status": "active",
                    "title": titulo,
                    "origin": "Bruno IA",
                    "product": produto,
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


async def _sincronizar_uma_vez(
    phone_clean: str, content: str, is_from_contact: bool,
    msg_type: str, media_url: Optional[str], whatsapp_id: Optional[str], nome: Optional[str],
) -> None:
    """Uma tentativa de sincronizar com o CRM. Lança exceção se falhar --
    quem chama decide o que fazer (marcar synced ou deixar pro worker)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        contact_id = await _get_or_create_contact(client, phone_clean, nome)
        if not contact_id:
            raise RuntimeError("nao foi possivel obter/criar contact_id")
        conversation_id, current_unread = await _get_or_create_conversation(client, phone_clean, contact_id)
        if not conversation_id:
            raise RuntimeError("nao foi possivel obter/criar conversation_id")

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


async def buscar_memoria_ia(phone: str) -> Optional[dict]:
    """Busca e junta a analise mais recente que a IA supervisora ja fez
    de TODAS as conversas desse contato -- nao so a conversa atual do
    Bruno. O mesmo cliente pode ja ter falado com um vendedor humano
    (David, Michael, etc) em outra conversa/instancia de WhatsApp, e o
    que foi apurado la (fatos, produtos, objecoes, promessas, proximos
    passos) tambem precisa alimentar o Bruno -- senao ele repete
    pergunta ou ignora combinado que so aconteceu em outro canal.

    Junta as listas (facts/products/objections/promises/next_steps/
    preferences) de todas as conversas do contato, sem duplicar, e usa
    a "situacao atual" (recommended_action) da analise mais recente
    entre todas elas. Nunca levanta excecao -- se falhar, Bruno segue
    sem essa memoria extra, como sempre funcionou.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return None

    phone_clean = _normalize_phone(phone)
    if not phone_clean:
        return None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # 1. Acha o contato (pode ter varias conversas em instancias
            # diferentes -- precisa do contact_id, nao so de UMA conversa).
            contato = await client.get(
                f"{SUPABASE_URL}/rest/v1/contacts",
                params={
                    "phone": f"eq.{phone_clean}",
                    "org_id": f"eq.{ORG_ID}",
                    "select": "id",
                    "limit": 1,
                },
                headers=_headers(),
            )
            if contato.status_code != 200 or not isinstance(contato.json(), list) or not contato.json():
                return None
            contact_id = contato.json()[0]["id"]

            # 2. TODAS as conversas desse contato, em qualquer instancia.
            convs = await client.get(
                f"{SUPABASE_URL}/rest/v1/conversations",
                params={
                    "contact_id": f"eq.{contact_id}",
                    "org_id": f"eq.{ORG_ID}",
                    "select": "id",
                },
                headers=_headers(),
            )
            if convs.status_code != 200 or not isinstance(convs.json(), list) or not convs.json():
                return None
            conversation_ids = [c["id"] for c in convs.json()]

            # 3. Analise mais recente de CADA uma dessas conversas (nao so
            # a mais recente entre todas -- uma conversa antiga com o
            # David pode ter um fato que a conversa nova com o Bruno
            # ainda nao tem).
            id_list = ",".join(conversation_ids)
            mems = await client.get(
                f"{SUPABASE_URL}/rest/v1/conversation_ai_memory",
                params={
                    "conversation_id": f"in.({id_list})",
                    "select": "conversation_id,analyzed_at,summary,recommended_action,memory",
                    "order": "analyzed_at.desc",
                },
                headers=_headers(),
            )
            if mems.status_code != 200 or not isinstance(mems.json(), list) or not mems.json():
                return None

            registros = mems.json()
            # Uma analise mais recente por conversation_id (a tabela pode
            # ter historico de varias analises da mesma conversa).
            por_conversa = {}
            for r in registros:
                cid = r.get("conversation_id")
                if cid and cid not in por_conversa:
                    por_conversa[cid] = r
            analises = list(por_conversa.values())
            if not analises:
                return None

            # Junta as listas de memoria de todas as conversas, sem duplicar.
            memoria_unificada = {"facts": [], "products": [], "objections": [], "promises": [], "next_steps": [], "preferences": []}
            for analise in analises:
                mem_data = analise.get("memory") if isinstance(analise.get("memory"), dict) else {}
                for chave in memoria_unificada:
                    itens = mem_data.get(chave)
                    if isinstance(itens, list):
                        for item in itens:
                            if item not in memoria_unificada[chave]:
                                memoria_unificada[chave].append(item)

            # "Situacao atual" vem da analise mais recente entre todas as
            # conversas (ja veio ordenado por analyzed_at desc).
            mais_recente = analises[0]

            return {
                "recommended_action": mais_recente.get("recommended_action"),
                "customer_intent": mais_recente.get("customer_intent"),
                "memory": memoria_unificada,
                "conversas_consideradas": len(analises),
            }
    except Exception as e:
        logger.error(f"[CRM Inbox] Falha ao buscar memoria da IA ({phone}): {e}")
        return None


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
    atendimento real via Twilio. Mas diferente de antes -- quando uma
    falha de rede simplesmente fazia a mensagem sumir do CRM pra
    sempre, sem ninguem saber -- agora toda mensagem grava PRIMEIRO
    numa fila duravel no banco do proprio Bruno (que ele ja depende de
    qualquer forma). A tentativa de sincronizar acontece na hora pra
    a maioria aparecer instantaneamente, mas se falhar, a linha fica
    pendente e o worker em segundo plano (crm_sync_worker_loop) insiste
    ate conseguir -- nunca desiste e nunca perde a mensagem de vista.
    """
    if not SUPABASE_KEY or SUPABASE_KEY == "stub":
        return

    phone_clean = _normalize_phone(phone)
    if not phone_clean or not content:
        return

    db = SessionLocal()
    try:
        fila = CrmSyncQueue(
            phone=phone_clean, content=content[:4000], is_from_contact=is_from_contact,
            msg_type=msg_type, media_url=media_url, whatsapp_id=whatsapp_id, nome=nome,
            synced=False,
        )
        db.add(fila)
        db.commit()
        db.refresh(fila)
    except Exception as e:
        # Se nem isso der certo (banco do proprio Bruno fora do ar),
        # ai sim nao ha o que fazer -- mas isso e muitissimo mais raro
        # que uma falha de rede pontual com o Supabase.
        logger.error(f"[CRM Inbox] Falha ao gravar na fila duravel ({phone}): {e}")
        db.rollback()
        return
    finally:
        db.close()

    try:
        await _sincronizar_uma_vez(phone_clean, content, is_from_contact, msg_type, media_url, whatsapp_id, nome)
        db = SessionLocal()
        try:
            row = db.query(CrmSyncQueue).filter(CrmSyncQueue.id == fila.id).first()
            if row:
                row.synced = True
                row.synced_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
    except Exception as e:
        # Nao desiste aqui -- so loga. A linha fica synced=False na
        # fila, e o worker em segundo plano vai pegar e tentar de novo
        # ate conseguir.
        logger.error(f"[CRM Inbox] Falha ao sincronizar na hora ({phone}), fica na fila pro worker: {e}")


async def sync_lead_com_retry(function_name: str, **kwargs) -> dict:
    """Wrapper com fila durável em volta de enviar_lead_crm/criar_lead_no_pipeline.

    Mesma logica do CrmSyncQueue, mas pro card do pipeline em si: grava
    a intencao no banco do Bruno ANTES de tentar (que quase nunca cai),
    tenta sincronizar na hora, e se falhar (ex: apagao do Supabase),
    deixa pendente pro worker reprocessar ate conseguir de verdade --
    em vez de simplesmente perder o lead pra sempre.
    """
    db = SessionLocal()
    try:
        fila = PipelineSyncQueue(
            function_name=function_name,
            payload_json=json.dumps(kwargs, default=str),
        )
        db.add(fila)
        db.commit()
        db.refresh(fila)
        fila_id = fila.id
    except Exception as e:
        logger.error(f"[Pipeline Sync] Falha ao gravar na fila (segue tentando direto): {e}")
        fila_id = None
    finally:
        db.close()

    try:
        resultado = await _executar_sync_lead(function_name, kwargs)
        if fila_id:
            db = SessionLocal()
            try:
                row = db.query(PipelineSyncQueue).filter(PipelineSyncQueue.id == fila_id).first()
                if row:
                    row.synced = True
                    row.synced_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
        return resultado
    except Exception as e:
        logger.error(f"[Pipeline Sync] Falha ao sincronizar na hora, fica na fila pro worker: {e}")
        return {"ok": False, "agent_name": None, "agent_phone": None}


async def _executar_sync_lead(function_name: str, kwargs: dict):
    """Chama a funcao real (enviar_lead_crm ou criar_lead_no_pipeline)."""
    if function_name == "enviar_lead_crm":
        from app.services.doss_crm_client import enviar_lead_crm
        return await enviar_lead_crm(**kwargs)
    elif function_name == "criar_lead_no_pipeline":
        return await criar_lead_no_pipeline(**kwargs)
    raise ValueError(f"Funcao desconhecida na fila de sync: {function_name}")





async def enviar_lead_crm_com_retry(*args, **kwargs) -> dict:
    """Drop-in pra enviar_lead_crm, mas com fila durável por baixo --
    ver sync_lead_com_retry. Aceita os mesmos argumentos posicionais/
    nomeados que a funcao original (escalate_to_human)."""
    from app.services.doss_crm_client import escalate_to_human
    import inspect
    params = list(inspect.signature(escalate_to_human).parameters.keys())
    merged = dict(zip(params, args))
    merged.update(kwargs)
    return await sync_lead_com_retry("enviar_lead_crm", **merged)


async def criar_lead_no_pipeline_com_retry(*args, **kwargs) -> bool:
    """Drop-in pra criar_lead_no_pipeline, com fila durável por baixo."""
    import inspect
    params = list(inspect.signature(criar_lead_no_pipeline).parameters.keys())
    merged = dict(zip(params, args))
    merged.update(kwargs)
    resultado = await sync_lead_com_retry("criar_lead_no_pipeline", **merged)
    return resultado if isinstance(resultado, bool) else resultado.get("ok", False)


crm_sync_worker_task: asyncio.Task = None


def start_crm_sync_worker():
    global crm_sync_worker_task
    crm_sync_worker_task = asyncio.create_task(crm_sync_worker_loop())
    logger.info("[CRM Sync Worker] Task iniciada com sucesso.")


_supabase_down_desde = None  # None = ok; datetime = desde quando esta fora

async def _checar_saude_supabase():
    """Health-check simples do Supabase, rodando junto do worker que ja
    existe (sem processo novo). Se cair, avisa o Michael via WhatsApp na
    hora -- em vez de so descobrir quando tenta usar o CRM e trava. Se
    voltar, avisa que normalizou tambem, sem precisar ficar testando na
    mao.
    """
    global _supabase_down_desde
    from app.config import get_settings
    from app.services.twilio_client import twilio_service
    settings = get_settings()
    ADMIN_PHONE = "+554797342869"  # Michael

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{settings.SUPABASE_URL}/rest/v1/",
                headers={"apikey": settings.SUPABASE_SERVICE_ROLE_KEY},
            )
        esta_ok = r.status_code < 500
    except Exception:
        esta_ok = False

    if not esta_ok and _supabase_down_desde is None:
        _supabase_down_desde = datetime.utcnow()
        logger.error("[Health Check] Supabase caiu -- avisando Michael.")
        try:
            await twilio_service.send_whatsapp_message(
                ADMIN_PHONE,
                "⚠️ Supabase (CRM) caiu agora. O Bruno continua vendendo "
                "normal (nao depende disso), mas o CRM/pipeline vai ficar "
                "sem sincronizar ate voltar. Nenhum lead se perde -- fica "
                "na fila e sincroniza sozinho quando normalizar.",
            )
        except Exception as e:
            logger.error(f"[Health Check] Falha ao avisar Michael: {e}")
    elif esta_ok and _supabase_down_desde is not None:
        duracao_min = int((datetime.utcnow() - _supabase_down_desde).total_seconds() / 60)
        logger.info(f"[Health Check] Supabase voltou apos {duracao_min} min -- avisando Michael.")
        try:
            await twilio_service.send_whatsapp_message(
                ADMIN_PHONE,
                f"✅ Supabase (CRM) normalizou. Ficou fora do ar por cerca "
                f"de {duracao_min} min. Tudo que ficou pendente ja esta "
                f"sincronizando sozinho.",
            )
        except Exception as e:
            logger.error(f"[Health Check] Falha ao avisar Michael: {e}")
        _supabase_down_desde = None


async def crm_sync_worker_loop():
    """Roda pra sempre em segundo plano, reprocessando mensagens que
    falharam a sincronizacao imediata. Nunca desiste de uma mensagem --
    so avisa o admin se alguma acumular tentativas demais, pra alguem
    checar manualmente o que esta acontecendo."""
    while True:
        try:
            await _checar_saude_supabase()
            db = SessionLocal()
            try:
                pendentes = (
                    db.query(CrmSyncQueue)
                    .filter(CrmSyncQueue.synced == False)  # noqa: E712
                    .order_by(CrmSyncQueue.created_at.asc())
                    .limit(50)
                    .all()
                )
                for item in pendentes:
                    try:
                        await _sincronizar_uma_vez(
                            item.phone, item.content, item.is_from_contact,
                            item.msg_type, item.media_url, item.whatsapp_id, item.nome,
                        )
                        item.synced = True
                        item.synced_at = datetime.utcnow()
                        db.commit()
                    except Exception as e:
                        item.attempts = (item.attempts or 0) + 1
                        item.last_error = str(e)[:500]
                        db.commit()
                        if item.attempts in (20, 50, 100):
                            logger.error(
                                f"[CRM Sync Worker] Mensagem id={item.id} phone={item.phone} "
                                f"ja falhou {item.attempts}x -- precisa de olho humano."
                            )

                # Mesmo ciclo, agora reprocessando cards de pipeline que
                # falharam (ex: durante um apagao do Supabase) -- sem
                # isso, um lead qualificado durante uma instabilidade
                # externa era perdido pra sempre, silenciosamente.
                pendentes_pipeline = (
                    db.query(PipelineSyncQueue)
                    .filter(PipelineSyncQueue.synced == False)  # noqa: E712
                    .order_by(PipelineSyncQueue.created_at.asc())
                    .limit(50)
                    .all()
                )
                for item in pendentes_pipeline:
                    try:
                        kwargs = json.loads(item.payload_json)
                        await _executar_sync_lead(item.function_name, kwargs)
                        item.synced = True
                        item.synced_at = datetime.utcnow()
                        db.commit()
                    except Exception as e:
                        item.attempts = (item.attempts or 0) + 1
                        item.last_error = str(e)[:500]
                        db.commit()
                        if item.attempts in (5, 20, 50):
                            logger.error(
                                f"[Pipeline Sync Worker] Card id={item.id} funcao={item.function_name} "
                                f"ja falhou {item.attempts}x -- precisa de olho humano."
                            )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[CRM Sync Worker] Erro no loop: {e}")
        await asyncio.sleep(30)

