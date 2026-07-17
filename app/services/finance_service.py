"""
Régua de Cobrança — WhatsApp automático por título a receber
──────────────────────────────────────────────────────────
Estágios (em relação à data de vencimento):
  -5 dias, -3 dias, -1 dia, no dia (0), +2 dias (margem de compensação
  do boleto — se venceu dia 05, só cobra dia 07, não dia 06, porque o
  pagamento pode ter sido feito no vencimento e ainda não ter
  compensado).

IMPORTANTE — pendência conhecida:
  A busca de títulos no Uniplus (get_titulos_a_receber, em
  uniplus_client.py) está com endpoint PLACEHOLDER — /public-api/v1/
  titulos-receber testado e confirmado que NÃO EXISTE (mesmo problema
  que /contatos deu na pesquisa de satisfação). Testei mais de 20
  variações de nome sem achar a certa. Precisa confirmar com o
  suporte do Uniplus qual é o endpoint público de títulos a receber
  antes de ativar isso de verdade.

Mesmo padrão da pesquisa de satisfação (satisfacao_service.py):
  - Envio via Template aprovado da Meta (texto livre é rejeitado fora
    da janela de 24h — confirmado hoje com a pesquisa de satisfação)
  - Telefone tratado pro prefixo '0xx' do Uniplus
  - Dedup via Supabase (tabela cobrancas_whatsapp), chave única
    (titulo_id, estagio) — nunca manda o mesmo aviso duas vezes pro
    mesmo título no mesmo estágio
"""

import asyncio
import logging
from datetime import date, timedelta

import httpx

from app.config import get_settings
from app.services.twilio_client import twilio_service
from app.services.uniplus_client import get_titulos_a_receber, get_customer_by_cnpj

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY

INTERVALO_LOOP_SEGUNDOS = 3600  # roda 1x por hora — cobrança não precisa de 10 em 10 min
TABELA = "cobrancas_whatsapp"

# MODO DE TESTE: enquanto True, só processa títulos de clientes cujo
# nome contém um dos termos abaixo. Deixar True até validar de ponta
# a ponta com um caso real controlado.
TEST_MODE = True
TEST_MODE_ONLY_CUSTOMERS = ["SO REVENDO", "MICHAEL"]

# Templates aprovados no Twilio (Content API) — PREENCHER depois de
# criar e aprovar cada um no Twilio Console, categoria Utility.
# Cada estágio precisa do PRÓPRIO template — a Meta não permite um
# template genérico que troca o corpo inteiro via variável.
TEMPLATE_CONTENT_SIDS = {
    "5_antes": "PREENCHER_HX_AQUI",
    "3_antes": "PREENCHER_HX_AQUI",
    "1_antes": "PREENCHER_HX_AQUI",
    "no_dia": "PREENCHER_HX_AQUI",
    "2_depois": "PREENCHER_HX_AQUI",
}

# Texto de referência de cada estágio — precisa ser IDÊNTICO ao texto
# cadastrado no Template do Twilio (a Meta só entrega o que está
# aprovado, não o que está aqui). {{1}} = nome do cliente.
TEXTOS_REFERENCIA = {
    "5_antes": "Olá {{1}}! Tudo bem? Passando para lembrar que sua fatura Doss Group vence em 5 dias. Qualquer dúvida sobre o boleto, estou à disposição.",
    "3_antes": "Oi {{1}}! Sua fatura Doss Group vence em 3 dias. Precisa da segunda via do boleto?",
    "1_antes": "Bom dia {{1}}! Amanhã vence sua fatura Doss Group. Já se programou para o pagamento?",
    "no_dia": "Olá {{1}}! Sua fatura Doss Group vence hoje. Caso precise da segunda via, me avise aqui!",
    "2_depois": "Oi {{1}}! Notamos que sua fatura Doss Group ainda não consta como paga. Houve algum problema? Qualquer dúvida, estamos à disposição.",
}

ESTAGIOS_DIAS = {
    "5_antes": -5,
    "3_antes": -3,
    "1_antes": -1,
    "no_dia": 0,
    "2_depois": 2,
}


def _headers_supabase(patch: bool = False) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if not patch:
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return h


def _formatar_telefone_e164(telefone_raw):
    """Mesma lógica da pesquisa de satisfação — trata o prefixo '0xx'."""
    if not telefone_raw:
        return None
    import re
    texto = re.sub(r"0xx", "", str(telefone_raw), flags=re.IGNORECASE)
    digitos = "".join(c for c in texto if c.isdigit())
    if not digitos:
        return None
    if len(digitos) <= 11:
        digitos = "55" + digitos
    return f"+{digitos}"


