import requests
import asyncio
import time
import logging
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SERASA_URL  = "https://netflexweb.com.br/serasaexperian/api/reports/"
SERASA_CNPJ = "45024640000170"
SERASA_KEY  = "8d97006023cbbc73980ad0b9c2b8ad0d"
SERASA_HASH = "72f7043082613c85a7a23b686bd898bb"
SERASA_DIST = "ABDATA"
SERASA_API  = getattr(settings, "SERASA_ENV", "producao")

HEADERS = {
    "cnpj":         SERASA_CNPJ,
    "key":          SERASA_KEY,
    "hash":         SERASA_HASH,
    "api":          SERASA_API,
    "distribuidor": SERASA_DIST,
    "Content-Type": "application/json"
}

OPTIONAL_FEATURES = ""

# ── DDD → Estado ───────────────────────────────────────────────────────────
DDD_ESTADO = {
    "11":"SP","12":"SP","13":"SP","14":"SP","15":"SP","16":"SP","17":"SP","18":"SP","19":"SP",
    "21":"RJ","22":"RJ","24":"RJ",
    "27":"ES","28":"ES",
    "31":"MG","32":"MG","33":"MG","34":"MG","35":"MG","37":"MG","38":"MG",
    "41":"PR","42":"PR","43":"PR","44":"PR","45":"PR","46":"PR",
    "47":"SC","48":"SC","49":"SC",
    "51":"RS","53":"RS","54":"RS","55":"RS",
    "61":"DF","62":"GO","64":"GO","65":"MT","66":"MT","67":"MS","68":"AC","69":"RO",
    "71":"BA","73":"BA","74":"BA","75":"BA","77":"BA",
    "79":"SE","81":"PE","82":"AL","83":"PB","84":"RN","85":"CE","86":"PI",
    "87":"PE","88":"CE","89":"PI","91":"PA","92":"AM","93":"PA","94":"PA",
    "95":"RR","96":"AP","97":"AM","98":"MA","99":"MA",
}

# ── Cidade → Estado ─────────────────────────────────────────────────────────
CIDADE_ESTADO = {
    "joinville":"SC","blumenau":"SC","florianopolis":"SC","brusque":"SC",
    "jaragua do sul":"SC","itajai":"SC","chapeco":"SC","criciuma":"SC",
    "balneario camboriu":"SC","sao bento do sul":"SC","lages":"SC",
    "guaramirim":"SC","schroeder":"SC","araquari":"SC","mafra":"SC",
    "porto alegre":"RS","caxias do sul":"RS","pelotas":"RS","novo hamburgo":"RS",
    "curitiba":"PR","londrina":"PR","maringa":"PR","cascavel":"PR",
    "sao paulo":"SP","campinas":"SP","ribeirao preto":"SP","santos":"SP",
    "rio de janeiro":"RJ","niteroi":"RJ","nova iguacu":"RJ",
    "belo horizonte":"MG","uberlandia":"MG","contagem":"MG",
    "salvador":"BA","feira de santana":"BA",
    "fortaleza":"CE","juazeiro do norte":"CE",
    "recife":"PE","olinda":"PE",
    "manaus":"AM","belem":"PA","goiania":"GO","brasilia":"DF",
    "cuiaba":"MT","campo grande":"MS","vitoria":"ES","maceio":"AL",
    "joao pessoa":"PB","natal":"RN","teresina":"PI","sao luis":"MA",
    "aracaju":"SE","porto velho":"RO","rio branco":"AC","boa vista":"RR",
}

# UFs para tentar — SP excluído pois retorna 404 nesta API
TODAS_UFS = [
    "SC","RS","PR","MG","RJ","BA","CE","GO","PE","AM",
    "PA","MT","MS","ES","AL","PB","RN","PI","MA","SE",
    "RO","AC","RR","AP","TO","DF","AC"
]


