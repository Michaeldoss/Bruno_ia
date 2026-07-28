import asyncio
import logging
import re
from typing import Optional

import httpx

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

BASE_LEADS_URL = getattr(
    settings,
    "DOSS_CRM_LEADS_URL",
    "https://doss-crm.vercel.app/api/leads/create",
)
FOLLOWUP_URL = BASE_LEADS_URL.rsplit("/", 1)[0] + "/followup"
BRUNO_API_KEY = getattr(settings, "BRUNO_API_KEY", "")
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


async def enviar_para_pipeline_followup(
    phone: str,
    nome: str,
    produto: str,
    resumo: str,
    mensagens: Optional[list] = None,
    ultimo_followup_em: Optional[str] = None,
    attempts: int = 4,
) -> dict:
    """Cria ou move o card para Follow-up sem enviar nova mensagem ao cliente."""
    if not BRUNO_API_KEY:
        raise RuntimeError("BRUNO_API_KEY nao configurada para encaminhar follow-up")

    phone_clean = _normalize_phone(phone)
    if len(phone_clean) < 12:
        raise ValueError(f"Telefone invalido para follow-up: {phone}")

    payload = {
        "phone": phone_clean,
        "nome": (nome or "").strip() or phone_clean,
        "produto": (produto or "").strip(),
        "resumo": (resumo or "").strip(),
        "mensagens": mensagens or [],
        "ultimo_followup_em": ultimo_followup_em,
    }

    last_error = None
    timeout = httpx.Timeout(20.0, connect=6.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    FOLLOWUP_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-bruno-key": BRUNO_API_KEY,
                        "Idempotency-Key": f"bruno:{phone_clean}:followup-closed",
                    },
                )
                data = response.json() if response.content else {}
                if response.status_code == 200 and data.get("success"):
                    logger.info(
                        "[FOLLOWUP] Lead %s enviado ao Pipeline/Follow-up. card=%s agente=%s",
                        phone_clean,
                        data.get("pipeline_lead_id"),
                        data.get("assigned_agent_id"),
                    )
                    return data

                if response.status_code not in RETRYABLE_STATUS:
                    raise RuntimeError(
                        f"CRM recusou follow-up: HTTP {response.status_code} {str(data)[:400]}"
                    )
                last_error = RuntimeError(
                    f"CRM temporariamente indisponivel: HTTP {response.status_code}"
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc

            logger.warning(
                "[FOLLOWUP] Tentativa %s/%s de encaminhar %s falhou: %s",
                attempt,
                attempts,
                phone_clean,
                last_error,
            )
            if attempt < attempts:
                await asyncio.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))

    raise RuntimeError(f"Falha definitiva ao enviar follow-up ao CRM: {last_error}")
