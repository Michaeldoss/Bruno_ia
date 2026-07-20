"""
Sistema de Campanhas — Bruno IA / Doss Group
──────────────────────────────────────────────
Cada campanha tem:
  - codigo:       palavra-chave que vem no link wa.me (?text=CODIGO)
  - nome:         nome interno da campanha
  - produto:      produto principal
  - origem:       canal (Instagram, Facebook, Google, etc.)
  - vigencia:     data de início e fim (None = sem prazo)
  - condicoes:    condições especiais de pagamento
  - brinde:       kit / brinde incluso (se houver)
  - desconto:     desconto especial (se houver)
  - contexto:     texto injetado no prompt do Bruno com as regras da campanha
  - ativa:        True/False manual (para pausar sem deletar)

Como usar:
  Link do anúncio: https://wa.me/5547991933197?text=1801kit
  Cliente clica → WhatsApp abre com "1801kit" pré-preenchido
  Bruno detecta o código e carrega as condições da campanha
"""

from datetime import date
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# CADASTRO DE CAMPANHAS
# ─────────────────────────────────────────────────────────────────────────────

CAMPANHAS = {

    # ── CAMPANHA 1 — DG 1801/2 com Kit Completo ──────────────────────────────
    "1801kit": {
        "nome":    "DG 1801/2 Kit Completo",
        "produto": "Plotter DG 1802i",
        "origem":  "Trafego Pago- Instagram",
        "vigencia": (date(2026, 4, 22), date(2026, 5, 31)),
        "ativa":   True,
        "condicoes": "35% entrada + 10x sem juros no boleto",
        "brinde":    "Kit incluso: 5 litros de tinta DGeco + 1 rolo de papel transfer 100m",
        "desconto":  "Entrada reduzida de 40% para 35% exclusivo desta campanha",
        "contexto": """
[CAMPANHA ATIVA: DG 1801/2 KIT COMPLETO]
O cliente veio do anúncio da DG 1802i com kit.

CONDIÇÕES EXCLUSIVAS DESTA CAMPANHA:
- Produto: Plotter DG 1802i (2 cabeças i3200, eco solvente ou sublimática)
- Preço: R$ 68.900
- Entrada ESPECIAL: 35% (R$ 24.115) + 10x sem juros no boleto
- KIT INCLUSO: 5 litros de tinta DGeco Premium + 1 rolo papel transfer 100m
- Campanha válida até 31/05/2026

COMO ABORDAR:
1. Confirme que o cliente viu o anúncio da 1802i com kit
2. Apresente as condições especiais IMEDIATAMENTE — não faça ele esperar
3. Destaque o kit incluso como diferencial: "Já sai imprimindo no dia da instalação"
4. Condição de entrada reduzida é exclusiva — use como urgência

SCRIPT SUGERIDO:
"Você veio pelo anúncio certo! A DG 1802i está com condição especial:
entrada de 35% em vez dos 40% padrão, mais o kit com 5 litros de tinta
e 1 rolo de papel transfer incluso. Você já sai imprimindo no dia da instalação.
Me conta, você já está no ramo ou está começando?"

NUNCA diga que a campanha vai acabar para pressionar — use apenas se o cliente pedir prazo.
""",
    },

    # ── CAMPANHA 2 — DTF 3002 Entrada Facilitada ─────────────────────────────
    "dtf30entrada": {
        "nome":    "DTF 3002 Entrada Facilitada",
        "produto": "DTF Têxtil 3002",
        "origem":  "Trafego Pago- Facebook",
        "vigencia": (date(2026, 4, 22), date(2026, 5, 15)),
        "ativa":   True,
        "condicoes": "30% entrada + 12x sem juros no boleto",
        "brinde":    "Treinamento presencial de 2 dias incluso + kit startup DTF",
        "desconto":  "Entrada reduzida para 30% e parcelamento estendido para 12x",
        "contexto": """
[CAMPANHA ATIVA: DTF 3002 ENTRADA FACILITADA]
O cliente veio do anúncio do DTF 3002 com entrada facilitada.

CONDIÇÕES EXCLUSIVAS DESTA CAMPANHA:
- Produto: DG DTF Têxtil 3002 (2 cabeças i1600, largura 300mm)
- Preço: R$ 52.900
- Entrada ESPECIAL: 30% (R$ 15.870) + 12x sem juros no boleto (R$ 3.085/mês)
- KIT STARTUP incluso: tinta DTF branco + CMYK para os primeiros 50 metros
- Treinamento presencial de 2 dias no cliente incluso
- Campanha válida até 15/05/2026

COMO ABORDAR:
1. Confirme o interesse em DTF têxtil
2. Apresente a entrada de 30% como facilitador para quem está começando
3. O kit startup é o argumento de "zero risco" — já começa produzindo
4. 12x reduz o impacto mensal para R$ 3.085

PERFIL ESPERADO: iniciante ou upgrade de plotagem para DTF

SCRIPT SUGERIDO:
"Você veio pela oferta certa! O DTF 3002 está com entrada de 30%
em vez dos 40% normais, mais 12 parcelas sem juros — fica R$ 3.085 por mês.
E já vem com o kit de tinta para você imprimir os primeiros 50 metros.
Você já trabalha com personalização hoje ou está começando do zero?"
""",
    },

    # ── CAMPANHA 3 — DG 1908i Alta Produção ──────────────────────────────────
    "1908pro": {
        "nome":    "DG 1908i Alta Produção",
        "produto": "Plotter DG 1908i",
        "origem":  "Trafego Pago- Instagram",
        "vigencia": (date(2026, 5, 1), date(2026, 5, 31)),
        "ativa":   False,  # ainda não ativa
        "condicoes": "40% entrada + 18x sem juros no boleto",
        "brinde":    "Instalação e treinamento com custo de deslocamento por conta da Doss",
        "desconto":  "Parcelamento estendido para 18x e deslocamento técnico incluso",
        "contexto": """
[CAMPANHA ATIVA: DG 1908i ALTA PRODUÇÃO]
O cliente veio do anúncio da máquina de 8 cabeças para alta produção.

CONDIÇÕES EXCLUSIVAS:
- Produto: DG 1908i (8 cabeças i3200, até 250m²/h)
- Preço: R$ 265.000
- Entrada: 40% (R$ 106.000) + 18x sem juros (R$ 9.722/mês)
- INCLUSO: deslocamento do técnico para instalação por conta da Doss
- Válido: maio/2026

PERFIL ESPERADO: gráficas de médio/grande porte, alta produção de lonas/banners
""",
    },

    # ── CAMPANHA — Combo de Sublimação DG 1801/2 ─────────────────────────────
    "combo_sublimacao": {
        "nome":    "Combo de Sublimação DG 1801/2",
        "produto": "Plotter DG 1801i / DG 1802i",
        "origem":  "Trafego Pago- Instagram",
        "vigencia": None,  # sem prazo definido
        "ativa":   True,
        "condicoes": "Condições padrão da tabela (40% entrada + 10x sem juros)",
        "brinde":    "Kit DGtex 1 litro CMYK completo + 1 rolo papel sublimático 1,60m x 300m x 38g tratado",
        "desconto":  "",
        "contexto": """
[CAMPANHA ATIVA: COMBO DE SUBLIMAÇÃO DG 1801/2]
O cliente veio pelo anúncio do Combo de Sublimação.

PRODUTO DESTA CAMPANHA:
A DG 1801/2 existe em duas versões — deixe o cliente escolher:

- DG 1801i (1 cabeça i3200): entrada no mercado, ideal para começar
- DG 1802i (2 cabeças i3200): mais velocidade e produtividade, ideal para crescer

Preços: consulte a tabela de preços em tempo real (Google Sheets).
Condições: padrão da tabela (40% entrada + 10x sem juros no boleto).

KIT CORTESIA INCLUSO NAS DUAS VERSÕES:
- 1 Kit DGtex CMYK completo (1 litro de cada cor: Ciano, Magenta, Amarelo e Preto)
- 1 Rolo de papel sublimático 1,60m x 300m x 38g tratado
O cliente já sai imprimindo no dia da instalação.

COMO ABORDAR:
1. Confirme o interesse em sublimação e entenda o que o cliente quer produzir
2. Apresente as duas versões (1 ou 2 cabeças) com os preços da tabela
3. Destaque o kit cortesia como diferencial — "já vem com tinta e papel para começar"
4. Faça o diagnóstico normal: cidade, volume, ramo, se já tem equipamento

SCRIPT DE APRESENTAÇÃO DO KIT:
"Essa campanha vem com um kit cortesia completo: 1 litro de cada cor DGtex (CMYK)
e um rolo de papel sublimático 1,60m x 300m. Você já sai produzindo no dia da instalação,
sem precisar comprar nada separado para começar."

PERGUNTA CHAVE para direcionar entre 1 ou 2 cabeças:
"Você está começando agora ou já tem produção e quer expandir?"
- Começando → DG 1801i (1 cabeça, menor investimento)
- Expandindo → DG 1802i (2 cabeças, mais velocidade)

NUNCA force uma versão sem entender o volume do cliente.
""",
    },
    # Usada quando não detecta nenhum código de campanha
    # ── CAMPANHA — 40% OFF Tinta DGtex Premium/Lite (a vista) ────────────────
    "dgtex40off": {
        "nome":    "40% OFF DGtex Premium/Lite (a vista)",
        "produto": "Tinta Sublimatica DGtex Premium / DGtex Lite",
        "origem":  "Trafego Pago- WhatsApp",
        "vigencia": (date(2026, 7, 1), date(2026, 7, 31)),
        "ativa":   True,
        "condicoes": "40% de desconto sobre o preco de tabela, pagamento A VISTA, frete FOB (por conta do cliente)",
        "brinde":    "",
        "desconto":  "40% sobre o preco de tabela das linhas DGtex Premium e DGtex Lite",
        "contexto": """
[CAMPANHA ATIVA: 40% OFF TINTA DGTEX PREMIUM/LITE]
O cliente veio do anuncio de 40% off na tinta sublimatica DGtex.

CONDICOES EXCLUSIVAS DESTA CAMPANHA (diferente do fluxo normal de
maquina -- aqui e so tinta, condicao de pagamento e OUTRA):
- Produtos: DGtex Premium e DGtex Lite (linhas de tinta sublimatica)
- Desconto: 40% sobre o preco de tabela -- consulte o preco atual na
  planilha (nao invente valor, sempre confira o preco vigente)
- Pagamento: A VISTA (nao e o parcelamento padrao 40%+10x de maquina
  -- aqui o 40% JA E o desconto, pago tudo de uma vez)
- Frete: FOB -- o cliente e responsavel pelo frete (ou retira)
- Conversao de impressora: a Doss auxilia remotamente com conversao
  e criacao de perfil de cor SEM custo. Se o cliente pedir tecnico
  presencial, os custos de conversao ficam por conta dele. Perto de
  Joinville, pode ter disponibilidade de tecnico da equipe (com ou
  sem custo, depende da agenda) -- NUNCA prometa tecnico ou custo
  exato, diga que vai confirmar com a equipe tecnica.
- Valida ate 31/07/2026

COMO ABORDAR:
1. Confirme que o cliente veio pelo anuncio do desconto na tinta
2. Pergunta qual linha interessa (Premium ou Lite) e o volume que usa
3. Deixe claro que e a vista com frete FOB -- nao ofereca parcelamento
   pra esse desconto
4. Sobre conversao: auxilio remoto é gratis; tecnico presencial tem
   custo por conta do cliente, dependendo da disponibilidade da
   equipe -- nunca garanta prazo ou valor de tecnico, diga que vai
   verificar

SCRIPT SUGERIDO:
"Voce veio pela promocao certa! A tinta DGtex Premium e Lite estao
com 40% de desconto sobre a tabela esse mes, pagamento a vista e
frete por sua conta (FOB). A gente te ajuda remotamente com a
conversao da impressora e o perfil de cor sem custo -- se precisar
de tecnico presencial, isso tem custo a parte e depende da agenda da
nossa equipe. Voce ja usa sublimatica hoje ou vai comecar agora?"
""",
    },

    # ── CAMPANHA — Conversão Epson F6200/F6070/F6370 ─────────────────────────
    "conversao_epson": {
        "nome":    "Conversão Epson para Compatível",
        "produto": "Chip/Conversão de placa Epson F6200/F6070/F6370 + Tinta DGtex Premium",
        "origem":  "Trafego Pago- WhatsApp",
        "vigencia": None,
        "ativa":   True,
        "condicoes": "Chip avulso a vista R$35,00/unidade ou conversao de placa R$1.700,00",
        "brinde":    "",
        "desconto":  "",
        "contexto": """
[CAMPANHA ATIVA: CONVERSAO EPSON PARA COMPATIVEL]
O cliente veio do anuncio de conversao de impressora Epson para usar
tinta compativel (nao gera desconto, e servico de conversao + venda
de tinta compativel).

MODELOS ELEGIVEIS: Epson F6200, F6070, F6370 (SOMENTE ESTES).
Se o cliente tiver outro modelo (ex: SC-series, F9xxx, F7xxx),
NAO prometa conversao -- informe que precisa confirmar viabilidade
com a equipe tecnica antes de qualquer promessa.

DUAS OPCOES DE CONVERSAO (apresente as duas, deixe o cliente escolher):
1. CHIP AVULSO (desbloqueio simples): R$35,00 por unidade de chip.
   Nao remove a trava de fabrica permanentemente -- pode precisar
   trocar chip a cada recarga, dependendo do sistema da maquina.
2. CONVERSAO DE PLACA (definitiva): R$1.700,00. Remove a necessidade
   de chip para sempre -- maquina passa a aceitar tinta compativel
   sem bloqueio.

TINTA COMPATIVEL RECOMENDADA (DGtex Premium -- linha padrao Doss,
usada para todos os segmentos):
- 1 Litro: R$180,00/L
- Galao 5L: R$725,00 = R$145,00/L efetivo (mais barato que comprar em litro)
Sempre confirme o preco vigente na planilha antes de fechar -- pode mudar.
Use o galao como argumento se o cliente tiver volume de consumo alto:
economiza R$35,00/L em relacao ao litro fechado.

COMO ABORDAR:
1. Confirme o modelo exato da Epson (precisa ser F6200, F6070 ou F6370)
2. Pergunte o volume de impressao mensal do cliente
3. Apresente as duas opcoes de conversao (chip R$35 vs placa R$1.700)
   explicando a diferenca: chip e mais barato mas pode exigir troca
   recorrente; placa e investimento maior mas resolve definitivo
4. Recomende DGtex Premium como linha de tinta, mencione opcao de
   galao para quem tem volume alto
5. NUNCA invente prazo de instalacao ou disponibilidade de tecnico --
   confirme com a equipe tecnica

SCRIPT SUGERIDO:
"Voce veio pela campanha certa! Pra conversao da Epson a gente atende
os modelos F6200, F6070 e F6370. Tem duas formas de fazer: o chip
avulso por R$35 a unidade (mais simples, mas pode precisar trocar
com frequencia) ou a conversao de placa por R$1.700 (resolve de vez,
sem precisar de chip nunca mais). Qual e o seu modelo exato e quanto
voce imprime por mes? Assim te indico a melhor opcao e a tinta certa
pra sua producao."

PERGUNTA CHAVE: modelo exato + volume mensal -- sem isso nao
recomende opcao de conversao.
""",
    },

    "_padrao": {
        "nome":    "Atendimento Padrão",
        "produto": "",
        "origem":  "WhatsApp Direto",
        "vigencia": None,
        "ativa":   True,
        "condicoes": "40% entrada + 10x sem juros no boleto",
        "brinde":    "",
        "desconto":  "",
        "contexto": "",  # sem contexto especial — fluxo normal do Bruno
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÕES DE DETECÇÃO E ACESSO
# ─────────────────────────────────────────────────────────────────────────────

def detectar_campanha(primeira_mensagem: str) -> dict:
    """
    Detecta a campanha pela primeira mensagem do cliente.
    Aceita tanto código curto (ex: 1801kit) quanto frases completas.
    Retorna o dict da campanha ou a campanha padrão.
    """
    if not primeira_mensagem:
        return CAMPANHAS["_padrao"]

    texto = primeira_mensagem.lower().strip()

    # Mapeamento de frases/gatilhos para código de campanha
    GATILHOS_FRASE = {
        "combo de sublimação": "combo_sublimacao",
        "combo sublimacao":    "combo_sublimacao",
        "combo sublimação":    "combo_sublimacao",
        "interesse no combo":  "combo_sublimacao",
        "40% off na tinta sublimatica dgtex": "dgtex40off",
        "anuncio de 40% off": "dgtex40off",
        "40 off na tinta": "dgtex40off",
        "tenho interesse em converter minha epson": "conversao_epson",
        "converter minha epson": "conversao_epson",
    }

    # Verifica gatilhos de frase primeiro
    for gatilho, codigo in GATILHOS_FRASE.items():
        if gatilho in texto:
            campanha = CAMPANHAS.get(codigo)
            if campanha and campanha.get("ativa", False):
                campanha = dict(campanha)  # copia para não mutar o original
                campanha["_codigo"] = codigo
                return campanha

    # Verifica códigos curtos
    for codigo, campanha in CAMPANHAS.items():
        if codigo in ("_padrao",):
            continue
        if not campanha.get("ativa", False):
            continue

        vigencia = campanha.get("vigencia")
        if vigencia:
            inicio, fim = vigencia
            if not (inicio <= date.today() <= fim):
                continue

        if codigo.lower() in texto:
            campanha = dict(campanha)
            campanha["_codigo"] = codigo
            return campanha

    return CAMPANHAS["_padrao"]


def get_contexto_campanha(campanha: dict) -> str:
    """Retorna o contexto da campanha para injetar no prompt do Bruno."""
    return campanha.get("contexto", "")


def get_origem_campanha(campanha: dict) -> str:
    """Retorna a origem para o card do CRM."""
    return campanha.get("origem", "WhatsApp Direto")


def listar_campanhas_ativas() -> list:
    """Lista todas as campanhas ativas e dentro da vigência."""
    hoje = date.today()
    ativas = []
    for codigo, c in CAMPANHAS.items():
        if codigo == "_padrao": continue
        if not c.get("ativa"): continue
        v = c.get("vigencia")
        if v and not (v[0] <= hoje <= v[1]): continue
        ativas.append({
            "codigo":  codigo,
            "nome":    c["nome"],
            "produto": c["produto"],
            "origem":  c["origem"],
            "fim":     v[1].strftime("%d/%m/%Y") if v else "Sem prazo",
        })
    return ativas


# ─────────────────────────────────────────────────────────────────────────────
# TESTE LOCAL
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== CAMPANHAS ATIVAS ===")
    for c in listar_campanhas_ativas():
        print(f"  [{c['codigo']}] {c['nome']} | {c['origem']} | até {c['fim']}")

    print("\n=== TESTE DE DETECÇÃO ===")
    testes = ["1801kit", "vim pelo anuncio dtf30entrada", "oi", "1908pro", "quero saber sobre plotters"]
    for msg in testes:
        c = detectar_campanha(msg)
        print(f"  '{msg}' → {c['nome']}")
