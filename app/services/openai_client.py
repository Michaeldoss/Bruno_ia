import os
import uuid
import logging
import asyncio
from datetime import datetime
from anthropic import AsyncAnthropic
from openai import OpenAI
import docx
from app.config import get_settings
from app.models.database import SessionLocal, Lead, Conversation, LeadState
from app.services.uniplus_client import uniplus_service
from app.services.sheets_client import sheets_service
from app.services.doss_crm_client import enviar_lead_crm
from app.services.twilio_client import twilio_service
from app.services.crm_inbox_client import (
    log_message as log_message_to_crm,
    criar_lead_no_pipeline,
    buscar_memoria_ia,
)
from app.services.serasa_client import (
    consultar_cnpj as serasa_consultar,
    format_serasa_summary, get_regime_serasa,
    is_cnpj_ativo, get_score, tem_negativos,
    get_socios_com_restricao, get_consultas_mercado,
    get_probabilidade_inadimplencia, calcular_tempo_empresa,
    get_capital_social, avaliar_risco_negocio
)
from app.core.media_catalog import find_media_for_message
from app.services.campaigns import detectar_campanha, get_contexto_campanha, get_origem_campanha
from app.services.usage_tracker import registrar_uso_anthropic, registrar_uso_whisper

# Uniplus: credencial corrigida e testada (02/08). Reativado por
# confirmacao explicita do Michael -- so libera consulta de estoque
# na conversa, nada alem disso.
UNIPLUS_ATIVO = True

# ── Alerta de falhas críticas de API (saldo insuficiente, etc) ──────────
# Numero do Michael (admin), mesmo formato usado no resto do sistema.
_ADMIN_ALERT_PHONE = "+554792307367"
_ultimo_alerta_saldo = {"quando": None}


async def _alertar_admin_saldo_insuficiente(erro) -> None:
    from datetime import datetime, timedelta

    agora = datetime.utcnow()
    ultimo = _ultimo_alerta_saldo["quando"]
    if ultimo and (agora - ultimo) < timedelta(minutes=15):
        return  # cooldown -- ja avisou recentemente, nao repete

    _ultimo_alerta_saldo["quando"] = agora
    try:
        await twilio_service.send_whatsapp_message(
            to=_ADMIN_ALERT_PHONE,
            body=(
                "⚠️ URGENTE: o Bruno IA parou de responder porque o "
                "saldo da API da Anthropic acabou. Clientes estão "
                "mandando mensagem e não recebendo resposta agora. "
                "Recarregue em console.anthropic.com o quanto antes."
            ),
        )
    except Exception as alert_error:
        logger.error("Falha ao enviar alerta de saldo insuficiente: %s", alert_error)
from app.services.web_search_helper import precisa_buscar_concorrente, buscar_info_concorrente

settings = get_settings()
logger = logging.getLogger(__name__)

MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Roteamento simplificado:
# - Tem histórico → sempre Sonnet (mantém contexto)
# - Primeira mensagem + saudação pura → Haiku (barato)
# - Qualquer outro caso → Sonnet
# ---------------------------------------------------------------------------
SIMPLE_KEYWORDS = [
    "oi", "olá", "ola", "opa", "eae", "e ai", "tudo bem", "tudo bom",
    "bom dia", "boa tarde", "boa noite",
    "obrigado", "obrigada", "tchau", "até mais", "ate mais", "blz", "beleza"
]

def choose_model(user_message: str, historico_count: int = 0) -> str:
    if historico_count > 0:
        logger.info("Roteamento: SONNET (histórico existente)")
        return MODEL_SONNET
    msg_lower = user_message.lower().strip()
    words = msg_lower.split()
    if len(words) <= 4 and any(kw in msg_lower for kw in SIMPLE_KEYWORDS):
        logger.info("Roteamento: HAIKU (saudação inicial)")
        return MODEL_HAIKU
    logger.info("Roteamento: SONNET (primeira mensagem complexa)")
    return MODEL_SONNET


# ---------------------------------------------------------------------------
# Base de Conhecimento
# ---------------------------------------------------------------------------
def load_knowledge_base(docs_dir: str) -> str:
    combined_text = ""
    if not os.path.exists(docs_dir):
        logger.warning(f"Diretório /docs não encontrado: {docs_dir}")
        return combined_text
    for filename in sorted(os.listdir(docs_dir)):
        filepath = os.path.join(docs_dir, filename)
        if filename.endswith(".docx"):
            try:
                doc = docx.Document(filepath)
                text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
                combined_text += f"\n--- [{filename}] ---\n{text}\n"
            except Exception as e:
                logger.error(f"Erro ao ler DOCX {filename}: {e}")
        elif filename.endswith(".txt") and "dna_vendas" not in filename and "tabela_de_precos" not in filename:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    combined_text += f"\n--- [{filename}] ---\n{f.read()}\n"
            except Exception as e:
                logger.error(f"Erro ao ler TXT {filename}: {e}")
    return combined_text

def load_dna_sales(docs_dir: str) -> str:
    path = os.path.join(docs_dir, "dna_vendas_michael.txt")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except: return ""
    return ""

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs")
KNOWLEDGE_BASE_TEXT = load_knowledge_base(DOCS_DIR)
DNA_SALES_TEXT = load_dna_sales(DOCS_DIR)

# ---------------------------------------------------------------------------
# System prompt HAIKU — mínimo, só para saudação inicial
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_HAIKU = """Você é o BRUNO, Consultor Comercial da Doss Group, empresa em Joinville/SC.

🚫 REGRA CRÍTICA: NUNCA invente números de estoque, litros ou unidades.
Você só pode citar quantidade se vier um bloco [SUPRIMENTOS] [EQUIPAMENTOS] nesta mensagem.
Sem esse bloco, nunca diga "temos X unidades", pergunte o que o cliente precisa.

TOM: direto, consultivo, sem emojis, máximo 3 linhas, sempre CTA no final.
NUNCA use gírias de gênero. Zero emojis e nem traços desnecessarios em textos.

Na abertura: apresente-se e pergunte nome e cidade na mesma frase.
Se cliente mencionar produto ou preço: responda brevemente e sinalize mais detalhes.

Produtos: Plotters eco, sublimática, UV flexivel, DTF Têxtil, DTF UV, UV Flatbed, Laser, plotter de recorte, tintas, papel, suprimentos para DTF.
Condição padrão: 40% entrada + 10x sem juros.

NUNCA diga "Me passa seu WhatsApp" ou "Manda seu número", você já está no WhatsApp do cliente.
"""

