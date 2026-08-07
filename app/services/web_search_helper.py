"""
Busca web para informações técnicas de máquinas concorrentes.
Só dispara quando detecta marca concorrente + pergunta técnica — controla custo.
"""
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MARCAS_CONCORRENTES = [
    "mimaki", "roland", "epson", "mutoh", "brother",
    "hp latex", "ricoh", "xuli", "flora", "infiniti", "allwin",
    # FIX: faltavam concorrentes diretos brasileiros de tinta/impressora
    # sublimatica e DTF -- o mesmo mercado da Doss. "Fabrijet" e o caso
    # documentado (cliente mencionou, Bruno nao reconheceu nem pesquisou)
    # -- concorrente real, sediado em Itajaí/SC, bem perto da Doss.
    "fabrijet", "sublimpel", "gênesis", "genesis", "sublitop",
]

TRIGGERS_TECNICOS = [
    "especificaç", "especific", "velocidade", "cabeç", "largura",
    "quanto imprime", "como funciona", "conta mais", "me fala",
    "detalhe", "caracteristica", "característica", "qual a diferença",
    # FIX: perguntas comuns de comparação nao batiam em nenhum gatilho
    # acima (ex: "voces tem tinta igual a Fabrijet?"), entao a busca
    # nunca disparava mesmo com a marca certa na lista.
    "melhor que", "pior que", "compara", "comparad", "vale a pena",
    "tem tinta igual", "tem algo igual", "é bom", "é boa", "recomend",
]


def precisa_buscar_concorrente(user_message: str, conversa_recente: str = "") -> str | None:
    texto = (user_message + " " + conversa_recente).lower()
    marca_encontrada = next((m for m in MARCAS_CONCORRENTES if m in texto), None)
    if not marca_encontrada:
        return None
    if any(t in texto for t in TRIGGERS_TECNICOS):
        return marca_encontrada
    return None


async def buscar_info_concorrente(client: AsyncAnthropic, marca: str, pergunta_cliente: str) -> str:
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
            system=(
                "Busque informação técnica pública e resumida sobre o equipamento mencionado. "
                "Responda em no máximo 3 linhas, direto, sem floreio. "
                "Foque em: tecnologia, largura de impressão, cabeçotes, velocidade aproximada se disponível. "
                "NUNCA invente dados. Se não achar, diga 'informação não encontrada publicamente'."
            ),
            messages=[
                {"role": "user", "content": f"Equipamento: {marca}. Pergunta do cliente: {pergunta_cliente}"}
            ],
        )
        texto_final = ""
        for block in response.content:
            if block.type == "text":
                texto_final += block.text
        return texto_final.strip() or "Informação não encontrada publicamente."
    except Exception as e:
        logger.error(f"[WEB_SEARCH] Erro ao buscar concorrente '{marca}': {e}")
        return ""
