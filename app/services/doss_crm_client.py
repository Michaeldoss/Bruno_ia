import httpx
import logging
from typing import Optional
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Endpoint do Doss CRM que recebe leads do Bruno IA. Ver
# api/leads/create.js no repo do Doss CRM para o contrato completo.
DOSS_CRM_URL = getattr(settings, "DOSS_CRM_LEADS_URL", "https://doss-crm.vercel.app/api/leads/create")
DOSS_CRM_KEY = getattr(settings, "BRUNO_API_KEY", None)

if not DOSS_CRM_KEY:
    logger.critical(
        "Doss CRM: BRUNO_API_KEY nao configurada no .env do Bruno. "
        "escalate_to_human vai falhar ate isso ser corrigido."
    )


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
) -> bool:
    """
    Envia o lead pro Doss CRM quando Bruno encerra a conversa.
    Substitui a integracao antiga com o Arcca (arcca_client.py).

    O endpoint do lado do Doss CRM cuida de: criar/achar contato,
    criar conversa, criar card no pipeline em "Novo Lead" com rodizio
    de agente, salvar resumo/analise Serasa como nota atrelada ao
    lead, grava a conversa real (mensagens) na Inbox, e grava o
    resultado do Serasa em campos proprios no CADASTRO DO CONTATO --
    incluindo agora o VEREDITO de risco (nivel, recomendacao de
    condicao de pagamento, fatores considerados), nao so score cru.
    """
    if not DOSS_CRM_KEY:
        logger.error("Doss CRM: BRUNO_API_KEY nao configurada - abortando escalate_to_human")
        return False

    payload = {
        "phone": phone,
        "nome": name or phone,
        "produto": produto,
        "cidade": cidade,
        "origem": origem,
        "valor_estimado": valor_estimado,
        "tecnologia": tecnologia,
        "perfil": perfil,
        "resumo": summary,
        "serasa_nota": serasa_nota,
        "mensagens": mensagens or [],
        "serasa_cnpj": serasa_cnpj,
        "serasa_score": serasa_score,
        "serasa_negativos": serasa_negativos,
        "serasa_regime": serasa_regime,
        "serasa_nivel": serasa_nivel,
        "serasa_recomendacao": serasa_recomendacao,
        "serasa_fatores": serasa_fatores,
    }

    # FIX: era requests.post (SINCRONO/bloqueante) chamado dentro de uma
    # funcao async -- travava o event loop inteiro do Bruno por ate 10s
    # (o timeout) toda vez que um lead fechava, congelando TODAS as outras
    # conversas simultaneas nesse intervalo. Agora usa httpx.AsyncClient,
    # que e async de verdade e nao bloqueia o loop.
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                DOSS_CRM_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-bruno-key": DOSS_CRM_KEY,
                },
            )
        if r.status_code == 200:
            data = r.json()
            logger.info(
                f"Doss CRM: lead criado - contact_id={data.get('contact_id')} "
                f"pipeline_lead_id={data.get('pipeline_lead_id')} "
                f"agente={data.get('assigned_agent_id')}"
            )
            return True

        logger.error(f"Doss CRM: falha ao criar lead - {r.status_code} | {r.text[:300]}")
        return False

    except Exception as e:
        logger.error(f"Doss CRM escalate_to_human excecao: {e}")
        return False


# Alias para compatibilidade com openai_client.py (mesmo nome usado
# antes com o arcca_client.py, assim nao precisa mexer em quem chama)
arcca_client = escalate_to_human
