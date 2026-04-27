import requests, json
from datetime import date

SERASA_URL  = "https://netflexweb.com.br/serasaexperian/api/reports/"
HEADERS = {
    "cnpj":        "45024640000170",
    "key":         "8d97006023cbbc73980ad0b9c2b8ad0d",
    "hash":        "72f7043082613c85a7a23b686bd898bb",
    "api":         "producao",
    "distribuidor": "ABDATA",
    "Content-Type": "application/json"
}

r = requests.post(
    SERASA_URL,
    params={"clientWebhook": "RELATORIO_AVANCADO_TOP_SCORE_PJ"},
    headers=HEADERS,
    json={"typeDocument": "CNPJ", "documentNumber": "18264058000108", "federalUnit": "SC"},
    timeout=25
)

data = r.json()
report = data.get("reports", [{}])[0] if isinstance(data, dict) else {}

ident  = report.get("identificationReport", {})
qsa    = report.get("QSAReport", {})
score  = report.get("score", {})
neg    = report.get("negativeData", {})
facts  = report.get("facts", {})

# Tempo de empresa
tempo = ""
try:
    fundacao = date.fromisoformat(ident.get("companyFoundation",""))
    anos = (date.today() - fundacao).days // 365
    tempo = f"{anos} anos (desde {ident.get('companyFoundation','')})"
except: pass

# Capital social
cap = qsa.get("companyData",{}).get("socialCapitalValue", 0) or 0

# Socios com restricao
partners = qsa.get("partnerCompleteReport",{}).get("partnersList",[]) or []
socios_r = [p.get("name","") for p in partners if p.get("restrictionSign")]

# Consultas
consultas = facts.get("inquiryCompanyResponse",{}).get("quantity",{}).get("actual",0) or 0

def fmt(label, bloco):
    qtd = bloco.get("summary",{}).get("count",0) or 0
    val = bloco.get("summary",{}).get("balance",0) or 0
    return f"  {'⚠️ ' if qtd else '✅ '}{label}: {qtd} ocorrencia(s) | R$ {val:,.2f}" if qtd else f"  ✅ {label}: Nenhum"

card = f"""
═══════════════════════════════════════════════
📊 CONSULTA SERASA EXPERIAN
═══════════════════════════════════════════════
Empresa:       {ident.get('companyName','')}
Situacao:      {'✅ ' if 'ATIVA' in (ident.get('statusCodeDescription','') or '').upper() else '❌ '}{ident.get('statusCodeDescription','')}
Tempo empresa: {tempo}
Capital Social: R$ {cap:,.2f}

📈 SCORE E RISCO
Score Serasa:  {score.get('score','')}/1000
Inadimplencia: {score.get('message','')}

🔍 RESTRICOES
{fmt('PEFIN (Pendencias Financeiras)', neg.get('pefin',{}))}
{fmt('REFIN (Restricoes Financeiras)', neg.get('refin',{}))}
{fmt('Dividas Vencidas', neg.get('collectionRecords',{}))}
{fmt('Cheques sem Fundo', neg.get('check',{}))}
{fmt('Protestos', neg.get('notary',{}))}
{fmt('Acoes Judiciais', facts.get('judgementFilings',{}))}
{fmt('Falencias', facts.get('bankrupts',{}))}

👥 SOCIOS COM RESTRICAO
  {'⚠️  ' + ', '.join(socios_r) if socios_r else '✅ Nenhum'}

📋 CONSULTAS AO MERCADO
  {'⚠️  ' if consultas > 10 else '✅ '}{consultas} consulta(s) este mes
═══════════════════════════════════════════════"""

print(card)

with open("serasa_resultado.json","w",encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("JSON salvo em serasa_resultado.json")