def _detectar_uf(phone: str = "", cidade: str = "") -> str:
    if cidade:
        uf = CIDADE_ESTADO.get(cidade.lower().strip())
        if uf:
            return uf
    if phone:
        ddd = "".join(c for c in phone if c.isdigit())
        if ddd.startswith("55") and len(ddd) > 11:
            ddd = ddd[2:]
        if len(ddd) >= 2:
            uf = DDD_ESTADO.get(ddd[:2])
            if uf:
                return uf
    return "SC"  # default: SC (maioria dos clientes Doss)


def _uma_consulta(cnpj_clean: str, uf: str) -> dict:
    """Faz UMA consulta à Serasa com a UF especificada."""
    try:
        params = {"clientWebhook": "RELATORIO_AVANCADO_TOP_SCORE_PJ"}
        r = requests.post(
            SERASA_URL,
            params=params,
            headers=HEADERS,
            json={"typeDocument": "CNPJ", "documentNumber": cnpj_clean, "federalUnit": uf},
            timeout=25
        )
        if r.status_code != 200:
            return {"error": f"http_{r.status_code}"}
        data = r.json()
        if isinstance(data, list):
            data = {"reports": data}
        return data
    except Exception as e:
        return {"error": str(e)}


def _e_not_found(data: dict) -> bool:
    """Retorna True se a resposta indica CNPJ não encontrado nesta UF."""
    if "error" in data:
        return True
    reports = data.get("reports", [])
    if not reports:
        return False
    first = reports[0] if isinstance(reports[0], dict) else {}
    code = str(first.get("code", ""))
    msg  = first.get("message", "")
    return code == "404" or "NOT_FOUND" in msg or "não preenchido" in msg.lower()


def _tem_dados(data: dict) -> bool:
    """Retorna True se a resposta tem dados reais de empresa."""
    if "error" in data:
        return False
    reports = data.get("reports", [])
    if not reports or not isinstance(reports[0], dict):
        return False
    first = reports[0]
    # Verifica se tem nome da empresa (sinal de dados reais)
    nome = first.get("identificationReport", {}).get("companyName", "")
    return bool(nome)


def _consultar_pj_sync(cnpj: str, phone: str = "", cidade: str = "") -> dict:
    cnpj_clean = "".join(c for c in cnpj if c.isdigit())
    if len(cnpj_clean) != 14:
        return {"error": "cnpj_invalido"}

    # UF detectada primeiro na lista
    uf_detectada = _detectar_uf(phone, cidade)
    ufs = [uf_detectada] + [u for u in TODAS_UFS if u != uf_detectada]

    for i, uf in enumerate(ufs):
        if i > 0:
            time.sleep(0.5)  # evita rate limit entre tentativas
        logger.info(f"Serasa: tentando CNPJ {cnpj_clean} com UF={uf}")
        data = _uma_consulta(cnpj_clean, uf)
        if _tem_dados(data):
            logger.info(f"Serasa: CNPJ {cnpj_clean} encontrado com UF={uf}")
            return data
        if not _e_not_found(data):
            # Erro diferente de "não encontrado" — retorna sem tentar mais
            logger.error(f"Serasa erro inesperado UF={uf}: {data}")
            return data

    logger.warning(f"Serasa: CNPJ {cnpj_clean} nao encontrado em nenhuma UF")
    return {"error": "cnpj_nao_encontrado",
            "message": "[ERROR][DOCUMENT_NOT_FOUND] Documento não encontrado na Serasa Experian."}


async def consultar_cnpj(cnpj: str, estado: str = "", phone: str = "", cidade: str = "") -> dict:
    return await asyncio.to_thread(_consultar_pj_sync, cnpj, phone, cidade)
def _get_report(data: dict) -> dict:
    try:
        reports = data.get("reports", [])
        if reports:
            return reports[0]
        return {}
    except Exception:
        return {}


def is_cnpj_ativo(data: dict) -> bool:
    try:
        r = _get_report(data)
        status = r.get("identificationReport", {}).get("statusCodeDescription", "")
        return "ATIVA" in status.upper()
    except Exception:
        return False


