"""
Testa CNPJ com todos os estados até achar o correto.
Rode com: python testar_serasa_v3.py
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

ESTADOS = [
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA",
    "MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN",
    "RS","RO","RR","SC","SP","SE","TO"
]

CNPJ = "18264058000108"

print(f"\nTestando CNPJ {CNPJ} em todos os estados...\n")

for uf in ESTADOS:
    r = requests.post(
        SERASA_URL,
        params={"clientWebhook": "RELATORIO_AVANCADO_TOP_SCORE_PJ"},
        headers=HEADERS,
        json={"typeDocument": "CNPJ", "documentNumber": CNPJ, "federalUnit": uf},
        timeout=15
    )
    data = r.json()
    reports = data if isinstance(data, list) else data.get("reports", [])
    
    if reports and isinstance(reports[0], dict):
        code = str(reports[0].get("code", ""))
        msg  = reports[0].get("message", "")
        nome = reports[0].get("identificationReport", {}).get("companyName", "")
        
        if nome:
            print(f"✅ [{uf}] ENCONTRADO: {nome}")
            score = reports[0].get("score", {}).get("score", "")
            print(f"   Score: {score}/1000")
            break
        elif "NOT_FOUND" in msg or code == "404":
            print(f"   [{uf}] não encontrado")
        else:
            print(f"   [{uf}] resposta: {msg[:60]}")
    else:
        print(f"   [{uf}] retorno inesperado: {str(data)[:60]}")

print("\nTeste concluído.")
