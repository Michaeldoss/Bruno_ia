import asyncio
import logging
import re
from typing import Optional

import httpx

from app.config import get_settings
from app.models.database import LeadState, SessionLocal

settings = get_settings()
logger = logging.getLogger(__name__)

DOSS_CRM_URL = getattr(settings, "DOSS_CRM_LEADS_URL", "https://doss-crm.vercel.app/api/leads/create")
DOSS_CRM_KEY = getattr(settings, "BRUNO_API_KEY", None)
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_VALID_FINAL_HANDOFF = {"sent_and_transferred", "already_assigned"}

if not DOSS_CRM_KEY:
    logger.critical(
        "Doss CRM: BRUNO_API_KEY nao configurada no ambiente do Bruno. "
        "A entrega de leads ao CRM permanecera bloqueada."
    )


def _clean(value) -> str:
    return str(value or "").strip()


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


def _valid_name(name: str, phone: str) -> bool:
    clean_name = _clean(name)
    if len(clean_name) < 2:
        return False
    if re.sub(r"\D", "", clean_name) == _normalize_phone(phone):
        return False
    if clean_name.isdigit():
        return False
    return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", clean_name))


def _failure(finalizado: bool, status: str, message: str, **extra) -> dict:
    """No fechamento forte, falhar interrompe o fluxo antes de `stage=closed`.

    O chamador atual só grava `closed` depois que esta função retorna. Ao lançar
    exceção em uma entrega final, evitamos encerrar falsamente o lead. No handoff
    fraco continuamos retornando `ok=False`, porque ele é apenas uma retenção.
    """
    result = {
        "ok": False,
        "status": status,
        "agent_name": None,
        "agent_phone": None,
        **extra,
    }
    if finalizado:
        raise RuntimeError(message)
    return result


def _load_attribution(phone: str) -> dict:
    db = SessionLocal()
    try:
        state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if not state:
            return {}
        return {
            "canal": state.origin_channel,
            "campanha": state.campaign_name,
            "conjunto_anuncios": state.adset_name,
            "anuncio": state.ad_name,
            "formulario": state.form_name,
            "utm_source": state.utm_source,
            "utm_medium": state.utm_medium,
            "utm_campaign": state.utm_campaign,
            "utm_content": state.utm_content,
            "utm_term": state.utm_term,
            "pagina_origem": state.landing_page,
            "referrer": state.referrer,
            "twilio_from": state.twilio_from,
            "twilio_to": state.twilio_to,
        }
    except Exception as exc:
        logger.warning("Nao foi possivel carregar atribuicao de %s: %s", phone, exc)
        return {}
    finally:
        db.close()


async def _post_with_retry(payload: dict, attempts: int = 4) -> httpx.Response:
    last_error: Optional[Exception] = None
    timeout = httpx.Timeout(20.0, connect=6.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    DOSS_CRM_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-bruno-key": DOSS_CRM_KEY,
                        "Idempotency-Key": f"bruno:{payload['phone']}:{'final' if payload['finalizado'] else 'retido'}",
                    },
                )
                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                last_error = RuntimeError(f"CRM HTTP {response.status_code}: {response.text[:300]}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc

            logger.warning(
                "Doss CRM falhou na tentativa %s/%s para %s: %s",
                attempt,
                attempts,
                payload.get("phone"),
                last_error,
            )
            if attempt < attempts:
                await asyncio.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))

    raise RuntimeError(f"Falha definitiva ao chamar o Doss CRM: {last_error}") from last_error