def get_score(data: dict) -> int:
    try:
        r = _get_report(data)
        return int(r.get("score", {}).get("score", 0) or 0)
    except Exception:
        return 0


def get_probabilidade_inadimplencia(data: dict) -> str:
    try:
        r = _get_report(data)
        msg = r.get("score", {}).get("message", "")
        rate = r.get("score", {}).get("defaultRate", "")
        if rate:
            try:
                pct = float(rate) / 100
                return f"{pct:.2f}%"
            except Exception:
                pass
        return msg
    except Exception:
        return ""


def tem_negativos(data: dict) -> bool:
    try:
        r = _get_report(data)
        neg   = r.get("negativeData", {})
        facts = r.get("facts", {})
        for bloco in [
            neg.get("pefin", {}), neg.get("refin", {}),
            neg.get("collectionRecords", {}), neg.get("check", {}),
            neg.get("notary", {}), facts.get("judgementFilings", {}),
            facts.get("bankrupts", {})
        ]:
            if bloco.get("summary", {}).get("count", 0):
                return True
        return False
    except Exception:
        return False


def get_socios_com_restricao(data: dict) -> list:
    try:
        r = _get_report(data)
        qsa = r.get("QSAReport", {})
        partners = qsa.get("partnerCompleteReport", {}).get("partnersList", []) or []
        directors = qsa.get("directorCompleteReport", {}).get("directorsList", []) or []
        todos = partners + directors
        return [p.get("name", "") for p in todos if p.get("restrictionSign")]
    except Exception:
        return []


def calcular_tempo_empresa(data: dict) -> str:
    try:
        from datetime import date
        r = _get_report(data)
        fundacao_str = r.get("identificationReport", {}).get("companyFoundation", "")
        if fundacao_str:
            fundacao = date.fromisoformat(fundacao_str)
            hoje = date.today()
            anos = (hoje - fundacao).days // 365
            return f"{anos} anos (desde {fundacao_str})"
        return ""
    except Exception:
        return ""


def get_capital_social(data: dict) -> str:
    try:
        r = _get_report(data)
        qsa = r.get("QSAReport", {})
        cap = qsa.get("companyData", {}).get("socialCapitalValue", 0) or 0
        if cap:
            return f"R$ {cap:,.2f}"
        return ""
    except Exception:
        return ""


def get_consultas_mercado(data: dict) -> int:
    try:
        r = _get_report(data)
        return int(r.get("facts", {}).get("inquiryCompanyResponse", {}).get("quantity", {}).get("actual", 0) or 0)
    except Exception:
        return 0


def get_regime_serasa(data: dict) -> str:
    """Infere regime pelo campo nature do QSA."""
    try:
        r = _get_report(data)
        nature = r.get("QSAReport", {}).get("companyData", {}).get("nature", "").upper()
        if "MEI" in nature or "MICRO EMPREENDEDOR" in nature:
            return "MEI"
        if "SIMPLES" in nature:
            return "SIMPLES"
        return "normal"
    except Exception:
        return "normal"


