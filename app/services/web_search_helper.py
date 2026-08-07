"""
Busca web para informações técnicas de máquinas concorrentes.
Só dispara quando detecta marca concorrente + pergunta técnica — controla custo.
"""
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MARCAS_CONCORRENTES = [
    # Fabricantes multinacionais
    "mimaki", "roland", "epson", "mutoh", "brother",
    "hp latex", "ricoh", "xuli", "flora", "infiniti", "allwin",
    "marabu", "sun chemical", "dupont artistri", "artistri",

    # FIX (07/08): lista expandida a partir de mapeamento real de mercado
    # -- 60 concorrentes/similares levantados (Manus AI, mar/2026).
    # Antes so tinha as multinacionais grandes -- faltava praticamente
    # todo o mercado nacional, que e onde o cliente da Doss realmente
    # compara preco no dia a dia. "Fabrijet" foi o caso documentado que
    # expos essa lacuna.
    #
    # IMPORTANTE: "Sun Special" NAO entra aqui -- e importador de
    # maquina DA PROPRIA Doss, nao concorrente (confirmado por Michael
    # 07/08). Se aparecer, e fornecedor, nao dispara pesquisa de
    # concorrente.

    # Fabricantes nacionais de tinta
    "gênesis", "genesis", "fremplast", "acn química", "acn quimica",
    "saturno", "stellar auroraink", "fabrijet",

    # Distribuidores/importadores de tinta
    "bluecolor", "bm do brasil", "bordeaux", "tegape",
    "nova silk", "jetbest", "inktec", "inknet", "fattu", "at inks",
    "sublimaink", "sublivix", "nasus ink", "brprints", "pampa tech",
    "fenix importação", "fenix suprimentos", "avante printer",
    "supriloja", "magna tech",

    # Equipamentos
    "headsign", "arkom", "digifoil", "br group", "firejet", "teknova",
    "inprint digital", "km brasil", "nocai", "ess do brasil",
    "electronic sign supply", "sc mídia", "sc midia", "dsi sistemas",
    "suplinet", "eprinters", "rocketjet", "docan", "sp plotter",
    "xinflying", "brasil dtf", "puracor",

    # Distribuidores completos e regionais
    "grupo bloom", "nexum", "sanju papéis", "sanju papeis",
    "brasko", "mecolour", "comprint", "gemir", "cogra distribuidora",
    "comercial paulista", "paulista sign", "sign brasil",
    "ponto da sublimação", "ponto da sublimacao", "sublima brasil",
    "sulblimaq", "mundial imports", "unica brasil transfer", "tele silk",
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
