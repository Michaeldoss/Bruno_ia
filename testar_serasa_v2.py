"""
Teste direto Serasa — sem federalUnit
Rode com: python testar_serasa_v2.py
"""
import requests
import json

SERASA_URL = "https://netflexweb.com.br/serasaexperian/api/reports/"
HEADERS = {
    "cnpj":         "45024640000170",
    "key":          "8d97006023cbbc73980ad0b9c2b8ad0d",
    "hash":         "72f7043082613c85a7a23b686bd898bb",
    "api":          "producao",
    "distribuidor": "ABDATA",
    "Content-Type": "application/json"
}

CNPJS = [
    ("18264058000108", "SO REVENDO (esposa)"),
    ("45024640000170", "Doss Group"),
]

for cnpj, descricao in CNPJS:
    print(f"\n{'='*55}")
    print(f"  CNPJ: {cnpj} — {descricao}")
    print(f"  Enviando SEM federalUnit...")
    print(f"{'='*55}")

    # SEM federalUnit no payload
    r = requests.post(
        SERASA_URL,
        params={"clientWebhook": "RELATORIO_AVANCADO_TOP_SCORE_PJ"},
        headers=HEADERS,
        json={"typeDocument": "CNPJ", "documentNumber": cnpj},
        timeout=25
    )

    print(f"  HTTP: {r.status_code}")
    data = r.json()
    reports = data.get("reports", []) if isinstance(data, dict) else []

    if reports:
        first = reports[0]
        code = str(first.get("code", ""))
        if code == "404" or "NOT_FOUND" in first.get("message", ""):
            print(f"  ❌ 404 — Ainda não encontrado")
        else:
            nome = first.get("identificationReport", {}).get("companyName", "")
            situacao = first.get("identificationReport", {}).get("statusCodeDescription", "")
            score = first.get("score", {}).get("score", "")
            print(f"  ✅ ENCONTRADO!")
            print(f"  Empresa:  {nome}")
            print(f"  Situação: {situacao}")
            print(f"  Score:    {score}/1000")
    else:
        print(f"  Retorno: {json.dumps(data, ensure_ascii=False)[:200]}")

print(f"\n{'='*55}\n")
