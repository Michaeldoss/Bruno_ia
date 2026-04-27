"""
Teste específico do CNPJ 18264058000108 pelo mesmo fluxo do Bruno.
Rode com: python testar_cnpj_fluxo.py
"""

import asyncio
import os
import sys
import json
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CNPJ_RAW = "18.264.058/0001-08"  # exatamente como o cliente mandou

async def main():
    from app.services.serasa_client import (
        consultar_cnpj, format_serasa_summary,
        is_cnpj_ativo, get_score, get_regime_serasa,
        tem_negativos, get_probabilidade_inadimplencia,
        calcular_tempo_empresa, get_capital_social,
        get_socios_com_restricao, get_consultas_mercado,
    )
    import re

    def _clean_cnpj(text: str) -> str:
        return re.sub(r'[^0-9]', '', text)

    print(f"\n{'='*55}")
    print(f"  CNPJ original: {CNPJ_RAW!r}")
    cnpj_clean = _clean_cnpj(CNPJ_RAW)
    print(f"  CNPJ limpo:    {cnpj_clean!r}")
    print(f"  Dígitos:       {len(cnpj_clean)}")
    print(f"{'='*55}\n")

    if len(cnpj_clean) != 14:
        print("❌ ERRO: CNPJ não tem 14 dígitos após limpeza!")
        return

    print("Consultando Serasa (igual ao Bruno faz)...")
    data = await consultar_cnpj(cnpj_clean)

    print(f"\nRetorno raw: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")

    print(f"\n{'='*55}")
    if "error" in data:
        print(f"❌ Erro: {data['error']}")
        print(f"   Mensagem: {data.get('message', '')}")
    else:
        print(f"✅ Dados recebidos!")
        print(f"   Ativo:   {is_cnpj_ativo(data)}")
        print(f"   Score:   {get_score(data)}")
        print(f"   Regime:  {get_regime_serasa(data)}")
        print(f"   Negat.:  {tem_negativos(data)}")
        print(f"\nResumo formatado:")
        print(format_serasa_summary(data))

    print(f"\n{'='*55}")
    print("  Salvo em: serasa_fluxo_resultado.json")
    with open("serasa_fluxo_resultado.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