async def escalate_to_human(
    phone: str,
    name: str,
    summary: str,
    produto: str = "",
    cidade: str = "",
    origem: str = "Bruno IA",
    valor_estimado: int = 0,
    tecnologia: str = "",
    perfil: str = "",
    serasa_nota: str = "",
    mensagens: Optional[list] = None,
    serasa_cnpj: Optional[str] = None,
    serasa_score: Optional[int] = None,
    serasa_negativos: Optional[bool] = None,
    serasa_regime: Optional[str] = None,
    serasa_nivel: Optional[str] = None,
    serasa_recomendacao: Optional[str] = None,
    serasa_fatores: Optional[str] = None,
    finalizado: bool = True,
    email: Optional[str] = None,
) -> dict:
    if not DOSS_CRM_KEY:
        return _failure(finalizado, "missing_key", "Doss CRM: BRUNO_API_KEY ausente")

    phone_clean = _normalize_phone(phone)
    if len(phone_clean) < 12:
        return _failure(finalizado, "invalid_phone", f"Telefone invalido para entrega: {phone}")

    if not _valid_name(name, phone_clean):
        logger.warning("Doss CRM: nome ainda invalido para %s", phone_clean)
        return _failure(finalizado, "invalid_name", f"Nome invalido para entrega do lead {phone_clean}")

    attribution = {k: v for k, v in _load_attribution(phone_clean).items() if _clean(v)}
    origin_value = _clean(attribution.get("canal")) or _clean(origem) or "Bruno IA"

    payload = {
        "phone": phone_clean,
        "nome": _clean(name),
        "produto": _clean(produto),
        "cidade": _clean(cidade),
        "origem": origin_value,
        "valor_estimado": valor_estimado if isinstance(valor_estimado, (int, float)) and valor_estimado > 0 else 0,
        "tecnologia": _clean(tecnologia),
        "perfil": _clean(perfil),
        "resumo": _clean(summary),
        "serasa_nota": _clean(serasa_nota),
        "mensagens": mensagens or [],
        "serasa_cnpj": serasa_cnpj,
        "serasa_score": serasa_score,
        "serasa_negativos": serasa_negativos,
        "serasa_regime": serasa_regime,
        "serasa_nivel": serasa_nivel,
        "serasa_recomendacao": serasa_recomendacao,
        "serasa_fatores": serasa_fatores,
        "finalizado": bool(finalizado),
        "email": _clean(email) or None,
        **attribution,
    }

    try:
        response = await _post_with_retry(payload)
    except Exception as exc:
        logger.error("Doss CRM: falha definitiva para %s: %s", phone_clean, exc, exc_info=True)
        return _failure(finalizado, "transport_error", f"Falha de transporte ao entregar {phone_clean}: {exc}")

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:500]}

    if response.status_code == 200 and data.get("success"):
        handoff_status = data.get("handoff_status")
        if finalizado and handoff_status not in _VALID_FINAL_HANDOFF:
            logger.error("CRM criou o lead, mas handoff nao concluiu para %s: %s", phone_clean, handoff_status)
            return _failure(
                True,
                "handoff_not_completed",
                f"Handoff do lead {phone_clean} nao concluido: {handoff_status}",
                contact_id=data.get("contact_id"),
                pipeline_lead_id=data.get("pipeline_lead_id"),
                handoff_status=handoff_status,
            )

        logger.info(
            "Doss CRM: lead entregue contact_id=%s pipeline_lead_id=%s agente=%s handoff=%s",
            data.get("contact_id"),
            data.get("pipeline_lead_id"),
            data.get("assigned_agent_id"),
            handoff_status,
        )
        return {
            "ok": True,
            "status": data.get("status", "qualified_and_delivered"),
            "agent_name": data.get("assigned_agent_name"),
            "agent_phone": data.get("assigned_agent_phone"),
            "handoff_status": handoff_status,
            "contact_id": data.get("contact_id"),
            "pipeline_lead_id": data.get("pipeline_lead_id"),
        }

    if response.status_code == 202:
        missing = data.get("missing", [])
        logger.warning("Doss CRM manteve %s em qualificacao: %s", phone_clean, missing)
        return _failure(
            finalizado,
            "qualifying",
            f"Lead {phone_clean} ainda incompleto: {missing}",
            missing=missing,
        )

    logger.error("Doss CRM recusou lead %s: HTTP %s | %s", phone_clean, response.status_code, str(data)[:500])
    return _failure(
        finalizado,
        "crm_rejected",
        f"CRM recusou lead {phone_clean}: HTTP {response.status_code}",
        http_status=response.status_code,
    )


arcca_client = escalate_to_human
