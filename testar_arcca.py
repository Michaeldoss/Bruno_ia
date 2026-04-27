"""
Teste de preenchimento completo do Arcca CRM
Simula uma conversa real e cria o card com todos os campos.
Rode com: python testar_arcca.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# ── Dados simulados da conversa ───────────────────────────────────────────

PHONE        = "+5547992307367"
NOME         = "Ricardo Alves"
CIDADE       = "Blumenau"
EMAIL        = "ricardo@grafica360.com.br"
TELEFONE     = "(47) 99812-3456"
CNPJ         = "12.345.678/0001-99"
REGIME       = "Simples Nacional"

PRODUTO      = "Plotter DG 1802i"
TECNOLOGIA   = "Eco Solvente"
VALOR_EST    = 68900
PERFIL       = "Upgrade / Expansao"
ORIGEM       = "Trafego Organico- Instagram"

PARQUE       = "Roland BN-20, Epson L1800"
TINTA_ATUAL  = "BM do Brasil"
CUSTO_TINTA  = "R$ 1.200/mes"

SERASA_NOTA  = """═══════════════════════════════════════════
📊 CONSULTA SERASA EXPERIAN
═══════════════════════════════════════════
Empresa:       GRAFICA 360 COMUNICACAO VISUAL LTDA
Situacao:      ✅ ATIVA
Tempo empresa: 8 anos (desde 2016-03-14)
Capital Social: R$ 30.000,00

📈 SCORE E RISCO
Score Serasa:  720/1000
Inadimplencia: 3.20%
Avaliacao:     Baixo risco de inadimplencia

🔍 HISTORICO DE RESTRICOES
  ✅ PEFIN: Nenhum
  ✅ REFIN: Nenhum
  ✅ Dividas Vencidas: Nenhum
  ✅ Cheques sem Fundo: Nenhum
  ✅ Protestos: Nenhum
  ✅ Acoes Judiciais: Nenhum
  ✅ Falencias: Nenhum

👥 SOCIOS E ADMINISTRADORES
  ✅ Nenhum socio com restricao

📋 CONSULTAS AO MERCADO
  ✅ 2 consultas este mes
═══════════════════════════════════════════"""

HISTORICO_CONVERSA = """Cliente: Oi, vi vocês no Instagram e quero saber sobre plotters eco solvente
Bruno: Olá! Que bom que chegou até nós. Qual é o seu nome e de onde você é?
Cliente: Ricardo, de Blumenau
Bruno: Blumenau, ótimo! Temos técnico fixo aí. O que você produz hoje, Ricardo?
Cliente: Tenho uma Roland BN-20 e uma Epson, faço lona, banner e adesivo. Preciso de mais capacidade
Bruno: Entendido. Você está terceirizando produção excedente hoje?
Cliente: Sim, terceirizo em torno de 80m por semana e pago uns R$ 1.200 em tinta todo mês
Bruno: Com esse volume, a DG 1802i resolve bem. Duas cabeças i3200, 90m²/h em 2 passadas. Qual o seu custo atual por m² terceirizando?
Cliente: Umas R$ 12 o metro. É caro né
Bruno: Caro sim. Com a DG 1802i seu custo cai para R$ 4-5/m². Em 300m/semana você recupera o investimento em menos de 12 meses só com o que economiza na terceirização. Quer que eu simule?
Cliente: Quero sim. Qual o preço?
Bruno: R$ 68.900 com 40% de entrada + 10x sem juros. Entrada de R$ 27.560. Você tem CNPJ?
Cliente: Tenho sim. 12.345.678/0001-99
Bruno: Consultei aqui. Simples Nacional, empresa ativa, tudo certo para parcelamento em boleto. O consultor monta a proposta formal. Antes de fechar, me conta: qual tinta você usa hoje na Roland e na Epson?
Cliente: BM do Brasil, pago uns R$ 1.200 por mês
Bruno: A nossa DGeco Premium é compatível com a Roland e tem um custo por litro melhor. O consultor vai te apresentar uma proposta de fornecimento para o parque inteiro. Faz sentido?
Cliente: Faz sim. Meu email é ricardo@grafica360.com.br, telefone (47) 99812-3456
Bruno: Perfeito Ricardo! Tenho tudo aqui. Quero confirmar: garantia de 12 meses nas partes estruturais, instalação e treinamento gratuitos por 2 dias em Blumenau. As despesas de deslocamento ficam por sua conta — calculamos no fechamento. Alguma dúvida antes de eu passar para o consultor?
Cliente: Não, pode passar
Bruno: Perfeito! Passei seus dados para nosso time comercial. Em breve um consultor entra em contato com a proposta. Foi um prazer, qualquer dúvida é só chamar!"""

# ── Monta resumo completo ─────────────────────────────────────────────────

RESUMO = f"""=== LEAD WHATSAPP — DOSS GROUP ===