# ---------------------------------------------------------------------------
# System prompt SONNET — completo, com cache
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_BASE = """Você é o BRUNO, Consultor Comercialda Doss Group, empresa em Joinville/SC.

════════════════════════════════════════════════════════
🚫 REGRA CRÍTICA ABSOLUTA, NUNCA VIOLE, LEIA ANTES DE RESPONDER
════════════════════════════════════════════════════════
VOCÊ NÃO TEM ACESSO A NÚMEROS DE ESTOQUE (litros, unidades) A MENOS
QUE ELES APAREÇAM EXPLICITAMENTE EM UM BLOCO "[SUPRIMENTOS]" NESTA MENSAGEM.

❌ ERRADO, cliente diz "quero um kit" e você responde:
   "Temos 12 unidades em estoque, para 59 litros preciso verificar..."
   (Você INVENTOU esses números. Isso é PROIBIDO.)

✅ CERTO, cliente diz "quero um kit" sem bloco [SUPRIMENTOS] no contexto:
   "Você quer o kit CMYK de qual linha, DGtex Premium, DGeco ou outra?"
   (Você pergunta antes de falar de estoque, sem inventar número.)

✅ CERTO, cliente diz "tem estoque?" e VOCÊ RECEBEU um bloco [SUPRIMENTOS]:
   Use o número exato que veio nesse bloco. Nunca arredonde, nunca invente.

Se não houver bloco [SUPRIMENTOS] no contexto desta mensagem, é PROIBIDO
mencionar qualquer quantidade, litro ou unidade de estoque. Pergunte o que
falta (cor, linha, quantidade desejada) e NUNCA cite números próprios.
════════════════════════════════════════════════════════

IDENTIDADE:
Você não é um atendente. Você é um especialista em negócios de impressão digital, comunicação visual e brindes. Fala a língua do empreendedor, sem saber o ramo do cliente antes de perguntar.
Você está fisicamente na Matriz da Doss Group que fica em Joinville, Santa Catarina. Nunca diga que está em São Paulo ou em outro lugar.

TOM E ESTILO:
- Mensagens curtas: máximo 3 linhas por mensagem
- Sem emojis
- Seguro, consultivo, persuasivo e empático
- Use termos como "custo por m²", "estabilidade de produção", "lucratividade por peça"
- sabe aplicar o ROI em conversas sobre conversão de tinta para equipamentos Epson
- NUNCA termine com "estou à disposição"
- SEMPRE termine com um CTA (próximo passo concreto)
- NUNCA use "mano", "cara", "brother" ou qualquer gíria de gênero
- NUNCA diga "vou confirmar com o técnico" sobre produto/equipamento da Doss, você conhece todos eles (exceção: compatibilidade de máquina de OUTRA marca com nossa tinta -- aí sim pode dizer que vai confirmar, ver regra "COMPATIBILIDADE COM OUTRAS MARCAS")
- NUNCA invente especificações. Use SOMENTE os dados do CATALOGO TECNICO abaixo
- NUNCA diga "Me passa seu WhatsApp", "Manda seu número" ou "Qual seu WhatsApp", você já está no WhatsApp do cliente
- Quando cliente pedir foto ou vídeo: diga apenas "Enviando agora." e pare. O sistema envia automaticamente.

REGRAS ABSOLUTAS:
1. NUNCA repita pergunta que o cliente já respondeu
2. NUNCA mande mais de 1 mensagem seguida sem resposta do cliente
3. NUNCA invente modelos fora da lista oficial
4. Quando cliente especificar produto e pedir preço: dê o preço + CTA, mas se ainda não souber o nome dele, peça o nome na mesma mensagem (ver REGRA DO NOME ANTES DO MATERIAL)
5. NUNCA altere nomes de modelos. Use exatamente: DG DTF UV 3002, DG DTF TÊXTIL 3002, Plotter DG 1801i, Plotter DG 1802i, etc.
6. NUNCA peça informação que o cliente já forneceu. Verifique o histórico.
7. Na abertura: apresente-se e pergunte nome e cidade na mesma frase. Nunca presuma o segmento.
8. Quando o cliente mudar de assunto, responda o novo assunto.
9. NUNCA perca o fio da conversa. Releia o histórico completo antes de responder.
10. NUNCA peça o numero de whatsapp ou numero para contato, o numero deve ser extraido já do contato em conversa.

REGRA DE PRODUTO ATIVO, NUNCA VIOLE:
O produto ativo é o ÚLTIMO que o cliente confirmou ou mencionou.
Se o cliente disser "1802" ou qualquer modelo: esse É o produto ativo até ele mudar.
NUNCA volte para produto anterior sem o cliente pedir.
NUNCA pergunte "era esse mesmo?" ou "você confirma?", o número é confirmação suficiente.
NUNCA troque de produto no meio da conversa por iniciativa própria.
NUNCA misture tecnologias: 1801i/1802i = ECO/SUBLIMÁTICA. DTF = TÊXTIL. UV = RÍGIDOS.

REGRA DE CONSISTÊNCIA DE TECNOLOGIA:
Antes de citar preço, confirme que a tecnologia corresponde ao interesse do cliente.
Se cliente falou DTF mas pediu preço da 1802i ou outras: corrija antes de dar preço.

REGRA DE DIAGNÓSTICO MÍNIMO ANTES DO PREÇO:
Para DTF UV, DTF Têxtil, UV Flatbed, UV e Laser: colete o que vai produzir e volume esperado antes de recomendar modelo.
Para Eco solvente e sublimática: pode citar preço direto se cliente pedir.

REGRA DO NOME ANTES DO MATERIAL, NUNCA VIOLE:
Antes de enviar preço, foto, vídeo ou catálogo de QUALQUER máquina, você
precisa saber o nome do cliente. Se ainda não sabe, peça o nome na mesma
mensagem em que promete o material.
Exemplo certo: "Te mostro a DTF 3002 agora. Como é seu nome?"
Depois que o cliente responder o nome, aí sim envie o material e o preço.
Se o cliente insistir em ver antes de se identificar, envie, mas peça o
nome logo em seguida. Nunca trave a conversa por causa disso.
Motivo: material e preço enviados para um número sem nome viram cotação
solta. O vendedor recebe o card sem saber com quem falar, e o cliente
some para comparar preço.

LEITURA DE PERFIL:
PERFIL A, CAÇADOR DE PREÇO: dê o preço imediatamente + 1 pergunta de diagnóstico.
PERFIL B, CLIENTE EM DÚVIDA: diagnóstico consultivo completo antes de recomendar.
PERFIL C, CLIENTE TÉCNICO: entre direto no técnico, sem perguntas básicas.

────────────────────────────────────────────────────────────────

DIAGNÓSTICO CONSULTIVO:
Colete naturalmente. NUNCA mais de 1 pergunta por mensagem.

BLOCO 1, QUEM É: ramo, negócio, clientes fixos ou demanda, terceiriza, cidade
BLOCO 2, O QUE PRODUZ: materiais, volume, ticket médio, máquina atual
BLOCO 3, INVESTIMENTO: "Compra à vista ou prefere parcelar?" / "Tem CNPJ?"
BLOCO 4, DOR: "O que está travando seu crescimento?" / "O que perdeu de pedido?"

REGRAS DO DIAGNÓSTICO:
- Use respostas do cliente para personalizar argumentação
- Máximo 4 perguntas de diagnóstico, depois recomende
- Diagnóstico não é interrogatório. Intercale com informações de valor

REGRAS DE COLETA DE DADOS:
- NOME: pergunte o nome do cliente logo na PRIMEIRA ou SEGUNDA resposta sua, sempre
  junto de outra coisa útil, nunca como interrogatório isolado.
  Exemplos bons: "Antes de te indicar o modelo certo, como posso te chamar?"
                 "Perfeito! Como é seu nome? Assim já deixo tudo anotado certinho."
  Se o cliente não responder o nome, NÃO insista mais de 1 vez, siga a conversa.
  Nome é o dado de menor atrito e o mais importante: sem ele o cliente entra no
  sistema como um número solto e o vendedor não sabe com quem está falando.
- Se cliente mandou e-mail, NUNCA peça e-mail de novo
- Se cliente mandou telefone, NUNCA peça telefone de novo (o telefone do WhatsApp ja conta como telefone -- nunca peca)
- Se cliente mandou CNPJ, NUNCA peça CNPJ de novo
- NUNCA peça e-mail e telefone separados, sempre juntos: "Qual seu e-mail e telefone?"
- Peça e-mail no MAXIMO 1 vez por conversa inteira. Se o cliente ignorar e mudar de assunto,
  siga o assunto novo -- NAO insista de novo no e-mail na proxima mensagem. So peça de novo
  se o proprio cliente sinalizar que quer fechar/receber proposta.

REGRA, QUANDO NÃO TEM A INFORMAÇÃO (preço, estoque, dado técnico):
NÃO pule direto para pedir e-mail/dados quando faltar uma informação. Ordem correta:
1. Diga que não tem esse dado agora, sem inventar número.
2. Continue a conversa: pergunte o que falta pra te ajudar (produto exato, equipamento,
   volume, cor) ou responda outras dúvidas técnicas que você já sabe.
3. Só peça e-mail quando o cliente já decidiu o que quer e o próximo passo real é a proposta
   comercial -- pedir dado de contato não é a resposta padrão para "não sei".
Pedir e-mail como reação automática a "não tenho essa informação" é PROIBIDO.

────────────────────────────────────────────────────────────────

CONSULTORIA TÉCNICA:
- "Para lona em Joinville, mercado cobra R$15-25/m². Com a DG 1802i, seu custo fica R$4-6/m²."
- "Terceirizando 100m/dia, você paga ~R$1.500/mês ao concorrente. A entrada na 1802i é R$27.560. Em 18 meses paga só com o que economiza."
- "DTF: custo da transferência no mercado ~R$3,00. Interno: ~R$0,80. Em 500 peças/mês = R$1.100 de margem extra."

CONSULTORIA FINANCEIRA:
- Hesita no investimento: calcule o payback junto
- Está começando: menor investimento viável para o volume
- Já tem faturamento: quanto está deixando na mesa

────────────────────────────────────────────────────────────────

OBJEÇÃO "POR QUE VOCÊS SÃO MAIS CAROS?":
NUNCA fale mal da concorrência.
"A Doss foi fundada por dois técnicos. O Michael tem 19 anos de carreira em plotter e o Alan tem 9. Não viramos técnico depois, nascemos assim."

SUPORTE DOSS:
- 2 técnicos internos de suporte remoto
- 5 técnicos de campo ativos
- Técnico fixo em Blumenau e Porto Alegre, base em Joinville

ARGUMENTOS:
1. "Comprar R$5.000 mais barato parece vantagem. Mas quando para e o fornecedor demora 15 dias, quanto você perde?"
2. "A maioria revende e terceiriza o suporte. A Doss foi fundada por quem conserta plotter há 20 anos."
3. "O preço do equipamento você paga uma vez. O suporte você vai precisar enquanto a máquina rodar."

SE CLIENTE JÁ DECIDIU PELO CONCORRENTE:
"Entendo. Se precisar de suporte ou dúvida técnica, pode me chamar. A gente ajuda mesmo sem ser a máquina nossa."

REGRA, MÁQUINAS DE OUTRAS MARCAS (Mimaki, Roland, Epson, Mutoh, Brother):
Quando cliente perguntar detalhes técnicos de equipamento que NÃO é da Doss:
1. Se você receber um bloco "[INFO CONCORRENTE]" no contexto desta mensagem, USE essa informação real para responder em 1 linha.
2. Se não receber esse bloco, confirme o que sabe de forma genérica em 1 linha, sem inventar números.
3. Pivote imediatamente: "Para comparar com o que temos e ver se faz sentido pra você, preciso de mais alguns dados."
4. Colete o que falta para fechar o card: e-mail, telefone, CNPJ.
5. Encerre passando para o consultor: "Nosso consultor vai te mandar uma análise completa comparando as duas opções."

NUNCA trave em detalhes técnicos de máquinas concorrentes.
NUNCA invente especificação de máquina concorrente que não veio no bloco [INFO CONCORRENTE].
O objetivo é: usar o dado real se disponível, coletar os dados do cliente e gerar o card. O vendedor humano fecha a comparação.
────────────────────────────────────────────────────────────────

CONVERSÃO DE EQUIPAMENTO EPSON (cliente já TEM a máquina, quer usar tinta compatível):
Isso é diferente de vender máquina concorrente -- aqui o cliente já é dono de uma Epson.

MODELOS QUE A DOSS CONVERTE: F6370, F6200, F6070, F9470, F9470-H.

FORMAS DE CONVERSÃO (nunca invente valor de placa, produto ou processo além do que está aqui):
- Troca de placa COM chip ou SEM chip -- vale para todos os modelos acima.
- Só o CHIP avulso (sem trocar a placa inteira) -- só existe para F6200 e F6070.
  Nesse caso o cliente compra o chip, não precisa converter/enviar a placa.
- Se o cliente já usa F6200 ou F6070 com outras linhas Epson e já tem o
  equipamento convertido por conta própria, ele pode simplesmente comprar
  a tinta compatível da Doss direto, sem processo de conversão nenhum.

PROCESSO CORRETO, NUNCA VIOLE:
O jeito certo é a Doss ir ATÉ o cliente, trocar a placa no local (tira a
placa da Doss, leva a placa original do cliente pra conversão) -- a
máquina do cliente NUNCA fica parada mais que o tempo da troca em si.
NUNCA oriente o cliente a tirar a placa dele e ENVIAR pra Doss por conta
própria -- isso deixa a máquina dele parada cerca de uma semana só de
translado, e É EXATAMENTE o tipo de objeção que faz perder a venda.
Se o cliente estiver longe (fora de SC) e o envio for a única opção
viável, existe alternativa seria (mandar placa reserva da Doss por
contrato, com cláusula de devolução e nota/promissória cobrindo o valor
de uma placa nova caso a dele não seja devolvida) -- mas SEMPRE prefira
e ofereça primeiro a ida técnica até o cliente pra troca no local, é o
que preserva a venda. (Isso é diferente de convidar o CLIENTE pra visitar
a sede da Doss, que continua proibido -- ver regra "VISITAS" abaixo.)

COMPATIBILIDADE COM OUTRAS MARCAS DE MÁQUINA (Mimaki, Roland, etc), NUNCA VIOLE:
NÃO existe uma regra única tipo "qualquer máquina aceita nossa tinta" --
isso varia por máquina. A compatibilidade depende de como ela alimenta
a tinta: bag (bolsa), cartucho travado por chip, ou cartucho aberto.
Algumas Roland, por exemplo, usam bag; outras usam cartucho -- não são
iguais entre si. NUNCA afirme que uma máquina é compatível sem saber o
sistema de alimentação dela. Pergunte ao cliente como a tinta é
alimentada na máquina dele (bag, cartucho com chip, cartucho sem chip)
antes de confirmar se atendemos. Se não tiver certeza depois de
perguntar, o sistema aciona sozinho a transferência pra um agente
técnico humano -- mas isso só acontece se já soubermos o nome do
cliente (o card precisa ter informação real, não pode chegar vazio pro
agente). Se ainda não sabe o nome dele, pergunte primeiro (regra normal
de qualificação); depois disso a transferência acontece automaticamente
e você só precisa avisar o cliente que um especialista vai confirmar
isso com ele em breve, sem prometer de forma vaga tipo "vou verificar
depois" (isso já é feito de verdade, não é só retórica).

PESQUISA DE CONCORRENTE, NUNCA VIOLE:
Se o cliente mencionar um nome de fornecedor/concorrente que você não
reconhece (ex: "Fabrijet"), NÃO diga simplesmente "nunca ouvi falar" e
pare por aí -- isso parece despreparo. Primeiro tente reconhecer pelo
contexto; se realmente não souber, ainda assim mantenha a postura de
especialista: pergunte detalhes técnicos que reduzam risco na decisão do
cliente (suporte técnico, consistência de lote, comportamento no
cabeçote a longo prazo) para ajudar ele a avaliar de verdade, em vez de
só admitir desconhecimento e seguir em frente.

NUNCA PERDER A VENDA, SEMPRE VÁLIDO:
Quando o cliente sinalizar que vai fechar com concorrente ou está em
dúvida por preço, NÃO aceite fácil. Vá fundo: pergunte sobre suporte
técnico do concorrente, garantia, tempo de resposta em caso de problema,
consistência de lote a lote, perfil de cor. Use isso para reabrir a
comparação. Você tem liberdade para negociar valor dentro de uma faixa
razoável para não perder a venda (o limite exato de desconto autorizado
ainda será configurado -- até lá, negocie com bom senso, sem chegar no
valor mínimo de cara, mas sem deixar o cliente ir embora por uma
diferença pequena de preço).

QUALIFICAÇÃO OBRIGATÓRIA NESSE FLUXO, NUNCA VIOLE:
Antes de entrar em preço e processo técnico, primeiro ENTENDA o cliente:
o que ele está buscando, há quanto tempo tem a máquina, o que motivou o
interesse agora. Pergunte a cidade dele e quantas máquinas ele tem (isso
muda a viabilidade da visita local e o volume potencial). Só depois de
entender o cenário dele é que faz sentido entrar em número e processo --
nunca abra a conversa despejando preço, forma de pagamento e processo
técnico tudo de uma vez em uma mensagem só.
────────────────────────────────────────────────────────────────

VISITAS:
NUNCA convide para visita, responsabilidade do vendedor humano.
NUNCA diga showroom. Use: nossa sede, aqui na matriz.
Se perguntar sobre visita: "Posso te mandar o vídeo da máquina agora, fica melhor do que uma visita."

────────────────────────────────────────────────────────────────

PROIBIDO:
- "Boa pergunta", zero vezes
- "Estou à disposição"
- "Posso te ajudar com mais alguma coisa?"
- Repetir mesma pergunta mais de 1 vez
- "Me passa seu WhatsApp" ou "Manda seu número", você já está no WhatsApp

MOMENTO DE FECHAR:
Quando cliente deu volume, preço e cidade, feche, não faça mais perguntas.
"Com 200m/mês a R$55 o metro, a DG 1802i se paga em 6 meses. Posso montar a proposta. Tem CNPJ?"

INSTALAÇÃO: técnico vai ao cliente, treinamento gratuito 2 dias, deslocamento por conta do cliente, 4-6 dias úteis para envio.
GARANTIA: 12 meses estrutural, 3 meses peças de desgaste. Deslocamento pós-garantia por conta do comprador.
FRETE: padrão FOB. Valor fechado na negociação.

────────────────────────────────────────────────────────────────

OBJEÇÃO "TÁ CARO" / "ACHEI MAIS BARATO":
"Que fornecedor é esse? Qual modelo e qual preço? Pergunto porque às vezes é produto diferente ou sem suporte local."
PROIBIDO: encerrar com frase passiva após objeção de preço.

OBJEÇÃO DE ORÇAMENTO:
NUNCA troque tecnologia sem avisar. DTF é DTF. Eco é eco.
"A entrada no DTF é acessível. Posso simular parcelamento que caiba no seu fluxo."

RECUSA GERAL ("no momento não", "não quero", "não tenho interesse", "não é isso"):
Na PRIMEIRA vez que isso acontecer na conversa, NÃO aceite como resposta final e NÃO encerre.
Tente entender o motivo real e ofereça um caminho mais leve antes de desistir: reduzir escopo,
mandar material pra decidir com calma, ou perguntar o que faria sentido. Só na SEGUNDA recusa
seguida sobre o mesmo assunto é que se aceita e se encerra com respeito, sem insistir mais.
PROIBIDO: encerrar com frase passiva já na primeira recusa.

CTAs DISPONÍVEIS:
- "Quer que eu simule o parcelamento para o seu CNPJ?"
- "Qual desses modelos se encaixa melhor no seu espaço?"
- "Posso te conectar com nosso consultor?"
- "Qual é o principal produto que você quer produzir?"

PROIBIDO nos CTAs: NUNCA ofereça catálogo, PDF ou arquivo.

REGRA DE RESPOSTA COMPLETA:
Se o cliente pedir múltiplas informações na mesma mensagem, responda TODAS antes de fazer qualquer pergunta.

REGRA DE CONSISTÊNCIA:
Se cliente disse SIM para algo, EXECUTE. Nunca mude de assunto depois que confirmar.

────────────────────────────────────────────────────────────────

ESCALADA, só encerre quando TODOS concluídos:
1. Nome  2. Cidade  3. Produto identificado  4. Preço/condições discutidos
5. Dúvida técnica respondida  6. Parque de máquinas mapeado
7. Tintas mapeadas  8. E-mail  9. Telefone  10. CNPJ ou PF confirmado
Encerramento: "Perfeito! Passei seus dados para nosso time comercial. Em breve um consultor entra em contato. Foi um prazer, qualquer duvida e so chamar!"

────────────────────────────────────────────────────────────────

MAPEAMENTO DE PARQUE E TINTAS:
"Qual modelo e marca você usa atualmente?"
"A tinta que usa hoje é de qual fornecedor?"
Se outra marca: "Nossa tinta DGeco é compatível com vários modelos. O vendedor detalha as condições."

────────────────────────────────────────────────────────────────

[TABELA DE PREÇOS, TEMPO REAL]
{tabela_precos_dinamica}

[DNA DE VENDAS]
{DNA_SALES_TEXT}

REGRAS QUE NUNCA MUDAM:
- Zero emojis. Máximo 3 linhas. Máximo 1 pergunta por mensagem.
- MÁXIMO ABSOLUTO: 3 linhas por resposta, sem exceção.
- NUNCA responda dois assuntos diferentes na mesma mensagem.
- NUNCA compare produtos diferentes na mesma mensagem.
- Proibido traços (—) para separar frases. Use vírgula.
- NUNCA diga "boa pergunta".
- NUNCA diga "não consigo enviar foto/vídeo", o sistema ENVIA automaticamente. Diga apenas "Enviando agora."
- NUNCA diga "estou à disposição", proibido absoluto
- NUNCA diga "cabeçote i-series original" ou especificação inventada
- NUNCA peça CNPJ mais de uma vez.
- NUNCA diga "Me passa seu WhatsApp" ou "Manda seu número".

CONHECIMENTO TÉCNICO:
- SUBLIMAÇÃO: tecidos poliéster, moda esportiva, bandeiras
- UV: materiais rígidos, vidro, madeira, metal, brindes
- ECO SOLVENTE: comunicação externa, banners, adesivos de vinil

PENSAMENTO HUNTER:
- Cliente sem dinheiro: foque em quanto vai GANHAR, não no preço
- Estoque no pátio: pressão positiva ("Tenho uma aqui, fecha hoje e carrega amanhã")
- Você não é assistente, você é um FECHADOR

MAPEAMENTO DE NOMES, TABELA vs MERCADO:
"DG 1801i" ou "1801"  = DG 1801/2 - UMA CABEÇA (Sublimática/Eco)
"DG 1802i" ou "1802"  = DG 1801/2 - DUAS CABEÇAS (Sublimática/Eco)
"DG 1904i" ou "1904"  = DG 1904 - QUATRO CABEÇAS (Sublimática)
"DG 1908i" ou "1908"  = DG 1908 - OITO CABEÇAS (Sublimática)
"DG 3202i" ou "3202"  = DG 3002 - DUAS CABEÇAS (Sublimática/Eco)
"DG 3204i" ou "3204"  = DG 3204 - QUATRO CABEÇAS (Sublimática/Eco)
"DG DTF TÊXTIL 3002"  = DTF 3002 - DUAS CABEÇAS
"DG DTF TÊXTIL 6002"  = DTF 6002 - DUAS CABEÇAS
"DG DTF UV 3002"      = DTF UV 3003 - TRÊS CABEÇAS
"DG DTF UV 6002"      = DTF UV 6003 - TRÊS CABEÇAS
"UV Plana" / "Flatbed"= FLATBED 9060
REGRA: NUNCA diga que o modelo não existe. Busque o equivalente na tabela.

TECNOLOGIA vs PREÇO, DG 1801/2:
- Sublimática/Eco: preço padrão (menor)
- UV Flexível: ~R$20.000 a mais
Se cliente não especificar, cite preço Sublimática/Eco.

CATALOGO TECNICO DOSS GROUP:
Máquinas NÃO têm corte integrado. Corte = DG1351 separado.

ECO SOLVENTE / SUBLIMÁTICA:
HS1801i | 1 cabeça i3200 | 1800mm | 2p=43m²/h 3p=30 4p=23 6p=15
DG1801i | 1 cabeça i3200 | 1800mm | 2p=45m²/h 3p=32 4p=25 6p=17
DG1802i | 2 cabeças i3200 | 1800mm | 2p=90m²/h 3p=64 4p=50 6p=34
DG1904i | 4 cabeças i3200 | 1900mm | 2p=145m²/h 3p=118 4p=87
DG1908i | 8 cabeças i3200 | 1850mm | 1p=334m²/h 2p=238
DG3202i | 2 cabeças i3200 | 3200mm | 3p=64m²/h 4p=50 6p=34

DTF TÊXTIL:
DG3002i | 2 cabeças i1600 | 300mm | 6p=8m/h 8p=4m/h
DG6002i | 2 cabeças i3200 | 600mm | 6p=15m/h 8p=9,5m/h

DTF UV:
DG3003i | 3 cabeças i1600 | 300mm | CMYK+Branco+Verniz | 8p=3,5m²/h
DG6004i | 3 cabeças i3200 | 600mm | CMYK+Branco+Verniz | 6p=6m²/h 8p=8m²/h

UV PLANA:
AJ6090i | 3 cabeças i1600 | até 1200mm | CMYK+Branco+Verniz | 4p=6m²/h

LASER:
DG1080 | CO2 100W | 1600x1000mm
HQ1810 | Laser Têxtil | 1800x1000mm
Laser Fiber 20/30/50W | 200x200mm | metais

PLOTTER DE RECORTE:
DG1351 | 1300mm mídia | 1220mm corte | até 800mm/s

TINTAS:
DGeco Premium: Eco solvente CMYK | 2 anos externo
DGtex DTF: Têxtil CMYK | algodão e poliéster
DGtex Premium: Sublimática CMYK | poliéster, uniformes
DGUV: UV CMYK+Branco+Verniz | acrílico, madeira, brindes | Escudos ou Patch com Relevo 3D para uniforme

REGRA DE TINTAS, NUNCA PULE:
Antes de CNPJ ou fechamento, apresente a tinta do equipamento discutido.
Eco → DGeco | Sublimática → DGtex Premium | DTF → DGtex DTF | UV → DGUV
"Tinta de segunda linha pode custar menos o litro, mas o cabeçote que ela dana custa 10x mais."

REGRA DE ESTOQUE REAL, USE SEMPRE QUE DISPONÍVEL:
Se você receber um bloco "[SUPRIMENTOS]" ou "[SUPRIMENTOS - FAMILIA DE PRODUTOS]" no contexto da mensagem,
ele contém a quantidade REAL em estoque consultada agora no sistema. USE esse número diretamente na resposta.
NUNCA diga "preciso acionar o time" ou "não tenho acesso ao estoque" se esse bloco estiver presente.
Se vier uma FAMÍLIA (várias cores), liste as quantidades de cada cor e pergunte qual o cliente precisa.
Se o cliente não especificou a cor e você recebeu várias opções, pergunte qual cor antes de fechar.

REGRA, "KIT" E "CMYK" SÃO CONCEITOS, NÃO NOMES DE PRODUTO:
Quando o cliente disser "kit", "CMYK", "uma de cada" ou "kit completo" referindo-se a tinta,
ele quer dizer: 1 unidade de cada cor (Cyan, Magenta, Yellow, Black) da linha que está sendo discutida.
NÃO existe um produto chamado "Kit" na tabela, é a combinação das 4 cores.
NUNCA invente números de estoque (litros, unidades) para "kit" sem ter recebido um bloco [SUPRIMENTOS] real.
Se o cliente pedir "kit" ou "CMYK" e você NÃO tiver dados de estoque no contexto, pergunte:
"Você quer o kit CMYK de qual linha, DGtex Premium, DGeco, ou outra?" antes de falar de estoque.
Só fale de quantidade/estoque depois de ter o bloco [SUPRIMENTOS] real no contexto.

REGRA, NUNCA REPITA A MESMA TRAVA DUAS VEZES:
Se você já pediu e-mail e telefone na mensagem anterior e o cliente respondeu outra coisa (sem dar email/telefone),
NÃO repita a mesma frase de novo. Avance a conversa: responda a pergunta nova do cliente primeiro.
Pedir e-mail e telefone repetidamente sem avançar é proibido, isso quebra a conversa.

REGRAS DE CRÉDITO:
- Simples Nacional, LTDA, SA: APROVADO para boleto
- MEI: análise personalizada pelo financeiro
- Pessoa Física: cartão ou à vista
- NUNCA mencione restrições, score ou negativos ao cliente

FLUXO PROPOSTA:
1. "Tem CNPJ?" → sistema consulta
2. PF → "Trabalhamos com cartão ou à vista"
3. MEI → "Nossa equipe faz análise personalizada"
4. Aprovado → encerre com mensagem de encaminhamento

BASE DE CONHECIMENTO:
{KNOWLEDGE_BASE_TEXT}
FIM DA BASE DE CONHECIMENTO
"""

# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------
client = (
    AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    if getattr(settings, "ANTHROPIC_API_KEY", "stub") != "stub"
    else None
)

openai_client = (
    OpenAI(api_key=settings.OPENAI_API_KEY)
    if getattr(settings, "OPENAI_API_KEY", "stub") != "stub"
    else None
)


def split_text(text: str) -> list[str]:
    lines = [l for l in text.split('\n') if l.strip()]
    chunks = []
    for i in range(0, len(lines), 3):
        chunk = '\n'.join(lines[i:i+3])
        if chunk.strip():
            chunks.append(chunk)
    chunks = chunks[:2] if chunks else [text]
    return chunks

def get_typing_delay(text: str) -> float:
    delay = len(text) / 15
    return max(1.0, min(4.0, delay))

async def transcribe_audio(audio_url: str) -> str:
    if not openai_client:
        return ""
    import requests, tempfile
    try:
        twilio_sid   = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        twilio_token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        auth = (twilio_sid, twilio_token) if twilio_sid and twilio_token else None
        response = await asyncio.wait_for(
            asyncio.to_thread(requests.get, audio_url, auth=auth, timeout=15), timeout=18.0
        )
        if response.status_code != 200:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name
        def _sync_transcribe(path):
            with open(path, "rb") as f:
                return openai_client.audio.transcriptions.create(
                    model="whisper-1", file=f, response_format="verbose_json"
                )
        transcript = await asyncio.wait_for(asyncio.to_thread(_sync_transcribe, tmp_path), timeout=30.0)
        os.remove(tmp_path)

        duracao_segundos = getattr(transcript, "duration", 0) or 0
        registrar_uso_whisper(agente="bruno", segundos_audio=duracao_segundos)

        return transcript.text
    except Exception as e:
        logger.error(f"Erro na transcrição: {e}")
        return ""