async def _ja_enviado(client: httpx.AsyncClient, titulo_id: str, estagio: str) -> bool:
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{TABELA}",
            headers=_headers_supabase(),
            params={"titulo_id": f"eq.{titulo_id}", "estagio": f"eq.{estagio}", "select": "id", "limit": "1"},
        )
        if r.status_code >= 400:
            logger.warning(f"[COBRANCA] check dedup falhou pra titulo {titulo_id}/{estagio}: {r.status_code}")
            return True  # em dúvida, não manda de novo
        return len(r.json()) > 0
    except Exception as e:
        logger.error(f"[COBRANCA] erro checando dedup {titulo_id}/{estagio}: {e}")
        return True


async def _gravar_resultado(client: httpx.AsyncClient, registro: dict):
    try:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/{TABELA}",
            headers=_headers_supabase(),
            params={"on_conflict": "titulo_id,estagio"},
            json=registro,
        )
        if r.status_code >= 400:
            logger.error(f"[COBRANCA] falha ao gravar {registro.get('titulo_id')}/{registro.get('estagio')}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[COBRANCA] erro gravando resultado: {e}")


async def _processar_titulo(client: httpx.AsyncClient, titulo: dict, estagio: str):
    titulo_id = str(titulo.get("id") or titulo.get("codigo") or "")
    if not titulo_id:
        return

    if await _ja_enviado(client, titulo_id, estagio):
        return

    nome_cliente = (titulo.get("nomeCliente") or (titulo.get("contato") or {}).get("nome") or "Cliente").strip()
    cnpj_cpf = titulo.get("cnpjCpfCliente") or ""

    # Trava de segurança: modo teste só processa clientes da lista
    if TEST_MODE and not any(alvo in nome_cliente.upper() for alvo in TEST_MODE_ONLY_CUSTOMERS):
        return

    registro = {
        "titulo_id": titulo_id,
        "nome_cliente": nome_cliente,
        "cnpj_cpf_cliente": cnpj_cpf,
        "telefone": None,
        "valor": titulo.get("valor"),
        "data_vencimento": titulo.get("dataVencimento"),
        "estagio": estagio,
        "status_envio": "falha",
        "erro": None,
    }

    content_sid = TEMPLATE_CONTENT_SIDS.get(estagio, "")
    if not content_sid or "PREENCHER" in content_sid:
        registro["status_envio"] = "falha"
        registro["erro"] = f"Template do estágio '{estagio}' ainda não configurado (TEMPLATE_CONTENT_SIDS)"
        await _gravar_resultado(client, registro)
        logger.warning(f"[COBRANCA] titulo {titulo_id}: template de '{estagio}' não configurado, pulando envio.")
        return

    try:
        telefone_raw = titulo.get("telefone") or (titulo.get("contato") or {}).get("celular")
        if not telefone_raw and cnpj_cpf:
            contato = await get_customer_by_cnpj(cnpj_cpf)
            telefone_raw = contato.get("telefone") or contato.get("celular") if contato else None

        telefone = _formatar_telefone_e164(telefone_raw)
        if not telefone:
            registro["status_envio"] = "sem_telefone"
            await _gravar_resultado(client, registro)
            logger.info(f"[COBRANCA] titulo {titulo_id}: sem telefone, pulando.")
            return

        registro["telefone"] = telefone.lstrip("+")

        await twilio_service.send_whatsapp_template_message(
            telefone,
            content_sid,
            {"1": nome_cliente},
        )
        registro["status_envio"] = "enviado"
        logger.info(f"[COBRANCA] titulo {titulo_id} ({estagio}): aviso enviado para {telefone}.")

    except Exception as e:
        registro["status_envio"] = "falha"
        registro["erro"] = str(e)[:500]
        logger.error(f"[COBRANCA] titulo {titulo_id} ({estagio}): erro no envio: {e}")

    await _gravar_resultado(client, registro)


async def _tick():
    try:
        hoje = date.today()
        async with httpx.AsyncClient(timeout=15) as client:
            for estagio, offset_dias in ESTAGIOS_DIAS.items():
                data_alvo = hoje + timedelta(days=offset_dias)
                titulos = await get_titulos_a_receber(data_vencimento=data_alvo)
                if not titulos:
                    continue
                logger.info(f"[COBRANCA] {len(titulos)} título(s) no estágio '{estagio}' (vencimento {data_alvo}).")
                for titulo in titulos:
                    await _processar_titulo(client, titulo, estagio)
    except Exception as e:
        logger.error(f"[COBRANCA] erro no tick: {e}")


async def _loop_cobranca():
    logger.info("[COBRANCA] Serviço iniciado.")
    while True:
        await _tick()
        await asyncio.sleep(INTERVALO_LOOP_SEGUNDOS)


_cobranca_task: asyncio.Task = None


def start_cobranca_service():
    global _cobranca_task
    _cobranca_task = asyncio.create_task(_loop_cobranca())
    logger.info("[COBRANCA] Task iniciada com sucesso.")


class FinanceService:
    """Mantido pra compatibilidade com o import existente em webhooks.py."""
    async def run_daily_collection(self):
        await _tick()


finance_service = FinanceService()
