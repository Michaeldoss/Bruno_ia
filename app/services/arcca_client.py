import requests
import asyncio
import logging
from typing import Optional
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

BASE        = "https://doss.atenderbem.com"
GLOBAL_KEY  = "69cdb59aa90bf334b9ce6e7f84e337c5"
QUEUE_ID    = 10   # Vendas04
QUEUE_KEY   = getattr(settings, "ARCCA_QUEUE_KEY", "7ca9750e11404d9b953723e698d15be0")
PIPELINE_ID = 2    # CRM DOSS 2025
STAGE_ID    = 8    # NOVO - estagio de entrada de leads
RESPONSAVEL_ID = 2 # ID do vendedor responsavel


def _post(path: str, payload: dict) -> Optional[dict]:
    try:
        r = requests.post(
            BASE + path,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=8
        )
        if r.status_code in (200, 201) and r.text.strip():
            return r.json()
        logger.error(f"Arcca {path}: {r.status_code} | {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Arcca {path} excecao: {e}")
        return None


def _search_contact_sync(phone: str) -> Optional[int]:
    """Busca contato pelo telefone."""
    phone_clean = phone.replace("whatsapp:", "").replace("+55", "").replace("+", "").replace("-", "").replace(" ", "")
    data = _post("/int/searchContact", {
        "queueId": QUEUE_ID,
        "apiKey":  QUEUE_KEY,
        "searchField": "number",
        "searchValue": phone_clean
    })
    if data and isinstance(data, dict):
        return data.get("id")
    return None


def _create_contact_sync(phone: str, name: str) -> Optional[int]:
    """Cria novo contato."""
    phone_clean = phone.replace("whatsapp:", "").replace("+55", "").replace("+", "").replace("-", "").replace(" ", "")
    data = _post("/int/addContact", {
        "queueId": QUEUE_ID,
        "apiKey":  QUEUE_KEY,
        "name":    name or phone_clean,
        "number":  phone_clean
    })
    if data and isinstance(data, dict):
        return data.get("contactId")
    return None


def _create_opportunity_sync(
    title: str,
    phone: str,
    contact_id: Optional[int],
    summary: str,
    valor_estimado: int = 0,
) -> Optional[int]:
    """Cria oportunidade no CRM."""
    phone_clean = phone.replace("whatsapp:", "").replace("+55", "").replace("+", "").replace("-", "").replace(" ", "")
    payload = {
        "queueId":      QUEUE_ID,
        "apiKey":       QUEUE_KEY,
        "fkPipeline":   PIPELINE_ID,
        "fkStage":      STAGE_ID,
        "responsableid": RESPONSAVEL_ID,
        "title":        title,
        "mainphone":    phone_clean,
        "description":  summary,
    }
    if contact_id:
        payload["contacts"] = [contact_id]
    if valor_estimado and valor_estimado > 0:
        payload["value"] = valor_estimado

    data = _post("/int/createOpportunity", payload)
    if data and isinstance(data, dict):
        return data.get("id")
    return None


def _add_note_sync(opportunity_id: int, note: str) -> bool:
    """Adiciona nota na oportunidade."""
    data = _post("/int/insertOpportunityNote", {
        "queueId": QUEUE_ID,
        "apiKey":  QUEUE_KEY,
        "id":      opportunity_id,
        "note":    note
    })
    return data is not None


def _add_tag_sync(opportunity_id: int, tag: str) -> bool:
    """Tenta adicionar tag/etiqueta na oportunidade."""
    # Tenta endpoint de tag — falha graciosamente se nao suportado
    try:
        data = _post("/int/addOpportunityTag", {
            "queueId": QUEUE_ID,
            "apiKey":  QUEUE_KEY,
            "id":      opportunity_id,
            "tag":     tag
        })
        return data is not None
    except Exception:
        return False


def _set_origin_sync(opportunity_id: int, origem: str) -> bool:
    """Tenta definir origem do lead na oportunidade."""
    try:
        data = _post("/int/updateOpportunity", {
            "queueId": QUEUE_ID,
            "apiKey":  QUEUE_KEY,
            "id":      opportunity_id,
            "origin":  origem
        })
        return data is not None
    except Exception:
        return False


async def escalate_to_human(
    phone: str,
    name: str,
    summary: str,
    produto: str = "",
    cidade: str = "",
    origem: str = "WhatsApp Direto",
    valor_estimado: int = 0,
    tecnologia: str = "",
    perfil: str = "",
    serasa_nota: str = "",
) -> bool:
    """
    Cria card completo no CRM Arcca quando Bruno encerra a conversa.
    Preenche: contato, oportunidade, nota principal, nota Serasa separada,
    origem, tags e valor estimado.
    """
    if QUEUE_KEY in ("stub", "", None):
        logger.warning("Arcca: ARCCA_QUEUE_KEY nao configurada no .env")
        return False

    try:
        # ── Título rico do card ───────────────────────────────────────────
        partes_titulo = ["Lead WhatsApp", name or phone]
        if produto:
            partes_titulo.append(produto)
        if cidade:
            partes_titulo.append(cidade)
        title = " - ".join(partes_titulo)

        # ── Busca ou cria contato ─────────────────────────────────────────
        contact_id = await asyncio.to_thread(_search_contact_sync, phone)
        if not contact_id:
            contact_id = await asyncio.to_thread(_create_contact_sync, phone, name)
            if contact_id:
                logger.info(f"Arcca: contato criado ID {contact_id}")
        else:
            logger.info(f"Arcca: contato encontrado ID {contact_id}")

        # ── Cria oportunidade ─────────────────────────────────────────────
        opp_id = await asyncio.to_thread(
            _create_opportunity_sync, title, phone, contact_id, summary, valor_estimado
        )
        if not opp_id:
            return False

        logger.info(f"Arcca: oportunidade criada ID {opp_id}")

        # ── Nota principal: resumo da conversa ────────────────────────────
        await asyncio.to_thread(_add_note_sync, opp_id, summary)

        # ── Nota secundária: Serasa (se disponível) ───────────────────────
        if serasa_nota:
            nota_serasa = f"=== ANALISE SERASA EXPERIAN ===\n\n{serasa_nota}"
            await asyncio.to_thread(_add_note_sync, opp_id, nota_serasa)

        # ── Tenta definir origem ──────────────────────────────────────────
        if origem:
            await asyncio.to_thread(_set_origin_sync, opp_id, origem)

        # ── Tenta adicionar tags ──────────────────────────────────────────
        for tag in filter(None, [tecnologia, perfil]):
            await asyncio.to_thread(_add_tag_sync, opp_id, tag)

        return True

    except Exception as e:
        logger.error(f"Arcca escalate_to_human: {e}")
        return False


# Alias para compatibilidade com openai_client.py
arcca_client = escalate_to_human
