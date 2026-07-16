"""
Pesquisa de Satisfação — envio automático quando OS finaliza
──────────────────────────────────────────────────────────
  1. A cada 10 min, verifica no Uniplus se há OS com status Finalizada
     dentro da janela recente.
  2. Pra cada OS ainda não notificada (controle no Supabase), busca o
     telefone do cliente e manda uma mensagem via Twilio perguntando
     a nota de 0 a 5.
  3. A resposta é capturada em app/api/webhooks.py (função
     verificar_resposta_satisfacao, chamada antes do fluxo normal do
     Bruno) e gravada de volta nessa mesma tabela do Supabase.
  4. O resultado alimenta o dashboard do CRM (view vw_satisfacao_dashboard
     no Supabase) — não fica no banco local do Bruno.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import get_settings
from app.services.twilio_client import twilio_service
from app.services.uniplus_client import list_os_finalizadas, get_customer_by_cnpj

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URL = settings.SUPABASE_URL.rstrip("/")
SUPABASE_KEY = settings.SUPABASE_SERVICE_ROLE_KEY

INTERVALO_LOOP_SEGUNDOS = 600  # 10 min
HORAS_JANELA_BUSCA = 6         # margem de segurança sobre o intervalo do loop
DIAS_EXPIRACAO_PESQUISA = 3

# Template aprovado pela Meta (Twilio Content API) — obrigatório porque
# a pesquisa é sempre business-iniciada fora da janela de 24h de
# atendimento (erro 63016: "Outside messaging window").
TEMPLATE_CONTENT_SID = "HX221083c9e20ca582281f6375fd6c0cc5"

TABELA = "os_notificacoes_whatsapp"


def _headers_supabase(patch: bool = False) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if not patch:
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return h


def _montar_mensagem(numero_os: str) -> str:
    """
    NÃO USADA MAIS PARA ENVIO — texto livre é rejeitado pela Meta fora da
    janela de 24h (erro 63016). Mantida só como referência do texto que
    também está cadastrado no Template aprovado (TEMPLATE_CONTENT_SID).
    Se precisar mudar o texto, mude nos DOIS lugares: aqui (documentação)
    e no Twilio Content Template Builder (o que realmente é enviado).
    """
    return (
        f"Olá! A sua Ordem de Serviço nº {numero_os} foi finalizada.\n\n"
        "De 0 a 5, sendo 0 ruim e 5 excelente, como você avalia nosso atendimento?\n\n"
        "Responda só com o número (0, 1, 2, 3, 4 ou 5). — Doss Group"
    )


def _formatar_telefone_e164(telefone_raw: str) -> Optional[str]:
    """
    Uniplus devolve telefone sujo, tipo '(0xx47)99110-5217' — o '0xx' é
    um prefixo de discagem antigo (não faz parte do número). Removê-lo
    ANTES de extrair dígitos, senão sobra um '0' a mais na frente do DDD.
    Twilio precisa do formato E.164 (+55...).
    """
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


async def _ja_notificada(client: httpx.AsyncClient, numero_os: str) -> bool:
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{TABELA}",
            headers=_headers_supabase(),
            params={"numero_os": f"eq.{numero_os}", "select": "numero_os", "limit": "1"},
        )
        if r.status_code >= 400:
            logger.warning(f"[SATISFACAO] check dedup falhou pra OS {numero_os}: {r.status_code}")
            return True  # em dúvida, não manda de novo (evita duplicar mensagem)
        return len(r.json()) > 0
    except Exception as e:
        logger.error(f"[SATISFACAO] erro checando dedup OS {numero_os}: {e}")
        return True


async def _gravar_resultado(client: httpx.AsyncClient, registro: dict):
    try:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/{TABELA}",
            headers=_headers_supabase(),
            params={"on_conflict": "numero_os"},
            json=registro,
        )
        if r.status_code >= 400:
            logger.error(f"[SATISFACAO] falha ao gravar {registro.get('numero_os')}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[SATISFACAO] erro gravando resultado: {e}")


async def _processar_uma_os(client: httpx.AsyncClient, os_item: dict):
    numero_os = str(os_item.get("codigo") or os_item.get("id") or "")
    if not numero_os:
        return

    if await _ja_notificada(client, numero_os):
        return

    nome_cliente = os_item.get("nomeCliente") or ""
    nome_atendente = os_item.get("nomeAtendente") or ""
    codigo_atendente = os_item.get("codigoAtendente") or ""
    cnpj_cpf = os_item.get("cnpjCpfCliente") or ""
    id_cliente = os_item.get("idCliente") or ""

    registro = {
        "numero_os": numero_os,
        "id_cliente": str(id_cliente or ""),
        "nome_cliente": nome_cliente,
        "nome_atendente": nome_atendente.strip() if nome_atendente else None,
        "codigo_atendente": str(codigo_atendente or "") or None,
        "cnpj_cpf_cliente": cnpj_cpf,
        "telefone": None,
        "status_envio": "falha",
        "status_pesquisa": "falha_envio",
        "erro": None,
    }

    try:
        # PRIORIDADE 1: telefone WhatsApp direto da própria OS (campo "extra3",
        # ativado no Uniplus em 16/07/2026 — mais confiável que o cadastro do
        # cliente, que pode estar desatualizado). Só existe em OS criadas
        # depois dessa data.
        telefone_raw = os_item.get("extra3") or None

        if not telefone_raw:
            # PRIORIDADE 2 (fallback p/ OS antigas sem extra3): busca no
            # cadastro do cliente por CNPJ/CPF. Menos confiável — cadastro
            # pode estar desatualizado ou o cliente pode não ter telefone lá.
            contato = await get_customer_by_cnpj(cnpj_cpf)
            telefone_raw = contato.get("telefone") or contato.get("celular") if contato else None

        telefone = _formatar_telefone_e164(telefone_raw)

        if not telefone:
            registro["status_envio"] = "sem_telefone"
            registro["status_pesquisa"] = "sem_telefone"
            await _gravar_resultado(client, registro)
            logger.info(f"[SATISFACAO] OS {numero_os}: sem telefone encontrado, pulando.")
            return

        registro["telefone"] = telefone.lstrip("+")  # guarda sem o + pra bater com o normalizePhone do CRM

        await twilio_service.send_whatsapp_template_message(
            telefone,
            TEMPLATE_CONTENT_SID,
            {"1": numero_os},
        )

        registro["status_envio"] = "enviado"
        registro["status_pesquisa"] = "aguardando_resposta"
        registro["expira_em"] = (
            datetime.now(timezone.utc) + timedelta(days=DIAS_EXPIRACAO_PESQUISA)
        ).isoformat()

        logger.info(f"[SATISFACAO] OS {numero_os}: pesquisa enviada para {telefone}.")

    except Exception as e:
        registro["status_envio"] = "falha"
        registro["status_pesquisa"] = "falha_envio"
        registro["erro"] = str(e)[:500]
        logger.error(f"[SATISFACAO] OS {numero_os}: erro no envio: {e}")

    await _gravar_resultado(client, registro)


async def _tick(horas_janela: int = HORAS_JANELA_BUSCA):
    try:
        finalizadas = await list_os_finalizadas(horas_janela)
        if not finalizadas:
            logger.info(f"[SATISFACAO] Nenhuma OS finalizada na janela de {horas_janela}h.")
            return
        logger.info(f"[SATISFACAO] {len(finalizadas)} OS finalizada(s) na janela de {horas_janela}h — verificando pendências.")
        async with httpx.AsyncClient(timeout=15) as client:
            for os_item in finalizadas:
                await _processar_uma_os(client, os_item)
    except Exception as e:
        logger.error(f"[SATISFACAO] erro no tick: {e}")


async def _loop_satisfacao():
    logger.info("[SATISFACAO] Serviço iniciado.")
    while True:
        await _tick()
        await asyncio.sleep(INTERVALO_LOOP_SEGUNDOS)


_satisfacao_task: asyncio.Task = None


def start_satisfacao_service():
    global _satisfacao_task
    _satisfacao_task = asyncio.create_task(_loop_satisfacao())
    logger.info("[SATISFACAO] Task iniciada com sucesso.")


# ---------------------------------------------------------------------------
# Captura da resposta — chamado pelo webhook do Twilio ANTES de encaminhar
# a mensagem pro fluxo normal do Bruno (IA / conversa).
#
# Retorna True se a mensagem era uma nota de pesquisa válida e já foi
# tratada (o webhook deve parar ali, não repassar pro Bruno/IA).
# Retorna False se não era resposta de pesquisa — segue fluxo normal.
# ---------------------------------------------------------------------------
async def verificar_resposta_satisfacao(phone: str, body: str) -> bool:
    texto = (body or "").strip()
    if not texto or not __import__("re").fullmatch(r"[0-5]", texto):
        return False

    telefone_limpo = "".join(c for c in phone if c.isdigit())
    agora = datetime.now(timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/{TABELA}",
                headers=_headers_supabase(),
                params={
                    "telefone": f"eq.{telefone_limpo}",
                    "status_pesquisa": "eq.aguardando_resposta",
                    "expira_em": f"gte.{agora}",
                    "order": "enviado_em.desc",
                    "limit": "1",
                },
            )
            if r.status_code >= 400:
                logger.warning(f"[SATISFACAO] busca pendente falhou pra {telefone_limpo}: {r.status_code}")
                return False

            pendentes = r.json()
            if not pendentes:
                return False

            pesquisa = pendentes[0]
            nota = int(texto)

            patch = await client.patch(
                f"{SUPABASE_URL}/rest/v1/{TABELA}",
                headers=_headers_supabase(patch=True),
                params={"numero_os": f"eq.{pesquisa['numero_os']}"},
                json={
                    "nota_satisfacao": nota,
                    "status_pesquisa": "respondido",
                    "respondido_em": agora,
                },
            )
            if patch.status_code >= 400:
                logger.error(f"[SATISFACAO] falha ao gravar resposta OS {pesquisa['numero_os']}: {patch.status_code}")
                return False

            logger.info(f"[SATISFACAO] {telefone_limpo} respondeu nota {nota} pra OS {pesquisa['numero_os']}.")
            return True

    except Exception as e:
        logger.error(f"[SATISFACAO] erro verificando resposta de {phone}: {e}")
        return False