DADOS DO CLIENTE
Nome:       {NOME}
WhatsApp:   {PHONE}
Telefone:   {TELEFONE}
E-mail:     {EMAIL}
Cidade:     {CIDADE}
CNPJ:       {CNPJ}
Regime:     {REGIME}

INTERESSE COMERCIAL
Produto:    {PRODUTO}
Tecnologia: {TECNOLOGIA}
Perfil:     {PERFIL}
Valor est.: R$ {VALOR_EST:,.0f}

PARQUE DE MAQUINAS ATUAL
Maquinas:    {PARQUE}
Tinta atual: {TINTA_ATUAL}
Custo tinta: {CUSTO_TINTA}

ORIGEM
Canal:       {ORIGEM}

CONVERSA (ultimas mensagens)
{HISTORICO_CONVERSA}"""


# ── Executa o teste ───────────────────────────────────────────────────────

async def main():
    print("\n" + "="*55)
    print("  TESTE DE PREENCHIMENTO ARCCA — DOSS GROUP")
    print("="*55)
    print(f"\n  Lead:    {NOME} ({PHONE})")
    print(f"  Produto: {PRODUTO}")
    print(f"  Cidade:  {CIDADE}")
    print(f"  Origem:  {ORIGEM}")
    print(f"  Valor:   R$ {VALOR_EST:,.0f}")
    print(f"  Parque:  {PARQUE}")
    print(f"  Tinta:   {TINTA_ATUAL} — {CUSTO_TINTA}")
    print("\n" + "-"*55)

    # Importa o arcca_client do projeto
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from app.services.arcca_client import escalate_to_human

    print("\n[1/1] Criando card no Arcca com todos os campos...")

    ok = await escalate_to_human(
        phone          = PHONE,
        name           = NOME,
        summary        = RESUMO,
        produto        = PRODUTO,
        cidade         = CIDADE,
        origem         = ORIGEM,
        valor_estimado = VALOR_EST,
        tecnologia     = TECNOLOGIA,
        perfil         = PERFIL,
        serasa_nota    = SERASA_NOTA,
    )

    print("\n" + "="*55)
    if ok:
        print("  ✅ SUCESSO! Card criado no Arcca.")
        print("  Verifique no CRM DOSS 2025 → pipeline NOVO")
        print(f"\n  Título esperado:")
        print(f"  Lead WhatsApp - {NOME} - {PRODUTO} - {CIDADE}")
        print(f"\n  Campos preenchidos:")
        print(f"  ✅ Dados do cliente (nome, WhatsApp, email, cidade, CNPJ)")
        print(f"  ✅ Interesse comercial (produto, tecnologia, valor estimado)")
        print(f"  ✅ Parque de máquinas ({PARQUE})")
        print(f"  ✅ Tinta atual ({TINTA_ATUAL} — {CUSTO_TINTA})")
        print(f"  ✅ Origem do lead ({ORIGEM})")
        print(f"  ✅ Nota de conversa completa")
        print(f"  ✅ Nota Serasa separada")
    else:
        print("  ❌ FALHA ao criar card no Arcca.")
        print("  Verifique ARCCA_QUEUE_KEY no .env e os logs acima.")
    print("="*55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
