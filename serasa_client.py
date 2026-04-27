import requests
import asyncio
import logging
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SERASA_URL  = "https://netflexweb.com.br/serasaexperian/api/reports/"
SERASA_CNPJ = "45024640000170"
SERASA_KEY  = "8d97006023cbbc73980ad0b9c2b8ad0d"
SERASA_HASH = "72f7043082613c85a7a23b686bd898bb"
SERASA_DIST = "ABDATA"
SERASA_API  = getattr(settings, "SERASA_ENV", "homologacao")

HEADERS = {
    "cnpj":         SERASA_CNPJ,
    "key":          SERASA_KEY,
    "hash":         SERASA_HASH,
    "api":          SERASA_API,
    "distribuidor": SERASA_DIST,
    "Content-Type": "application/json"
}

OPTIONAL_FEATURES = ""  # Features opcionais removidas (causavam NR19)


def _consultar_pj_sync(cnpj: str, estado: str = "") -> dict:
    cnpj_clean = "".join(c for c in cnpj if c.isdigit())
    if len(cnpj_clean) != 14:
        return {"error": "cnpj_invalido"}
    try:
        params = {"clientWebhook": "RELATORIO_AVANCADO_TOP_SCORE_PJ"}
        if OPTIONAL_FEATURES:
            params["optionalFeatures"] = OPTIONAL_FEATURES

        # Monta payload — só envia federalUnit se explicitamente passado
        payload = {"typeDocument": "CNPJ", "documentNumber": cnpj_clean}
        if estado:
            payload["federalUnit"] = estado.upper()

        r = requests.post(
            SERASA_URL,
            params=params,
            headers=HEADERS,
            json=payload,
            timeout=25
        )
        logger.info(f"Serasa PJ status: {r.status_code} | CNPJ: {cnpj_clean} | Estado: {estado or 'nao enviado'}")
        if r.status_code == 200:
            data = r.json()
            # Normaliza para sempre retornar dict com "reports"
            if isinstance(data, list):
                data = {"reports": data}

            # ── Verifica se reports contém erro interno da Serasa ─────────
            reports = data.get("reports", [])
            if reports and isinstance(reports[0], dict):
                first = reports[0]
                code  = str(first.get("code", ""))
                msg   = first.get("message", "")
                # Erro 404 = CNPJ não encontrado na base Serasa
                if code == "404" or "DOCUMENT_NOT_FOUND" in msg:
                    logger.warning(f"Serasa: CNPJ {cnpj_clean} nao encontrado na base.")
                    return {"error": "cnpj_nao_encontrado", "message": msg}
                # Outros erros de negócio
                if code and code not in ("200", "201", "") and "error" in msg.lower():
                    logger.warning(f"Serasa erro negocio: code={code} | {msg[:100]}")
                    return {"error": f"serasa_code_{code}", "message": msg}

            return data
        return {"error": f"status_{r.status_code}", "body": r.text[:300]}
    except Exception as e:
        logger.error(f"Serasa PJ excecao: {e}")
        return {"error": str(e)}


async def consultar_cnpj(cnpj: str, estado: str = "") -> dict:
    return await asyncio.to_thread(_consultar_pj_sync, cnpj, estado)


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
    if not data:
        return ""

    # ── Trata erros conhecidos ────────────────────────────────────────────
    if "error" in data:
        err = data["error"]
        if err == "cnpj_invalido":
            return "CNPJ invalido (menos de 14 digitos)."
        if err == "cnpj_nao_encontrado":
            return "CNPJ nao encontrado na base Serasa Experian. Pode ser MEI recente, CNPJ baixado ou nao cadastrado."
        if err.startswith("status_"):
            return f"Serasa indisponivel (HTTP {err.replace('status_','')}). Tente novamente mais tarde."
        return f"Consulta Serasa indisponivel: {data.get('message', err)}"
    try:
        r = _get_report(data)
        if not r:
            return ""

        # Verifica se tem dados reais (pode vir report vazio)
        nome = r.get("identificationReport", {}).get("companyName", "")
        if not nome:
            return ""

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