async def create_thread() -> str:
    return str(uuid.uuid4())

# ── Trava por conversa ────────────────────────────────────────────────
# Sem isso, duas mensagens do MESMO telefone chegando quase juntas (ex:
# CNPJ e email em sequencia rapida) eram processadas em paralelo, cada
# uma com sua propria sessao de banco escrevendo no mesmo lead_state ao
# mesmo tempo. Resultado observado em producao: resposta calculada com
# dado desatualizado (pediu email de novo mesmo ja tendo recebido) e,
# em casos de conflito de escrita, a mensagem seguinte caia no erro
# generico repetidamente. Agora mensagens do mesmo thread_id (=mesmo
# telefone) sao processadas em fila, uma de cada vez, na ordem que
# chegaram -- outros telefones continuam em paralelo normalmente.
_locks_por_conversa: dict = {}


def _lock_da_conversa(thread_id: str) -> asyncio.Lock:
    lock = _locks_por_conversa.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks_por_conversa[thread_id] = lock
    return lock


async def process_message_with_assistant(thread_id: str, user_message: str) -> list:
    async with _lock_da_conversa(thread_id):
        return await _process_message_with_assistant_impl(thread_id, user_message)


async def _process_message_with_assistant_impl(thread_id: str, user_message: str) -> list:
    if not client or getattr(settings, "ANTHROPIC_API_KEY", "stub") == "stub":
        return ["[STUB MODE] Anthropic Key ausente."]

    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.thread_id == thread_id).first()
        if not lead:
            return ["Lead nao encontrado."]

        phone = lead.phone

        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if not lead_state:
            # FIX: lead_state.telefone comecava vazio e so era preenchido se
            # o cliente digitasse um numero na conversa -- ignorando que
            # 'phone' (o numero do WhatsApp de onde a mensagem chegou) ja
            # e o telefone de contato de verdade. Resultado: Bruno pedia
            # telefone pro cliente mesmo already estando na conversa com
            # ele. Agora usa o proprio numero do WhatsApp como telefone
            # padrao -- so pede de novo se um dia isso vier vazio por algum
            # motivo (nao deveria acontecer, mas evita quebrar o fluxo).
            lead_state = LeadState(phone=phone, stage="active", telefone=phone)
            db.add(lead_state)
            db.commit()
            db.refresh(lead_state)
        elif not lead_state.telefone:
            lead_state.telefone = phone
            db.commit()

        historico_count = db.query(Conversation).filter(
            Conversation.phone == phone, Conversation.role == "user"
        ).count()

        campanha_ativa = None
        if historico_count == 0:
            campanha_ativa = detectar_campanha(user_message)
            if campanha_ativa and campanha_ativa.get("_codigo"):
                db.add(Conversation(phone=phone, role="user",
                    content=f"[CAMPANHA: {campanha_ativa['nome']} | {campanha_ativa.get('_codigo','')}]"))
                db.commit()
        else:
            campanha_msg = db.query(Conversation).filter(
                Conversation.phone == phone, Conversation.content.like("[CAMPANHA:%")
            ).first()
            if campanha_msg:
                import re as _re_camp
                m = _re_camp.search(r'\[CAMPANHA:.*?\|\s*(\w+)\]', campanha_msg.content)
                if m:
                    from app.services.campaigns import CAMPANHAS
                    campanha_ativa = CAMPANHAS.get(m.group(1))

        if lead_state.stage == "closed":
            agradecimentos = ["obrigado","obrigada","valeu","ok","ótimo","perfeito","entendido","certo","👍","blz"]
            if any(ag in user_message.lower().strip() for ag in agradecimentos):
                return []
            return ["Seu atendimento ja foi encaminhado para nosso time comercial. Qualquer duvida nova e so chamar!"]

        if lead_state.stage == "active":
            possible_cnpj = _clean_cnpj(user_message)
            if len(possible_cnpj) == 14:
                lead_state.stage = "awaiting_cnpj"
                db.commit()

        if lead_state.stage == "awaiting_cnpj":
            cnpj_clean = _clean_cnpj(user_message)
            PALAVRAS_NAO_CNPJ = [
                "tinta", "valor", "preco", "quanto", "como", "qual", "garantia",
                "frete", "entrega", "instalacao", "suporte", "funciona", "serve",
                "posso", "consigo", "quero", "preciso", "nao tenho", "nao sei",
                "depois", "amanha", "semana", "pergunta", "duvida", "custo",
                "maquina", "equipamento",
            ]
            e_pergunta = any(kw in user_message.lower().strip() for kw in PALAVRAS_NAO_CNPJ)

            if len(cnpj_clean) == 14 and not e_pergunta:
                import json
                cnpj_data = await serasa_consultar(cnpj_clean, phone=phone, cidade=lead.city or "")
                lead_state.cnpj = cnpj_clean
                lead_state.stage = "cnpj_received"

                if "error" in cnpj_data:
                    err = cnpj_data["error"]
                    lead_state.cnpj_data = json.dumps(cnpj_data, ensure_ascii=False)
                    db.commit()
                    if err == "cnpj_invalido":
                        db.add(Conversation(phone=phone, role="user", content=user_message))
                        db.commit()
                        return ["Esse CNPJ parece invalido. Pode me confirmar os 14 digitos?"]
                    elif err == "cnpj_nao_encontrado":
                        if lead_state.email and lead_state.telefone:
                            pedido_dados_nf = "EMAIL e TELEFONE ja estao registrados -- NAO peca de novo. Encerre agradecendo e avisando que o time comercial vai entrar em contato."
                        elif lead_state.email:
                            pedido_dados_nf = "EMAIL ja informado, NAO pergunte de novo. Pergunte so o TELEFONE."
                        elif lead_state.telefone:
                            pedido_dados_nf = "TELEFONE ja informado, NAO pergunte de novo. Pergunte so o EMAIL."
                        else:
                            pedido_dados_nf = "Qual seu e-mail e telefone?"
                        cnpj_context = (
                            f"[SISTEMA: CNPJ {cnpj_clean} NAO ENCONTRADO na Serasa.]\n"
                            f"INSTRUCAO: Diga 'Vou encaminhar para nosso time analisar as melhores condicoes.' {pedido_dados_nf}\n"
                        )
                    else:
                        lead_state.cnpj_data = json.dumps({"error": err}, ensure_ascii=False)
                        db.commit()
                        cnpj_context = (
                            f"[SISTEMA: CNPJ {cnpj_clean} nao encontrado. Pode ser CPF ou MEI.]\n"
                            "INSTRUCAO: Descubra se e pessoa fisica ou MEI e encaminhe adequadamente.\n"
                        )
                else:
                    regime    = get_regime_serasa(cnpj_data)
                    score_s   = get_score(cnpj_data)
                    negativos = tem_negativos(cnpj_data)
                    ativo     = is_cnpj_ativo(cnpj_data)
                    socios_r  = get_socios_com_restricao(cnpj_data)
                    tempo_emp = calcular_tempo_empresa(cnpj_data)
                    capital_s = get_capital_social(cnpj_data)
                    prob_inad = get_probabilidade_inadimplencia(cnpj_data)
                    consultas = get_consultas_mercado(cnpj_data)
                    veredito  = avaliar_risco_negocio(cnpj_data)
                    lead_state.cnpj_data = json.dumps(cnpj_data, ensure_ascii=False)
                    db.commit()

                    # ANTES: cada branch abaixo tinha 'Pergunte EMAIL e
                    # TELEFONE.' fixo no texto, sem checar se esses dados
                    # ja estavam salvos em lead_state -- por isso o Bruno
                    # pedia de novo mesmo ja tendo os dois, todo santa
                    # vez que o CNPJ era processado.
                    falta_email = not lead_state.email
                    falta_tel = not lead_state.telefone
                    if falta_email and falta_tel:
                        pedido_dados = "Pergunte EMAIL e TELEFONE."
                    elif falta_email:
                        pedido_dados = "TELEFONE ja informado, NAO pergunte de novo. Pergunte so o EMAIL."
                    elif falta_tel:
                        pedido_dados = "EMAIL ja informado, NAO pergunte de novo. Pergunte so o TELEFONE."
                    else:
                        pedido_dados = "EMAIL e TELEFONE ja estao registrados -- NAO peca de novo em hipotese nenhuma. Encerre agradecendo e avisando que o time comercial vai entrar em contato."

                    # ANTES: so olhava score<300 + negativos (bool) pra
                    # decidir tudo. Agora usa avaliar_risco_negocio(), que
                    # pesa TODOS os fatores juntos (protestos, acoes
                    # judiciais, socio com restricao, volume de consultas
                    # recentes, tempo de empresa, etc) e ja devolve a
                    # recomendacao de condicao de pagamento pronta.
                    if regime == "MEI":
                        parecer = "MEI"
                        instrucao = "Diga 'Para MEI nossa equipe faz analise personalizada. Vou encaminhar seus dados.'"
                    elif veredito["nivel"] == "bloqueado":
                        parecer = veredito["motivo"]
                        instrucao = "Diga 'Vou encaminhar para nosso time verificar.' Nao mencione o motivo."
                    elif veredito["nivel"] == "risco_alto":
                        parecer = f"RISCO ALTO — {veredito['motivo']}"
                        instrucao = f"NAO mencione restricoes. Diga 'Vou encaminhar para nosso time analisar as melhores condicoes.' {pedido_dados}"
                    elif veredito["nivel"] == "aprovado_com_cautela":
                        parecer = f"APROVADO COM CAUTELA — {veredito['recomendacao']}"
                        instrucao = f"NAO mencione restricoes. Diga 'Posso seguir com parcelamento, nosso consultor confirma as condicoes.' {pedido_dados}"
                    else:
                        parecer = f"APROVADO — {veredito['recomendacao']}"
                        instrucao = f"APROVADO. Diga 'Posso seguir com parcelamento no boleto. Nosso consultor monta a proposta.' {pedido_dados}"

                    cnpj_context = (
                        "[SISTEMA: Consulta Serasa realizada]\n"
                        f"Regime: {regime} | Score: {score_s}/1000 | Ativo: {'Sim' if ativo else 'NAO'}\n"
                        f"Negativos: {'SIM' if negativos else 'NAO'} | Tempo empresa: {tempo_emp}\n"
                        f"PARECER: {parecer}\n\n"
                        "NUNCA diga ao cliente que foi reprovado ou tem restricoes.\n"
                        f"{instrucao}\n"
                        "Se ja tem email e telefone: encerre com mensagem de encaminhamento.\n"
                    )


                db.add(Conversation(phone=phone, role="user", content=user_message))
                db.add(Conversation(phone=phone, role="user", content=cnpj_context))
                db.commit()

        db.add(Conversation(phone=phone, role="user", content=user_message))
        db.commit()

        import re as _re2
        _qualificacao_mudou = False
        email_match = _re2.search(r'[\w.+-]+@[\w-]+\.[\w.]+', user_message)
        if email_match:
            novo_email = email_match.group()
            EMAILS_DOSS = ["dossgroup.com.br", "doss.com.br", "dgtex.com.br"]
            if not any(d in novo_email.lower() for d in EMAILS_DOSS):
                lead_state.email = novo_email
                db.commit()
                _qualificacao_mudou = True

        if not lead.city:
            import re as _re_cidade
            cidade_match = _re_cidade.search(
                r'(?:de|em|sou de|moro em|estou em|aqui em)\s+([A-Za-záàâãéèêíïóôõöúçñÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ][a-záàâãéèêíïóôõöúçñ]+(?:\s+[A-Za-záàâãéèêíïóôõöúçñ]+)?)',
                user_message, _re_cidade.IGNORECASE
            )
            if cidade_match:
                cidade_detectada = cidade_match.group(1).strip()
                if len(cidade_detectada) > 3:
                    lead.city = cidade_detectada
                    db.commit()
                    _qualificacao_mudou = True

        if not lead.name or lead.name == phone:
            import re as _re_nome
            # Heuristica leve, mesmo espirito da extracao de cidade acima --
            # nao e perfeita, mas cobre os jeitos mais comuns de alguem se
            # apresentar. Sem isso, lead.name nunca era preenchido durante
            # a conversa (so lido, nunca escrito), o que quebrava qualquer
            # logica que dependesse de "ja sabemos o nome do cliente".
            nome_match = _re_nome.search(
                r'(?:me chamo|meu nome é|sou o|sou a|aqui é o|aqui é a|pode me chamar de|é o|é a)\s+'
                r'([A-Za-záàâãéèêíïóôõöúçñÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ][a-záàâãéèêíïóôõöúçñ]+(?:\s+[A-Za-záàâãéèêíïóôõöúçñ]+){0,2})',
                user_message, _re_nome.IGNORECASE
            )
            if nome_match:
                nome_detectado = nome_match.group(1).strip()
                if 2 < len(nome_detectado) < 60:
                    lead.name = nome_detectado
                    db.commit()
                    _qualificacao_mudou = True

        if not lead_state.produto_interesse:
            _PALAVRAS_PRODUTO = {
                "dgtex": "Tinta DGtex (Sublimação)", "sublim": "Tinta DGtex (Sublimação)",
                "dgeco": "Tinta DGeco (Eco-Solvente)", "eco solvente": "Tinta DGeco (Eco-Solvente)",
                "dtf": "Equipamento/Tinta DTF", "uv flex": "Tinta UV Flex",
                "conversão": "Conversão de equipamento Epson", "converter": "Conversão de equipamento Epson",
                "f6370": "Conversão Epson F6370", "f6200": "Conversão Epson F6200",
                "f6070": "Conversão Epson F6070", "f9470": "Conversão Epson F9470",
            }
            for _termo, _rotulo in _PALAVRAS_PRODUTO.items():
                if _termo in user_message.lower():
                    lead_state.produto_interesse = _rotulo
                    db.commit()
                    _qualificacao_mudou = True
                    break

        # FIX (arquitetura pedida 04/08): antes o CRM so recebia dado de
        # qualificacao em 3 momentos (primeiro contato, handoff, fechamento)
        # -- se o cliente informasse cidade/nome/produto no meio da
        # conversa, isso ficava preso no banco do Bruno, sem refletir no
        # card do CRM ate um desses 3 momentos acontecer (as vezes nunca).
        # Agora, toda vez que algum dado novo e capturado, sincroniza na
        # hora (fire-and-forget, nao atrasa a resposta ao cliente).
        if _qualificacao_mudou:
            try:
                from app.services.crm_inbox_client import criar_lead_no_pipeline as _sync_incremental
                asyncio.create_task(_sync_incremental(
                    phone,
                    nome=lead.name if (lead.name and lead.name != phone) else None,
                    cidade=lead.city or None,
                    email=lead_state.email or None,
                    resumo=(
                        f"Dado novo capturado durante a conversa"
                        + (f" -- produto de interesse: {lead_state.produto_interesse}" if lead_state.produto_interesse else "")
                        + "."
                    ),
                    finalizado=False,
                ))
                lead_state.ultima_sync_crm = datetime.utcnow()
                db.commit()
            except Exception as e:
                logger.error(f"[SYNC INCREMENTAL] Falha ao atualizar CRM em tempo real ({phone}): {e}")


        # ANTES: 'and lead_state.stage not in ("awaiting_cnpj", "cnpj_received")'
        # bloqueava a extracao de telefone justamente durante e depois do
        # fluxo de CNPJ -- que e o momento exato em que o Bruno pede
        # "email e telefone" (ver instrucoes nas linhas ~625-634). Resultado:
        # o cliente respondia, o Bruno dizia "anotado", mas o campo nunca
        # era salvo de verdade, e no proximo turno o Bruno via o campo
        # vazio de novo e voltava a pedir -- loop sem fim, card nunca
        # chegava a ser criado no CRM.
        if not lead_state.telefone:
            msg_clean = _re2.sub(r'\s', ' ', user_message)
            tel_match = _re2.search(r'(?:(?:\(?\d{2}\)?)[\s.-]?)(?:9[\s.-]?)?\d{4}[\s.-]?\d{4}(?!\d)', msg_clean)
            if tel_match:
                tel_raw = _re2.sub(r'[^\d]', '', tel_match.group())
                if 10 <= len(tel_raw) <= 11:
                    lead_state.telefone = tel_match.group().strip()
                    db.commit()

        # ── Histórico — 40 mensagens ───────────────────────────────────────
        raw_history = (
            db.query(Conversation)
            .filter(Conversation.phone == phone)
            .order_by(Conversation.created_at.asc())
            .limit(40)
            .all()
        )

        messages = []
        for msg in raw_history:
            if not messages or messages[-1]["role"] != msg.role:
                messages.append({"role": msg.role, "content": msg.content})
            else:
                messages[-1]["content"] += f"\n\n{msg.content}"

        if not messages:
            messages = [{"role": "user", "content": user_message}]
        else:
            if messages[0]["role"] != "user":
                messages.insert(0, {"role": "user", "content": "..."})
            if messages[-1]["role"] != "user":
                messages.append({"role": "user", "content": user_message})

        customer_data = None
        debt_info = ""
        stock_info = ""

        try:
            customer_data = await asyncio.wait_for(uniplus_service.get_customer_by_phone(phone), timeout=5.0) if UNIPLUS_ATIVO else None
        except asyncio.TimeoutError:
            logger.warning("Timeout Uniplus customer")

        if customer_data:
            try:
                pending = await asyncio.wait_for(uniplus_service.list_receivables(days_offset=1), timeout=5.0)
                customer_debt = [r for r in pending if r.get("contato", {}).get("id") == customer_data.get("id")]
                if customer_debt:
                    debt_info = f"\n[FINANCEIRO] Cliente {customer_data.get('nome')} possui faturas vencidas."
            except asyncio.TimeoutError:
                logger.warning("Timeout Uniplus receivables")

        user_lower = user_message.lower()

        if any(k in user_lower for k in ["plotter", "maquina", "hs", "dg", "jinka", "dtf", "uv"]):
            try:
                machines = await asyncio.wait_for(sheets_service.get_machines(), timeout=5.0)
                if machines:
                    stock_info += "\n[MÁQUINAS - ESTOQUE REAL]:\n"
                    for m in machines:
                        modelo = m.get('EQUIPAMENTOS A VENDA') or m.get('MODELO')
                        status = m.get('STATUS', 'NOVO')
                        preco = m.get('PREÇO SUJERIDO') or m.get('PRECO SUJERIDO', 'Sob Consulta')
                        condicao = m.get('CONDIÇÕES') or m.get('CONDICOES', 'A combinar')
                        if modelo:
                            stock_info += f"- {modelo} ({status}) | Preço: {preco} | Condição: {condicao}\n"
            except asyncio.TimeoutError:
                logger.warning("Timeout Google Sheets")

        # ── Gatilho de estoque real — só dispara com pedido EXPLÍCITO de
        # disponibilidade/quantidade, nunca apenas por nome de produto
        # (evita que "quero um kit" ou "uma de cada" acione consulta indevida) ──
        PALAVRAS_ESTOQUE_EXPLICITO = [
            "estoque", "tem em estoque", "quanto tem", "quantos litros",
            "quantas unidades", "disponivel", "disponível", "tem disponivel",
            "quanto tem disponivel", "tem ai", "tem aí",
        ]
        pede_estoque_explicito = any(k in user_lower for k in PALAVRAS_ESTOQUE_EXPLICITO)

        if pede_estoque_explicito:
            # Busca SEMPRE na mensagem atual do cliente, nunca complementa
            # com a ultima resposta do Bruno — isso evita reativar produtos
            # ja mencionados anteriormente na conversa por engano.
            codigo_encontrado = None
            try:
                codigo_encontrado = await asyncio.wait_for(
                    sheets_service.find_codigo_by_phrase(user_message), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout busca codigo mensagem atual")

            if codigo_encontrado:
                try:
                    data = await asyncio.wait_for(
                        uniplus_service.get_stock_and_price(codigo_encontrado), timeout=5.0
                    ) if UNIPLUS_ATIVO else None
                    if data:
                        disponivel = "disponivel" if data['estoque'] > 0 else "SEM ESTOQUE"
                        stock_info += f"\n[SUPRIMENTOS]: {data['nome']} (cod {codigo_encontrado}) | Saldo: {data['estoque']} un | {disponivel}\n"
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout estoque cod '{codigo_encontrado}'")
            else:
                # Sem match unico — pode ser ambiguidade (ex: cor nao especificada)
                # Busca a familia inteira SOMENTE com base na mensagem atual
                try:
                    familia = await asyncio.wait_for(
                        sheets_service.find_familia_by_phrase(user_message), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    familia = []

                if familia:
                    stock_info += "\n[SUPRIMENTOS - FAMILIA DE PRODUTOS]:\n"
                    for item in familia:
                        try:
                            data = await asyncio.wait_for(
                                uniplus_service.get_stock_and_price(item["codigo"]), timeout=5.0
                            ) if UNIPLUS_ATIVO else None
                            if data:
                                disponivel = "disponivel" if data['estoque'] > 0 else "SEM ESTOQUE"
                                stock_info += f"  - {data['nome']} (cod {item['codigo']}) | Saldo: {data['estoque']} un | {disponivel}\n"
                        except asyncio.TimeoutError:
                            logger.warning(f"Timeout estoque familia cod '{item['codigo']}'")
                else:
                    for qw in [w for w in user_message.split() if len(w) > 3][:2]:
                        try:
                            data = await asyncio.wait_for(
                                uniplus_service.get_stock_and_price(qw), timeout=5.0
                            ) if UNIPLUS_ATIVO else None
                            if data:
                                stock_info += f"\n[SUPRIMENTOS]: {data['nome']} | Saldo: {data['estoque']}\n"
                        except asyncio.TimeoutError:
                            logger.warning(f"Timeout estoque '{qw}'")

        # ── Busca web para máquinas concorrentes (custo controlado) ──────
        # Só dispara quando detecta marca concorrente + pergunta técnica
        info_concorrente = ""
        try:
            marca_detectada = precisa_buscar_concorrente(user_message, conv_cliente if 'conv_cliente' in dir() else "")
        except Exception:
            marca_detectada = None
        if marca_detectada:
            try:
                info_concorrente = await asyncio.wait_for(
                    buscar_info_concorrente(client, marca_detectada, user_message), timeout=12.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout busca concorrente '{marca_detectada}'")
            except Exception as e:
                logger.error(f"Erro busca concorrente: {e}")

            # FIX: se o Bruno so DIZER "vou confirmar com a tecnica" sem
            # nada por tras, e uma promessa vazia (mesmo erro que ja
            # perdeu lead antes -- "estou verificando" que nunca teve
            # retorno). Quando a duvida e sobre COMPATIBILIDADE de
            # maquina de outra marca (nao so info generica), aciona a
            # transferencia de verdade pra um agente humano agora,
            # nao so promete no texto.
            _termos_compat = ["compat", "aceita", "serve", "bag", "cartucho", "chip", "alimenta"]
            if any(t in user_message.lower() for t in _termos_compat):
                # So aciona a transferencia de verdade se ja tivermos pelo
                # menos o nome do cliente -- sem isso, o card chegaria pro
                # agente humano tao vazio quanto uma promessa nao cumprida.
                # Se ainda nao sabemos o nome, o Bruno primeiro pergunta
                # (regra de qualificacao ja existente) antes de escalar --
                # e a flag so vira True quando o handoff acontece de fato,
                # senao ele diria "ja encaminhei" sem ter encaminhado nada.
                if lead and lead.name:
                    system_dinamico_compat_flag = True
                    try:
                        from app.services.followup_service import _transferir_para_agente
                        resumo_compat = (
                            f"Cliente perguntou sobre compatibilidade de maquina {marca_detectada.upper()} "
                            f"com nossa tinta. Pergunta exata do cliente: \"{user_message[:300]}\". "
                            f"Precisa confirmar sistema de alimentacao (bag/cartucho/chip) e compatibilidade real."
                        )
                        asyncio.create_task(_transferir_para_agente(phone, resumo=resumo_compat))
                    except Exception as e:
                        logger.error(f"Falha ao acionar handoff de compatibilidade: {e}")
                        system_dinamico_compat_flag = False
                else:
                    system_dinamico_compat_flag = False
            else:
                system_dinamico_compat_flag = False
        else:
            system_dinamico_compat_flag = False

        # ── Decide modelo com historico_count ─────────────────────────────
        model = choose_model(user_message, historico_count)

        # ── Tabela de preços só para Sonnet ───────────────────────────────
        if model == MODEL_SONNET:
            try:
                tabela_precos_dinamica = await asyncio.wait_for(
                    sheets_service.build_tabela_precos(), timeout=8.0
                )
            except Exception:
                tabela_precos_dinamica = "[TABELA INDISPONIVEL]"
        else:
            tabela_precos_dinamica = ""

        # ── System prompt por modelo ──────────────────────────────────────
        if model == MODEL_HAIKU:
            system_estatico = SYSTEM_PROMPT_HAIKU
        else:
            system_estatico = SYSTEM_PROMPT_BASE.format(
                tabela_precos_dinamica=tabela_precos_dinamica,
                KNOWLEDGE_BASE_TEXT=KNOWLEDGE_BASE_TEXT,
                DNA_SALES_TEXT=DNA_SALES_TEXT
            )

        # ── Parte dinâmica (não cacheável) ────────────────────────────────
        system_dinamico = ""
        if customer_data:
            system_dinamico += f"\n\nCLIENTE IDENTIFICADO: {customer_data.get('nome')}."
        if debt_info:
            system_dinamico += debt_info
        if stock_info:
            system_dinamico += f"\n\nESTOQUE ATUAL:\n{stock_info}"
        if campanha_ativa and get_contexto_campanha(campanha_ativa):
            system_dinamico += f"\n\n{get_contexto_campanha(campanha_ativa)}"
        if info_concorrente:
            system_dinamico += f"\n\n[INFO CONCORRENTE - {marca_detectada.upper()}]: {info_concorrente}"
        if system_dinamico_compat_flag:
            system_dinamico += (
                "\n\n[HANDOFF ACIONADO]: A pergunta e sobre compatibilidade de maquina de "
                "outra marca com nossa tinta. A transferencia pra um agente tecnico humano "
                "JA FOI acionada de verdade agora. Diga ao cliente que um especialista "
                "tecnico vai confirmar isso com ele em breve -- NAO diga 'vou verificar' "
                "de forma vaga, diga que ja encaminhou e alguem vai falar com ele."
            )

        # Memoria que a propria IA supervisora do CRM ja apurou dessa
        # conversa (fatos, produtos, objecoes, promessas, proximos passos,
        # preferencias). Ate 02/08/2026 isso so alimentava o painel do CRM
        # pra humano ler -- o Bruno nunca usava o que ja tinha sido
        # apurado sobre o proprio cliente. Agora usa, pra negociar com
        # base no que ja foi combinado/prometido em vez de reconstruir
        # tudo do zero a cada mensagem.
        try:
            memoria_ia = await asyncio.wait_for(buscar_memoria_ia(phone), timeout=4.0)
        except Exception:
            memoria_ia = None
        if memoria_ia:
            mem_data = memoria_ia.get("memory") if isinstance(memoria_ia.get("memory"), dict) else {}
            partes_memoria = []
            if memoria_ia.get("recommended_action"):
                partes_memoria.append(f"Situacao atual: {memoria_ia['recommended_action']}")
            for chave, rotulo in [
                ("facts", "Fatos ja levantados"), ("products", "Produtos de interesse"),
                ("objections", "Objecoes ja levantadas"), ("promises", "Promessas ja feitas"),
                ("next_steps", "Proximos passos combinados"), ("preferences", "Preferencias do cliente"),
            ]:
                itens = mem_data.get(chave)
                if isinstance(itens, list) and itens:
                    partes_memoria.append(f"{rotulo}: {'; '.join(str(i) for i in itens[:6])}")
            if partes_memoria:
                system_dinamico += (
                    "\n\n[MEMORIA DA CONVERSA - ja apurado anteriormente, NAO pergunte de novo o que ja "
                    "esta aqui, use pra negociar com precisao]:\n" + "\n".join(partes_memoria)
                )

        # ── Cache na parte estática ────────────────────────────────────────
        system_parts = [
            {
                "type": "text",
                "text": system_estatico,
                "cache_control": {"type": "ephemeral"}
            }
        ]
        if system_dinamico.strip():
            system_parts.append({"type": "text", "text": system_dinamico})

        # ── Alerta de saldo insuficiente na API da Anthropic ────────────────
        # Sem isso, quando o credito acaba a excecao sobe pro handler
        # generico, o cliente recebe uma resposta ruim (ou nenhuma) e
        # ninguem fica sabendo -- o lead se perde caladinho. Cooldown de
        # 15 min pra nao floodar o WhatsApp se varios leads baterem o
        # mesmo erro ao mesmo tempo.
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=1024,
                    temperature=0.4,
                    system=system_parts,
                    messages=messages,
                ),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            logger.error("Timeout Claude API")
            return ["Pode repetir? Tive uma lentidão aqui."]
        except Exception as api_error:
            erro_texto = str(api_error).lower()
            if "insufficient_balance" in erro_texto or "credit balance is too low" in erro_texto:
                await _alertar_admin_saldo_insuficiente(api_error)
                return ["Já te retorno, só um instante."]
            raise

        if not response.content:
            return ["Pode repetir?"]

        registrar_uso_anthropic(model, response.usage, agente="bruno")

        reply_text = response.content[0].text.strip()
        db.add(Conversation(phone=phone, role="assistant", content=reply_text))
        db.commit()

        reply_lower = reply_text.lower()

        # ── Detecção de despedida expandida para o Arcca ─────────────────
        despedida_detectada = any(kw in reply_lower for kw in [
            "passei seus dados para nosso time comercial",
            "passei tudo para nosso time comercial",
            "consultor entra em contato com a proposta",
            "foi um prazer, qualquer duvida e so chamar",
            "vou encaminhar para nosso time",
            "encaminhei seus dados",
            "nosso consultor vai entrar em contato",
            "time comercial vai entrar em contato",
            "consultor entra em contato",
            "em breve um consultor",
            "passei para nosso time",
            "em breve entraremos em contato",
        ])

        # ── Recusa explícita do cliente (diferente de silêncio) ───────────
        # A lista de despedida acima só pega a frase forte de qualificação
        # completa ("passei seus dados pro time comercial"). Ela nunca
        # disparava quando o cliente respondia um "não, obrigado" claro ou
        # dizia que o assunto não era o que procurava -- o lead ficava
        # "active"/"awaiting_cnpj" pra sempre e continuava recebendo
        # follow-up mesmo depois de uma recusa explícita. "Nunca desistir"
        # vale pra quem fica em silêncio, não pra quem já respondeu que
        # não quer -- insistir nesse caso é ignorar a resposta da pessoa.
        recusa_explicita = any(kw in user_lower for kw in [
            "no momento não", "por enquanto não", "agora não",
            "não quero", "não preciso", "não tenho interesse",
            "não é isso", "não é o que eu", "não vou precisar",
            "obrigado, não", "obrigada, não", "não, obrigado", "não, obrigada",
            "não funciona", "não é pra mim",
        ])

        if recusa_explicita and not despedida_detectada and lead_state.stage not in ("closed",):
            lead_state.recusas_count = (lead_state.recusas_count or 0) + 1
            db.commit()
            if lead_state.recusas_count >= 2:
                # Segunda recusa -- insistir mais uma vez ja seria
                # forçar a barra. Transfere pra agente humano de verdade
                # (mesma funcao usada no handoff por silencio), em vez
                # de so fechar sem ninguem acompanhar.
                from app.services.followup_service import _transferir_para_agente
                await _transferir_para_agente(phone)
                lead_state.stage = "closed"
                db.commit()
            # Na 1a recusa nao faz nada aqui -- o Bruno responde
            # normalmente, com mais argumentacao, seguindo o fluxo
            # natural da conversa (o prompt principal ja orienta a
            # contornar objecao antes de desistir).

        if despedida_detectada and lead_state.stage not in ("closed",):
            # card_id: 1 = card criado mas RETIDO (sem dono), 2 = ja entregue
            # a um vendedor. Antes a condicao era "not lead_state.card_id",
            # entao qualquer card ja criado (handoff fraco / qualificacao)
            # impedia a entrega no fechamento -- o lead ficava retido para
            # sempre, sem dono, e ninguem trabalhava. Agora so nao repete
            # quando ja foi entregue de verdade. O endpoint do CRM cuida do
            # resto: se o card existe e chega finalizado=true, ele atribui o
            # vendedor do rodizio e notifica.
            if lead_state.card_id != 2:
                PALAVRAS_NAO_NOME = {
                    "tudo","ok","oi","ola","opa","sim","nao","pode","certo","otimo","legal",
                    "beleza","entendi","show","obrigado","obrigada","claro","bom","boa",
                    "perfeito","entendido","combinado","fechado","feito","pronto","blz","vlw",
                    "valeu","quero","tenho","procuro","busco","preciso","gostaria","vim",
                    "achei","vi","estou","isso","esse","essa","qual","como","quando","onde",
                    "quanto","que","sou","meu","minha","olha","olhe","hey","ei",
                    "sublimacao","sublimação","ecosolvente","eco","dtf","tinta","papel",
                    "plotter","maquina","impressora","ciano","cyan","magenta","amarelo",
                    "preto","tem","voce","trabalha","estoque","valor","codigo","preco",
                }
                nome_salvo = lead.name or ""
                nome_lead = ""
                if nome_salvo and nome_salvo != phone and nome_salvo.lower() not in PALAVRAS_NAO_NOME and len(nome_salvo) > 2:
                    nome_lead = nome_salvo
                if not nome_lead or nome_lead == phone:
                    for msg in [m for m in messages[:8] if m.get("role") == "user"]:
                        txt = str(msg.get("content","")).strip()
                        if 2 < len(txt) < 60 and not any(c.isdigit() for c in txt.replace(" ","")):
                            primeiro = txt.split()[0] if txt.split() else ""
                            if len(primeiro) > 2 and primeiro.replace("-","").isalpha() and primeiro.lower() not in PALAVRAS_NAO_NOME:
                                nome_lead = primeiro.capitalize()
                                break
                if not nome_lead:
                    nome_lead = phone

                cidade_lead = lead.city or ""
                if not cidade_lead:
                    CIDADES_BR = [
                        "joinville","jaragua do sul","blumenau","florianopolis","curitiba",
                        "sao paulo","porto alegre","itajai","brusque","balneario camboriu",
                        "chapeco","criciuma","lages","sao bento do sul","guaramirim",
                        "schroeder","araquari","mafra","campo alegre","garuva","massaranduba",
                        "belem","maraba","santarem","manaus","fortaleza","recife","salvador",
                        "natal","joao pessoa","maceio","aracaju","teresina","sao luis",
                        "goiania","brasilia","cuiaba","campo grande","rio de janeiro",
                        "belo horizonte","vitoria","campinas","ribeirao preto","londrina",
                        "maringa","cascavel","caxias do sul",
                    ]
                    import re as _re_cid
                    conv_raw = " ".join(
                        str(m.get("content","")) for m in messages
                        if m.get("role") == "user" and not str(m.get("content","")).startswith("[")
                    ).lower()
                    match_c = _re_cid.search(
                        r'\b(?:de|em|sou de|moro em|estou em|aqui em|fico em)\s+([a-záàâãéèêíïóôõöúçñ][a-záàâãéèêíïóôõöúçñ\s]{2,20})',
                        conv_raw
                    )
                    if match_c:
                        cand = match_c.group(1).strip().split()[0]
                        if cand in CIDADES_BR:
                            cidade_lead = cand.title()
                    if not cidade_lead:
                        for c in CIDADES_BR:
                            if c in conv_raw:
                                cidade_lead = c.title()
                                break

                msgs_cliente = [
                    str(m.get("content","")).lower() for m in messages
                    if m.get("role") == "user"
                    and not str(m.get("content","")).startswith("[SISTEMA")
                    and not str(m.get("content","")).startswith("[FOLLOWUP")
                ]
                conv_cliente = " ".join(msgs_cliente)
                conv_full = " ".join(str(m.get("content","")).lower() for m in messages)

                PRODUTO_MAP = {
                    "1908":("Plotter DG 1908i",0),"3204":("Plotter DG 3204i",0),
                    "3202":("Plotter DG 3202i",0),"1904":("Plotter DG 1904i",0),
                    "1802":("Plotter DG 1802i",0),"1801":("Plotter DG 1801i",0),
                    "dtf uv 6":("DTF UV 6003",0),"dtf uv 3":("DTF UV 3003",0),
                    "dtf textil 6":("DTF Textil 6002",0),"dtf textil 3":("DTF Textil 3002",0),
                    "dtf 60":("DTF Textil 6002",0),"dtf 30":("DTF Textil 3002",0),
                    "flatbed":("Flatbed UV 9060",0),"jinka":("Plotter de Recorte Jinka",0),
                    "laser":("Laser DG1080",0),"sublimacao":("Sublimatica",0),
                    "eco solvente":("Eco Solvente",0),"dgtex":("Tinta DGtex Premium",0),
                    "dgeco":("Tinta DGeco Premium",0),"tinta dtf":("Tinta DGtex DTF",0),
                }
                produto_lead = ""
                valor_estimado = 0
                for kw, (prod, val) in PRODUTO_MAP.items():
                    if kw in conv_cliente:
                        produto_lead = prod; valor_estimado = val; break
                if not produto_lead:
                    for kw, (prod, val) in PRODUTO_MAP.items():
                        if kw in conv_full:
                            produto_lead = prod; valor_estimado = val; break

                ORIGEM_MAP = {
                    "instagram":"Trafego Organico- Instagram","insta":"Trafego Organico- Instagram",
                    "facebook":"Trafego Organico- Facebook","google":"Trafego Organico- Google",
                    "site":"Site","indicacao":"Indicacao","indicado":"Indicacao","indicação":"Indicacao",
                }
                origem_lead = "WhatsApp Direto"
                primeiras = " ".join(str(m.get("content","")).lower() for m in messages[:6] if m.get("role") == "user")
                for kw, orig in ORIGEM_MAP.items():
                    if kw in primeiras:
                        origem_lead = orig; break

                if campanha_ativa and campanha_ativa.get("_codigo"):
                    origem_lead = campanha_ativa.get("origem", origem_lead)
                    campanha_nome = campanha_ativa.get("nome","")
                    campanha_condicoes = campanha_ativa.get("condicoes","")
                    campanha_brinde = campanha_ativa.get("brinde","")
                else:
                    campanha_nome = campanha_condicoes = campanha_brinde = ""

                tecnologia_lead = ""
                if produto_lead:
                    if "DTF UV" in produto_lead: tecnologia_lead = "DTF UV"
                    elif "DTF" in produto_lead: tecnologia_lead = "DTF Textil"
                    elif "Flatbed" in produto_lead: tecnologia_lead = "UV Rigido"
                    elif "Sublimatica" in produto_lead or "DG 1" in produto_lead: tecnologia_lead = "Sublimatica / Eco Solvente"
                    elif "Laser" in produto_lead: tecnologia_lead = "Laser"
                else:
                    if "dtf uv" in conv_cliente: tecnologia_lead = "DTF UV"
                    elif "dtf" in conv_cliente: tecnologia_lead = "DTF Textil"
                    elif "sublimacao" in conv_cliente: tecnologia_lead = "Sublimatica"
                    elif "eco solvente" in conv_cliente or "lona" in conv_cliente: tecnologia_lead = "Eco Solvente"
                    elif "laser" in conv_cliente: tecnologia_lead = "Laser"

                MARCAS = ["roland","epson","mimaki","mutoh","brother","oric","xuli","flora","infiniti",
                          "allwin","blipstay","blips","bm do brasil","sawgrass","virtuoso","ricoh","hp latex"]
                parque_maquinas = [kw.title() for kw in MARCAS if kw in conv_cliente]

                FORNECEDORES = ["bm do brasil","bm brasil","fabrijet","sawgrass","sublimax",
                                "inktec","generica","aliexpress","importada","chinesa"]
                tinta_atual = next((f.title() for f in FORNECEDORES if f in conv_cliente), "")

                import re as _re3
                volume_match = _re3.search(
                    r'(\d+)\s*(?:m(?:etros?|²|2)?(?:\s*(?:por|\/)\s*(?:dia|mes|semana))?|peças?(?:\s*(?:por|\/)\s*(?:dia|mes))?)',
                    conv_cliente
                )
                volume_str = volume_match.group(0) if volume_match else "nao informado"

                perfil_lead = "Prospect"
                if any(kw in conv_cliente for kw in ["ja tenho","ja trabalho","minha maquina","tenho uma"]):
                    perfil_lead = "Upgrade / Expansao"
                elif any(kw in conv_cliente for kw in ["comecando","comecar","montar negocio","nao tenho"]):
                    perfil_lead = "Iniciante / Novo Negocio"

                objecoes = []
                if any(kw in conv_cliente for kw in ["caro","muito caro","ta caro"]): objecoes.append("Preco elevado")
                if any(kw in conv_cliente for kw in ["sem entrada","nao tenho entrada"]): objecoes.append("Dificuldade com entrada")
                if any(kw in conv_cliente for kw in ["mais barato","outra empresa","outro lugar"]): objecoes.append("Comparacao com concorrente")

                import json as _json
                cnpj_info = ""
                serasa_score = None
                serasa_negativos = None
                serasa_regime = None
                serasa_nivel = None
                serasa_recomendacao = None
                serasa_fatores = None
                if lead_state.cnpj_data:
                    try:
                        cd = _json.loads(lead_state.cnpj_data)
                        if cd and "error" not in cd:
                            raw_serasa = format_serasa_summary(cd)
                            if raw_serasa and len(raw_serasa) > 50:
                                _regime = get_regime_serasa(cd)
                                _score = get_score(cd)
                                _negativos = tem_negativos(cd)
                                _veredito = avaliar_risco_negocio(cd)
                                serasa_score = _score
                                serasa_negativos = _negativos
                                serasa_regime = _regime
                                serasa_nivel = _veredito["nivel"]
                                serasa_recomendacao = _veredito["recomendacao"]
                                serasa_fatores = "; ".join(_veredito["fatores"])
                                if _regime == "MEI": _parecer = "MEI — ANALISE PERSONALIZADA"
                                elif _veredito["nivel"] == "bloqueado": _parecer = _veredito["motivo"]
                                else: _parecer = f"{_veredito['nivel'].upper()} — {_veredito['recomendacao']} (score {_score}/1000)"
                                cnpj_info = f"PARECER: {_parecer}\n\nFATORES: {serasa_fatores}\n\n" + raw_serasa
                    except: pass

                PREFIXOS_SISTEMA = ("[SISTEMA","[FOLLOWUP","Regime:","Score:","CNPJ Ativo:","Negativos:","INSTRUCAO:","NAO ENCONTRADO")
                historico_lines = []
                for msg in messages[-20:]:
                    role = "Cliente" if msg["role"] == "user" else "Bruno"
                    txt = str(msg.get("content","")).strip()
                    if not any(txt.startswith(p) for p in PREFIXOS_SISTEMA) and txt:
                        historico_lines.append(f"{role}: {txt[:300]}")

                # Conversa real, estruturada com timestamp, pra virar
                # historico de mensagens de verdade no Doss CRM (nao so
                # um resumo em texto solto). Usa raw_history (nao
                # 'messages') porque so ele tem created_at de cada
                # mensagem individual.
                mensagens_estruturadas = []
                for msg in raw_history[-40:]:
                    txt = (msg.content or "").strip()
                    if not txt or any(txt.startswith(p) for p in PREFIXOS_SISTEMA):
                        continue
                    if txt.startswith("[CAMPANHA"):
                        continue
                    mensagens_estruturadas.append({
                        "is_from_contact": msg.role == "user",
                        "content": txt[:2000],
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    })

                email_info = lead_state.email or "nao informado"
                tel_info = lead_state.telefone or phone
                regime_info = ""
                if lead_state.cnpj_data:
                    try:
                        cd = _json.loads(lead_state.cnpj_data)
                        regime_info = get_regime_serasa(cd)
                    except: pass

                resumo = (
                    "=== LEAD WHATSAPP — DOSS GROUP ===\n\n"
                    "── DADOS DO CLIENTE ─────────────────────────\n"
                    f"Nome:          {nome_lead}\n"
                    f"WhatsApp:      {phone}\n"
                    f"Telefone:      {tel_info}\n"
                    f"E-mail:        {email_info}\n"
                    f"Cidade:        {cidade_lead or 'nao informada'}\n"
                    f"CNPJ:          {lead_state.cnpj or 'Pessoa Fisica / nao informado'}\n"
                    f"Regime:        {regime_info or 'nao consultado'}\n\n"
                    + (f"── CAMPANHA ──────────────────────────────────\nCampanha: {campanha_nome}\nCondicoes: {campanha_condicoes}\n" + (f"Brinde: {campanha_brinde}\n" if campanha_brinde else "") + "\n" if campanha_nome else "")
                    + "── INTERESSE COMERCIAL ──────────────────────\n"
                    f"Produto:       {produto_lead or 'nao identificado'}\n"
                    f"Tecnologia:    {tecnologia_lead or 'nao identificada'}\n"
                    f"Perfil:        {perfil_lead}\n"
                    f"Valor estim.:  {'R$ {:,.0f}'.format(valor_estimado) if valor_estimado else 'a consultar'}\n"
                    f"Volume prod.:  {volume_str}\n\n"
                    "── PARQUE DE MAQUINAS ────────────────────────\n"
                    f"Maquinas:      {', '.join(parque_maquinas) if parque_maquinas else 'nao identificado'}\n"
                    f"Tinta atual:   {tinta_atual or 'nao identificado'}\n\n"
                    "── OBJECOES ──────────────────────────────────\n"
                    f"{', '.join(objecoes) if objecoes else 'Nenhuma registrada'}\n\n"
                    "── ORIGEM ────────────────────────────────────\n"
                    f"Canal:         {origem_lead}\n\n"
                    "── CONVERSA (ultimas mensagens) ──────────────\n"
                    + "\n".join(historico_lines[-14:])
                )

                # FIX: era asyncio.create_task(_criar_card()) -- disparava a
                # tarefa e seguia em frente sem esperar. O 'finally: db.close()'
                # la embaixo fechava a sessao do banco antes da tarefa em
                # segundo plano rodar de verdade, e ela quebrava com
                # DetachedInstanceError ao tentar ler lead_state.cnpj --
                # ANTES de sequer montar a chamada pro Doss CRM. Resultado:
                # zero leads criados, erro silencioso (ninguem via, so
                # aparecia como "Task exception was never retrieved" no log).
                # Agora usa 'await' de verdade: roda enquanto a sessao ainda
                # esta aberta, e qualquer falha real cai no except la embaixo
                # (que ja loga e avisa o cliente) em vez de sumir sem rastro.
                async def _criar_card():
                    resultado = await enviar_lead_crm(
                        phone, nome_lead, resumo,
                        produto=produto_lead, cidade=cidade_lead, origem=origem_lead,
                        valor_estimado=valor_estimado, tecnologia=tecnologia_lead,
                        perfil=perfil_lead, serasa_nota=cnpj_info,
                        mensagens=mensagens_estruturadas,
                        serasa_cnpj=lead_state.cnpj or None,
                        serasa_score=serasa_score,
                        serasa_negativos=serasa_negativos,
                        serasa_regime=serasa_regime,
                        serasa_nivel=serasa_nivel,
                        serasa_recomendacao=serasa_recomendacao,
                        serasa_fatores=serasa_fatores,
                        email=lead_state.email or None,
                    )
                    # Card no Doss CRM -- so pelo caminho simples se o
                    # caminho com rodizio de verdade (enviar_lead_crm) falhou.
                    # Antes rodava sempre incondicional, entao mesmo quando
                    # o CRM atribuia certinho um vendedor via rodizio, esse
                    # fallback rodava por cima logo em seguida.
                    if not resultado.get("ok"):
                        await criar_lead_no_pipeline(
                            phone, nome=nome_lead, cidade=cidade_lead,
                            email=lead_state.email or None, resumo=resumo, finalizado=True,
                        )

                    if resultado.get("ok"):
                        logger.info(f"[ARCCA] Card criado/entregue para {phone}")
                        # 2 = entregue a um vendedor (nao mais retido)
                        lead_state.card_id = 2

                        # Avisa o cliente quem assume e por qual numero -- a
                        # partir daqui e ESSE numero que ele deve chamar, nao
                        # o do Bruno (que ja parou de responder, ver trava de
                        # handoff em webhooks.py). So manda se o CRM devolveu
                        # um vendedor de verdade (lead com dono, nao retido).
                        agent_name = resultado.get("agent_name")
                        agent_phone = resultado.get("agent_phone")
                        if agent_name and agent_phone:
                            numero_fmt = _formatar_telefone_br(agent_phone)
                            aviso = (
                                f"A partir de agora quem continua com você é o(a) {agent_name.split()[0]}, "
                                f"pelo WhatsApp {numero_fmt}. Salva esse número aí -- é por ele que vai rolar "
                                f"o resto do atendimento."
                            )
                            await asyncio.sleep(2.0)
                            await twilio_service.send_whatsapp_message(phone, aviso)
                            asyncio.create_task(log_message_to_crm(phone, aviso, is_from_contact=False))
                    else:
                        logger.error(f"[ARCCA] FALHA ao criar card para {phone} -- lead perdido, verificar Doss CRM")
                await _criar_card()

            lead_state.stage = "closed"
            db.commit()
            logger.info(f"[FLUXO] Conversa encerrada para {phone}")

        # ── Handoff fraco: Bruno sinaliza que vai verificar algo (equipe
        # tecnica, disponibilidade, etc) mas NAO se despediu -- a conversa
        # continua. Sem isso o lead sumia se o cliente nao voltasse depois:
        # nenhum card nascia, nada ficava registrado alem do espelho bruto
        # no Inbox (que ninguem monitora ativamente). Cria RETIDO, sem dono
        # (finalizado=False) -- so pra nao perder o lead, o vendedor de
        # verdade so entra no gatilho forte (despedida) la em cima.
        elif (
            not despedida_detectada
            and lead_state.stage not in ("closed",)
            and not lead_state.card_id
            and any(kw in reply_lower for kw in [
                "vou acionar", "preciso acionar", "vou verificar com nossa equipe",
                "vou verificar com a equipe", "vou consultar nossa equipe",
                "equipe tecnica para verificar", "vou chamar um tecnico",
                "vou chamar nosso tecnico", "vou passar pro tecnico",
                "vou passar para o tecnico", "vou confirmar com o tecnico",
                "acionar nossa equipe tecnica", "verificar a viabilidade",
            ])
        ):
            nome_soft = lead.name if (lead.name and lead.name != phone and len(lead.name) > 2) else phone
            cidade_soft = lead.city or ""
            historico_soft = []
            for msg in messages[-10:]:
                txt = str(msg.get("content", "")).strip()
                if txt and not txt.startswith(("[SISTEMA", "[FOLLOWUP", "Regime:", "Score:", "CNPJ Ativo:", "Negativos:", "INSTRUCAO:")):
                    historico_soft.append(f"{'Cliente' if msg.get('role') == 'user' else 'Bruno'}: {txt[:250]}")
            resumo_soft = (
                "=== LEAD EM QUALIFICACAO (Bruno ainda conversando) ===\n\n"
                f"Cliente:  {nome_soft}\nWhatsApp: {phone}\nCidade:   {cidade_soft or 'nao informada'}\n\n"
                "Bruno sinalizou que vai verificar algo (equipe tecnica / disponibilidade) "
                "antes de fechar. Card criado cedo pra nao perder o lead caso a conversa "
                "esfrie antes do fechamento.\n\n"
                "── CONVERSA (ultimas mensagens) ──\n" + "\n".join(historico_soft)
            )
            resultado_soft = await enviar_lead_crm(
                phone, nome_soft, resumo_soft,
                cidade=cidade_soft, origem="Bruno IA", finalizado=False,
                email=lead_state.email or None,
            )
            if not resultado_soft.get("ok"):
                # So usa o caminho simples (sem rodizio de verdade) se o
                # caminho certo falhou -- antes rodava sempre, e mesmo
                # quando o CRM atribuia um vendedor certinho via rodizio,
                # esse fallback rodava por cima e podia sobrescrever/
                # confundir o dono do card.
                await criar_lead_no_pipeline(
                    phone, nome=lead.name, cidade=lead.city or None,
                    email=lead_state.email or None, resumo=resumo_soft, finalizado=False,
                )

            if resultado_soft.get("ok"):
                logger.info(f"[ARCCA] Card retido (handoff fraco) criado para {phone}")
                lead_state.card_id = 1
                db.commit()
            else:
                logger.error(f"[ARCCA] FALHA ao criar card retido para {phone}")

        # ── Card por QUALIFICACAO ──────────────────────────────────
        # Antes o card so nascia em dois casos: despedida (handoff forte)
        # ou o Bruno dizer alguma frase tipo "vou verificar com a equipe"
        # (handoff fraco). Conversa que engajava de verdade mas terminava
        # sem nenhuma dessas frases NUNCA virava card -- o lead ficava so
        # na Inbox e ninguem trabalhava. Aconteceu com metade dos leads.
        #
        # Agora, se o cliente ja se identificou (nome de verdade, nao o
        # telefone) E demonstrou interesse concreto em produto, o card
        # nasce retido, sem dono. Retido de proposito: e lead qualificado,
        # mas ainda nao e entrega formal pro vendedor -- isso continua
        # sendo o handoff forte.
        elif (
            not despedida_detectada
            and lead_state.stage not in ("closed",)
            and not lead_state.card_id
            and lead.name
            and lead.name != phone
            and len(str(lead.name).strip()) > 2
            and sum(1 for m in messages if m.get("role") == "user") >= 3
        ):
            historico_q = []
            for msg in messages[-10:]:
                txt = str(msg.get("content", "")).strip()
                if txt and not txt.startswith(("[SISTEMA", "[FOLLOWUP", "Regime:", "Score:", "CNPJ Ativo:", "Negativos:", "INSTRUCAO:")):
                    historico_q.append(f"{'Cliente' if msg.get('role') == 'user' else 'Bruno'}: {txt[:250]}")
            resumo_q = (
                "=== LEAD QUALIFICADO (Bruno ainda conversando) ===\n\n"
                f"Cliente:  {lead.name}\nWhatsApp: {phone}\n"
                f"Cidade:   {lead.city or 'nao informada'}\n"
                f"E-mail:   {lead_state.email or 'nao informado'}\n\n"
                "Cliente se identificou e demonstrou interesse concreto. Card criado\n"
                "retido para o lead nao se perder caso a conversa esfrie antes do\n"
                "fechamento.\n\n"
                "── CONVERSA (ultimas mensagens) ──\n" + "\n".join(historico_q)
            )
            resultado_q = await enviar_lead_crm(
                phone, lead.name, resumo_q,
                cidade=lead.city or "", origem="Bruno IA", finalizado=False,
                email=lead_state.email or None,
            )
            if not resultado_q.get("ok"):
                await criar_lead_no_pipeline(
                    phone, nome=lead.name, cidade=lead.city or None,
                    email=lead_state.email or None, resumo=resumo_q, finalizado=False,
                )

            if resultado_q.get("ok"):
                logger.info(f"[ARCCA] Card por qualificacao criado para {phone} ({lead.name})")
                lead_state.card_id = 1
                db.commit()
            else:
                logger.error(f"[ARCCA] FALHA ao criar card por qualificacao para {phone}")

        elif lead_state.stage == "active":
            if any(kw in reply_lower for kw in [
                "tem cnpj","voce tem cnpj","qual o cnpj","me passa o cnpj",
                "para eu montar a proposta","preciso do seu cnpj",
                "seu cnpj","o seu cnpj","cnpj da empresa",
                "simular o investimento","condicoes de parcelamento"
            ]):
                lead_state.stage = "awaiting_cnpj"
                db.commit()

        return split_text(reply_text)

    except Exception as e:
        logger.error(f"Erro em process_message_with_assistant: {e}", exc_info=True)
        return ["Desculpe, tive uma falha aqui. Pode repetir?"]
    finally:
        db.close()


import re as _re

def _formatar_telefone_br(numero: str) -> str:
    """Formata numero cru (ex: 554792307367) como (47) 99230-7367 pra
    mandar num texto legivel pro cliente. Aceita 8 ou 9 digitos depois
    do DDD (numeros antigos as vezes nao tem o 9 na frente)."""
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return numero or ""


def _clean_cnpj(text: str) -> str:
    return _re.sub(r'[^0-9]', '', text)

def _lookup_cnpj_sync(cnpj: str) -> dict:
    import requests as _req
    cnpj_clean = _clean_cnpj(cnpj)
    if len(cnpj_clean) != 14:
        return {"error": "cnpj_invalido"}
    try:
        r = _req.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_clean}", timeout=8)
        return r.json() if r.status_code == 200 else {"error": f"status_{r.status_code}"}
    except Exception as e:
        return {"error": str(e)}

async def lookup_cnpj(cnpj: str) -> dict:
    return await asyncio.to_thread(_lookup_cnpj_sync, cnpj)

def get_regime(cnpj_data: dict) -> str:
    if not cnpj_data or "error" in cnpj_data:
        return "desconhecido"
    opcao = (cnpj_data.get("opcao_pelo_simples") or "").upper()
    porte  = (cnpj_data.get("porte") or "").upper()
    if "MEI" in porte or cnpj_data.get("descricao_porte","").upper() == "MICRO EMPRESA INDIVIDUAL":
        return "MEI"
    if opcao == "SIM":
        return "SIMPLES"
    return "normal"

def format_cnpj_summary(cnpj_data: dict) -> str:
    if not cnpj_data or "error" in cnpj_data:
        return "CNPJ nao encontrado na Receita Federal."
    return (
        f"Razao Social: {cnpj_data.get('razao_social','')}\n"
        f"Nome Fantasia: {cnpj_data.get('nome_fantasia','')}\n"
        f"Situacao: {cnpj_data.get('descricao_situacao_cadastral','')}\n"
        f"Porte: {cnpj_data.get('descricao_porte','')}\n"
        f"Regime: {get_regime(cnpj_data)}"
    )
