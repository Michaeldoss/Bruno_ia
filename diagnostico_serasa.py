"""
Diagnóstico completo da integração Serasa
Rode com: python diagnostico_serasa.py
"""

import asyncio
import os
import json
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# CNPJs para testar
CNPJS_TESTE = [
    ("18264058000108", "CNPJ da conversa anterior"),
    ("45024640000170", "CNPJ da Doss Group"),
]

async def main():
    from app.services.serasa_client import (
        consultar_cnpj, format_serasa_summary,
        is_cnpj_ativo, get_score, tem_negativos,
        get_regime_serasa, get_probabilidade_inadimplencia,
    )

    for cnpj, descricao in CNPJS_TESTE:
        print(f"\n{'='*60}")
        print(f"  CNPJ: {cnpj}")
        print(f"  Desc: {descricao}")
        print(f"{'='*60}")

        try:
            print("  Consultando Serasa...")
            data = await consultar_cnpj(cnpj)

            print(f"\n  Status HTTP: retornou dados")
            print(f"  Tipo retorno: {type(data).__name__}")
            print(f"  Chaves: {list(data.keys()) if isinstance(data, dict) else 'lista'}")

            if "error" in data:
                print(f"\n  ❌ ERRO: {data['error']}")
                if "body" in data:
                    print(f"  Body: {data['body'][:300]}")
            else:
                print(f"\n  ativo:   {is_cnpj_ativo(data)}")
                print(f"  score:   {get_score(data)}")
                print(f"  regime:  {get_regime_serasa(data)}")
                print(f"  negativ: {tem_negativos(data)}")
                print(f"  prob:    {get_probabilidade_inadimplencia(data)}")

                summary = format_serasa_summary(data)
                print(f"\n  RESUMO:\n{summary}")

                # Salva JSON completo
                fname = f"serasa_{cnpj}.json"
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"\n  JSON salvo em: {fname}")

        except Exception as e:
            print(f"\n  ❌ EXCEÇÃO: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}\n  Diagnóstico concluído.\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
