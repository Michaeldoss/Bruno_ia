import asyncio
import sys
import os
import requests
sys.path.append('.')
from app.services.uniplus_client import uniplus_service

async def raw_test():
    headers = uniplus_service._get_headers()
    url = f"{uniplus_service.base_url}/v2/financeiro/contas-receber"
    print(f"Buscando faturas no Uniplus em: {url}")
    try:
        response = requests.get(url, headers=headers, params={'limit': 100}, timeout=20)
        if response.status_code == 200:
            content = response.json().get('content', [])
            print(f"Total de faturas encontradas (Top 100): {len(content)}")
            
            so_revendo_found = False
            for r in content:
                nome = r.get('contato', {}).get('nome', '---')
                sit = r.get('situacao', '---')
                venc = r.get('dataVencimento', '---')
                
                if "SO REVENDO" in nome.upper():
                    print(f"!!! ENCONTRADA: {nome} | Situacao: {sit} | Vencimento: {venc}")
                    so_revendo_found = True
                
            if not so_revendo_found:
                print("Cliente 'SO REVENDO' nao encontrado nas últimas 100 faturas.")
        else:
            print(f"Erro na API ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Falha na conexão: {e}")

if __name__ == "__main__":
    asyncio.run(raw_test())
