"""
Teste Serasa com detecção automática de estado por DDD/cidade
Rode com: python testar_serasa_v4.py
"""
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

import asyncio
from app.services.serasa_client import consultar_cnpj, format_serasa_summary

TESTES = [
    ("18264058000108", "+5547992307367", "Brusque",     "SO REVENDO (esposa - SC)"),
    ("45024640000170", "+5547991933197", "Joinville",   "Doss Group (SC)"),
]

async def main():
    for cnpj, phone, cidade, descricao in TESTES:
        print(f"\n{'='*55}")
        print(f"  {descricao}")
        print(f"  CNPJ: {cnpj} | Phone: {phone} | Cidade: {cidade}")
        print(f"{'='*55}")

        data = await consultar_cnpj(cnpj, phone=phone, cidade=cidade)

        if "error" in data:
            print(f"  ❌ {data['error']}: {data.get('message','')[:80]}")
        else:
            print(format_serasa_summary(data))

    print(f"\n{'='*55}\n")

asyncio.run(main())