def format_serasa_summary(data: dict) -> str:
    """
    Formata resumo executivo completo para o card Arcca.
    Inclui todos os campos relevantes para decisao de venda.
    """
    if not data or "error" in data:
        return f"Erro na consulta Serasa: {data.get('error', 'desconhecido')}"
    # Verifica se retornou dados validos (Serasa bloqueia consulta do proprio CNPJ)
    r_test = _get_report(data)
    if not r_test or not r_test.get("identificationReport", {}).get("companyName", ""):
        return "Consulta Serasa: dados nao retornados. Verifique se o CNPJ e valido e diferente do CNPJ consultante."
    try:
        r = _get_report(data)
        if not r:
            return "Sem dados no retorno Serasa."

        ident    = r.get("identificationReport", {})
        nome     = ident.get("companyName", "")
        fantasia = ident.get("companyAlias", "")
        situacao = ident.get("statusCodeDescription", "")
        fundacao = ident.get("companyFoundation", "")

        score_data = r.get("score", {})
        score      = score_data.get("score", "")
        prob_inad  = get_probabilidade_inadimplencia(data)
        score_msg  = score_data.get("message", "")

        capital     = get_capital_social(data)
        tempo       = calcular_tempo_empresa(data)
        consultas   = get_consultas_mercado(data)
        socios_rest = get_socios_com_restricao(data)
        negativos   = tem_negativos(data)

        neg   = r.get("negativeData", {})
        facts = r.get("facts", {})

        def fmt_neg(label, bloco):
            qtd = bloco.get("summary", {}).get("count", 0) or 0
            val = bloco.get("summary", {}).get("balance", 0) or 0
            if qtd:
                return f"  ⚠️  {label}: {qtd} ocorrencia(s) | R$ {val:,.2f}"
            return f"  ✅ {label}: Nenhum"

        lines = ["═" * 45]
        lines.append("📊 CONSULTA SERASA EXPERIAN")
        lines.append("═" * 45)
        lines.append(f"Empresa:       {nome}")
        if fantasia and fantasia != nome:
            lines.append(f"Fantasia:      {fantasia}")
        lines.append(f"Situacao:      {'✅ ' if 'ATIVA' in situacao.upper() else '❌ '}{situacao}")
        if tempo:
            lines.append(f"Tempo empresa: {tempo}")
        if capital:
            lines.append(f"Capital Social:{capital}")

        lines.append("")
        lines.append("📈 SCORE E RISCO")
        if score is not None and score != "":
            lines.append(f"Score Serasa:  {score}/1000")
            lines.append(f"Inadimplencia: {prob_inad}")
        if score_msg:
            lines.append(f"Avaliacao:     {score_msg}")

        lines.append("")
        lines.append("🔍 HISTORICO DE RESTRICOES")
        lines.append(fmt_neg("PEFIN (Pendencias Financeiras)", neg.get("pefin", {})))
        lines.append(fmt_neg("REFIN (Restricoes Financeiras)", neg.get("refin", {})))
        lines.append(fmt_neg("Dividas Vencidas",               neg.get("collectionRecords", {})))
        lines.append(fmt_neg("Cheques sem Fundo",              neg.get("check", {})))
        lines.append(fmt_neg("Protestos",                      neg.get("notary", {})))
        lines.append(fmt_neg("Acoes Judiciais",                facts.get("judgementFilings", {})))
        lines.append(fmt_neg("Falencias/Concordatas",          facts.get("bankrupts", {})))

        lines.append("")
        lines.append("👥 SOCIOS E ADMINISTRADORES")
        if socios_rest:
            lines.append(f"  ⚠️  Socios/adm com restricao: {', '.join(socios_rest)}")
        else:
            lines.append("  ✅ Nenhum socio/adm com restricao")

        lines.append("")
        lines.append("📋 CONSULTAS AO MERCADO")
        if consultas > 10:
            lines.append(f"  ⚠️  {consultas} consultas este mes (alto)")
        elif consultas > 0:
            lines.append(f"  ✅ {consultas} consulta(s) este mes")
        else:
            lines.append("  ✅ Nenhuma consulta registrada este mes")

        # Features opcionais
        opt = r.get("optionalFeatures", {})
        if opt:
            lines.append("")
            lines.append("💰 LIMITE DE CREDITO (SERASA)")
            limite = opt.get("LIMITE_CREDITO", {})
            if isinstance(limite, dict):
                scores_l = limite.get("scores", [])
                if scores_l:
                    for s in scores_l[:1]:
                        resp = s.get("scoreResponse", {})
                        lines.append(f"  Score limite: {resp.get('score', '')}")
                        lines.append(f"  Mensagem: {resp.get('message', '')}")

        lines.append("═" * 45)
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Erro ao formatar Serasa: {e}")
        return f"Erro ao formatar retorno Serasa: {e}"
