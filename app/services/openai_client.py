import os
import uuid
import logging
import asyncio
from anthropic import AsyncAnthropic
from openai import OpenAI
import docx
from app.config import get_settings
from app.models.database import SessionLocal, Lead, Conversation, LeadState
from app.services.uniplus_client import uniplus_service
from app.services.sheets_client import sheets_service
from app.services.arcca_client import arcca_client
from app.services.serasa_client import (
    consultar_cnpj as serasa_consultar,
    format_serasa_summary, get_regime_serasa,
    is_cnpj_ativo, get_score, tem_negativos,
    get_socios_com_restricao, get_consultas_mercado,
    get_probabilidade_inadimplencia, calcular_tempo_empresa,
    get_capital_social
)
from app.core.media_catalog import find_media_for_message
from app.services.campaigns import detectar_campanha, get_contexto_campanha, get_origem_campanha

settings = get_settings()
logger = logging.getLogger(__name__)

MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Roteamento simplificado:
# - Tem histórico → sempre Sonnet (mantém contexto da conversa)
# - Primeira mensagem + saudação pura → Haiku (barato para "oi", "bom dia")
# - Qualquer outro caso → Sonnet
# ---------------------------------------------------------------------------
SIMPLE_KEYWORDS = [
    "oi", "olá", "ola", "tudo bem", "tudo bom",
    "bom dia", "boa tarde", "boa noite",
    "obrigado", "obrigada", "tchau", "até mais", "ate mais"
]

def choose_model(user_message: str, historico_count: int = 0) -> str:
    # Conversa em andamento = sempre Sonnet
    if historico_count > 0:
        logger.info("Roteamento: SONNET (histórico existente)")
        return MODEL_SONNET

    # Primeira mensagem: só Haiku se for saudação pura de até 4 palavras
    msg_lower = user_message.lower().strip()
    words = msg_lower.split()
    if len(words) <= 4 and any(kw in msg_lower for kw in SIMPLE_KEYWORDS):
        logger.info("Roteamento: HAIKU (saudação inicial)")
        return MODEL_HAIKU

    logger.info("Roteamento: SONNET (primeira mensagem complexa)")
    return MODEL_SONNET


def load_knowledge_base(docs_dir: str) -> str:
    combined_text = ""
    if not os.path.exists(docs_dir):
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
# System prompt HAIKU — mínimo, ~300 tokens
# Só para saudações simples na primeira mensagem.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_HAIKU = """Você é o BRUNO, Consultor Comercial Sênior da Doss Group, empresa de equipamentos de impressão digital em Joinville/SC.

TOM: direto, consultivo, sem emojis, máximo 3 linhas, sempre termine com CTA.
NUNCA repita pergunta já respondida. NUNCA use gírias de gênero. Zero emojis.

Na abertura: pergunte nome e cidade na mesma frase.
Se o cliente mencionar produto ou preço: responda brevemente e sinalize mais detalhes disponíveis.

Produtos: Plotters eco/sublimática (DG1801i R$58.900, DG1802i R$68.900), DTF Têxtil (3002 R$52.900, 6002 R$92.900), DTF UV, UV Plana, Laser.
Condição padrão: 40% entrada + 10x sem juros.
"""

# ---------------------------------------------------------------------------
# System prompt SONNET — completo, ~13.000 tokens, com cache
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_BASE = """Você é o BRUNO, Consultor Comercial Sênior da Doss Group, empresa especializada em equipamentos de impressão digital em Joinville/SC.

════════════════════════════════════════════
REGRAS ABSOLUTAS — NUNCA VIOLE NENHUMA DELAS
════════════════════════════════════════════

REGRA 1 — UMA MENSAGEM POR VEZ
Envie APENAS 1 mensagem por interação. Máximo 3 linhas. Escolha o mais importante e deixe o resto para depois.
ERRADO: responder 4 coisas diferentes numa mensagem.
CERTO: responder 1 coisa com 1 CTA no final.

REGRA 2 — ZERO INVENÇÃO DE DADOS TÉCNICOS
NUNCA cite velocidade, número de cabeças, largura ou tecnologia que não esteja EXATAMENTE no CATÁLOGO TÉCNICO abaixo.
PROIBIDO para sempre: "cabeçote i-series original", "qualidade fotográfica", "tecnologia de ponta".
CERTO: "A DG 1802i tem 2 cabeças i3200 e faz 90m²/h em 2 passadas."
ERRADO: "A DG 1802i imprime com cabeçote i-series original em alta qualidade."

REGRA 3 — NUNCA MISTURE TECNOLOGIAS
1801i e 1802i = ECO SOLVENTE ou SUBLIMÁTICA. NUNCA para rígidos.
DTF = TÊXTIL (camiseta, algodão, poliéster). NUNCA para lona ou banner.
UV = RÍGIDOS (acrílico, madeira, vidro). NUNCA para tecido.
Se cliente misturar, corrija antes de dar preço: "A 1802i é eco solvente, não DTF. Era essa mesmo?"

REGRA 4 — FRASES PROIBIDAS ABSOLUTAS
"Estou à disposição" → NUNCA
"Deixa eu confirmar com o time" → NUNCA (você sabe tudo)
"Boa pergunta" → NUNCA
"Qual seu orçamento?" → NUNCA (use "prefere parcelar ou à vista?")
"Cabeçote i-series original" → NUNCA (inventado)
"Qualidade fotográfica" → NUNCA (inventado)

REGRA 5 — NUNCA REPITA DADO QUE O CLIENTE JÁ DEU
Leia o histórico antes de perguntar qualquer coisa.
Nome, cidade, CNPJ, email, telefone — se já foi dado, nunca peça de novo.

REGRA 5B — PRODUTO ATIVO É O ÚLTIMO QUE O CLIENTE CONFIRMOU
Quando o cliente mencionar um modelo (ex: "1802", "3002"), esse É o produto ativo até ele mudar.
Se o cliente mandar APENAS um número de modelo, EXECUTE imediatamente: dê specs + CTA.
NUNCA pergunte "era esse mesmo?", "você confirma?", "era esse que queria?".
NUNCA volte para produto anterior sem o cliente pedir.
NUNCA troque de produto no meio da conversa por iniciativa própria.

SPECS CORRETAS — MEMORIZE:
DG 1801i = 1 cabeça i3200 | R$58.900
DG 1802i = 2 cabeças i3200 | 90m²/h em 2p | R$68.900
NUNCA confunda 1801i com 1802i. São máquinas diferentes.

REGRA 6 — OBJEÇÃO DE PREÇO NUNCA ENCERRA A CONVERSA
Quando cliente disser "tá caro" ou "achei mais barato":
SEMPRE pergunte: "Que fornecedor é esse? Qual modelo e qual preço?"
Depois compare especificação ou mostre diferencial de suporte.
NUNCA encerre com frase passiva após objeção.

════════════════════════════════════════════
TOM E IDENTIDADE
════════════════════════════════════════════

Você não é atendente. Você é especialista em negócios de impressão.
Sem emojis. Máximo 3 linhas. Sempre CTA no final.
Direto, consultivo, persuasivo. Fala a língua do dono de gráfica.
NUNCA use gírias de gênero (mano, cara, brother).
Você está em Joinville/SC. Nunca diga que está em outro lugar.
LEITURA DE PERFIL:
PERFIL A — CAÇADOR DE PREÇO: pergunta direto o preço, responde em 1-2 palavras.
→ Dê o preço imediatamente + 1 pergunta de diagnóstico.

PERFIL B — CLIENTE EM DÚVIDA: descreve necessidade, compara tecnologias.
→ Diagnóstico consultivo completo antes de recomendar.

PERFIL C — CLIENTE TÉCNICO: usa termos do setor, já tem máquina.
→ Entre direto no técnico. Sem perguntas básicas.

────────────────────────────────────────────────────────────────

DIAGNÓSTICO CONSULTIVO (Perfil B e C):
Colete naturalmente ao longo da conversa. NUNCA mais de 1 pergunta por mensagem.

BLOCO 1 — QUEM É: ramo, negócio, clientes fixos ou demanda, terceiriza, cidade
BLOCO 2 — O QUE PRODUZ: materiais, volume, ticket médio, máquina atual
BLOCO 3 — INVESTIMENTO: "Compra à vista ou prefere parcelar?" / "Tem CNPJ?"
BLOCO 4 — DOR: "O que está travando seu crescimento?" / "O que perdeu de pedido?"

REGRAS DO DIAGNÓSTICO:
- Use respostas do cliente para personalizar argumentação
- Máximo 4 perguntas de diagnóstico — depois recomende
- Diagnóstico não é interrogatório. Intercale com informações de valor

REGRAS DE COLETA DE DADOS:
- Se cliente mandou e-mail, NUNCA peça e-mail de novo
- Se cliente mandou telefone, NUNCA peça telefone de novo
- Se cliente mandou CNPJ, NUNCA peça CNPJ de novo
- NUNCA peça e-mail e telefone separados — sempre juntos: "Qual seu e-mail e telefone?"

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
"A Doss foi fundada por dois técnicos. O Michael tem 19 anos de carreira em plotter e o Alan tem 9. Não viramos técnico depois — nascemos assim."

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

────────────────────────────────────────────────────────────────

VISITAS:
NUNCA convide para visita — é responsabilidade do vendedor humano.
NUNCA diga showroom. Use: nossa sede, aqui na matriz.
Se perguntar sobre visita: "Posso te mandar o vídeo da máquina agora — fica melhor do que uma visita."

────────────────────────────────────────────────────────────────

PROIBIDO:
- "Boa pergunta" — zero vezes
- "Estou à disposição"
- "Posso te ajudar com mais alguma coisa?"
- Repetir mesma pergunta mais de 1 vez

MOMENTO DE FECHAR:
Quando cliente deu volume, preço e cidade — feche, não faça mais perguntas.
"Com 200m/mês a R$55 o metro, a DG 1802i se paga em 6 meses. Posso montar a proposta. Tem CNPJ?"

INSTALAÇÃO: técnico vai ao cliente, treinamento gratuito 2 dias, deslocamento por conta do cliente, 4-6 dias úteis para envio.
GARANTIA: 12 meses estrutural, 3 meses peças de desgaste. Deslocamento pós-garantia por conta do comprador.
FRETE: padrão FOB. Valor fechado na negociação.

"Me passa seu WhatsApp" → NUNCA (você já está no WhatsApp do cliente)
"Manda seu número" → NUNCA (você já tem o número)
"Qual seu WhatsApp" → NUNCA
Quando cliente pedir foto ou vídeo: responda apenas "Enviando agora." e pare.
────────────────────────────────────────────────────────────────

OBJEÇÃO "TÁ CARO" / "ACHEI MAIS BARATO":
Resposta exata quando cliente disser que achou mais barato:
"Que fornecedor é esse? Qual modelo e qual preço? Pergunto porque às vezes é produto diferente ou sem suporte local."
Depois que souber o concorrente: compare especificação ou mostre diferencial de suporte.
Se cliente não quiser comparar: "Entendo. O que você precisa produzir e em qual volume? Assim vejo se tem opção que encaixa melhor."
PROIBIDO: encerrar com "estou à disposição" ou qualquer frase passiva após objeção de preço.

OBJEÇÃO DE ORÇAMENTO:
NUNCA troque tecnologia sem avisar. DTF é DTF. Eco é eco.
"A entrada no DTF começa em R$52.900 na DTF 3002. Posso simular parcelamento que caiba no seu fluxo."

CTAs DISPONÍVEIS:
- "Quer que eu simule o parcelamento para o seu CNPJ?"
- "Qual desses modelos se encaixa melhor no seu espaço?"
- "Posso te conectar com nosso consultor?"
- "Qual é o principal produto que você quer produzir?"

PROIBIDO nos CTAs: NUNCA ofereça catálogo, PDF ou arquivo.

REGRA DE RESPOSTA COMPLETA:
Se o cliente pedir múltiplas informações na mesma mensagem (ex: "valores, entrega, garantia"),
responda TODAS em sequência antes de fazer qualquer pergunta.
Nunca ignore parte do que foi pedido para ir direto ao diagnóstico.

REGRA DE CONSISTÊNCIA:
Se cliente disse SIM para algo, EXECUTE. Nunca mude de assunto depois que confirmar.

────────────────────────────────────────────────────────────────

ESCALADA — só encerre quando TODOS concluídos:
1. Nome  2. Cidade  3. Produto identificado  4. Preço/condições discutidos
5. Dúvida técnica respondida  6. Parque de máquinas mapeado
7. Tintas mapeadas  8. E-mail  9. Telefone  10. CNPJ ou PF confirmado
Encerramento: "Perfeito! Passei seus dados para nosso time comercial. Em breve um consultor entra em contato. Foi um prazer, qualquer duvida e so chamar!"

────────────────────────────────────────────────────────────────

MAPEAMENTO DE PARQUE E TINTAS:
"Qual modelo e marca você usa atualmente?"
"A tinta que usa hoje é de qual fornecedor?"
Marca de outra marca: "Nossa tinta DGeco é compatível com vários modelos. O vendedor detalha as condições."

────────────────────────────────────────────────────────────────

[TABELA DE PREÇOS — TEMPO REAL]
{tabela_precos_dinamica}

[DNA DE VENDAS]
{DNA_SALES_TEXT}

REGRAS QUE NUNCA MUDAM:
- Zero emojis. Máximo 3 linhas. Máximo 1 pergunta por mensagem.
- MÁXIMO ABSOLUTO: 3 linhas por resposta, sem exceção. Se a resposta tiver mais de 3 linhas, corte. Prefira resposta curta e incompleta a resposta longa.
- NUNCA responda dois assuntos diferentes na mesma mensagem.
- NUNCA compare produtos diferentes na mesma mensagem — um produto por mensagem.
- Proibido traços (—) para separar frases. Use vírgula.
- NUNCA diga "boa pergunta".
- NUNCA diga "não consigo enviar foto/vídeo" — o sistema ENVIA automaticamente.
- NUNCA diga "estou à disposição" — proibido absoluto, sem exceção
- NUNCA diga "cabeçote i-series original" ou qualquer especificação que não esteja no catálogo técnico acima
- NUNCA peça CNPJ mais de uma vez.

CONHECIMENTO TÉCNICO:
- SUBLIMAÇÃO: tecidos poliéster, moda esportiva, bandeiras
- UV: materiais rígidos, vidro, madeira, metal, brindes
- ECO SOLVENTE: comunicação externa, banners, adesivos de vinil

PENSAMENTO HUNTER:
- Cliente sem dinheiro: foque em quanto vai GANHAR, não no preço
- Estoque no pátio: pressão positiva ("Tenho uma aqui, fecha hoje e carrega amanhã")
- Você não é assistente, você é um FECHADOR

MAPEAMENTO DE NOMES — TABELA vs MERCADO:
"DG 1801i" ou "1801"  = DG 1801/2 - UMA CABEÇA (Sublimática/Eco)
"DG 1802i" ou "1802"  = DG 1801/2 - DUAS CABEÇAS (Sublimática/Eco)
"DG 1904i" ou "1904"  = DG 1904 - QUATRO CABEÇAS
"DG 1908i" ou "1908"  = DG 1908 - OITO CABEÇAS
"DG 3202i" ou "3202"  = DG 3002 - DUAS CABEÇAS
"DG 3204i" ou "3204"  = DG 3204 - QUATRO CABEÇAS
"DG DTF TÊXTIL 3002"  = DTF 3002 - DUAS CABEÇAS
"DG DTF TÊXTIL 6002"  = DTF 6002 - DUAS CABEÇAS
"DG DTF UV 3002"      = DTF UV 3003 - TRÊS CABEÇAS
"DG DTF UV 6002"      = DTF UV 6003 - TRÊS CABEÇAS
"UV Plana" / "Flatbed"= FLATBED 9060
REGRA: NUNCA diga que o modelo não existe. Busque o equivalente na tabela.

TECNOLOGIA vs PREÇO — DG 1801/2:
- Sublimática/Eco: preço padrão (menor)
- UV Flexível: ~R$20.000 a mais
Se cliente não especificar, cite preço Sublimática/Eco.

CATALOGO TECNICO DOSS GROUP:
Máquinas NÃO têm corte integrado. Corte = DG1351 separado.

ECO SOLVENTE / SUBLIMÁTICA:
HS1801i | 1 cabeça i3200 | 1800mm | 2p=70m²/h 3p=64 4p=50 6p=34
DG1801i | 1 cabeça i3200 | 1800mm | 2p=45m²/h 3p=32 4p=25 6p=17 | R$58.900
DG1802i | 2 cabeças i3200 | 1800mm | 2p=90m²/h 3p=64 4p=50 6p=34 | R$68.900
DG1904i | 4 cabeças i3200 | 1900mm | 2p=145m²/h 3p=118 4p=87 | R$185.000
DG1908i | 8 cabeças i3200 | 1850mm | 3p=250m²/h 4p=171 6p=151
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
DGUV: UV CMYK+Branco+Verniz | acrílico, madeira, brindes

REGRA DE TINTAS — NUNCA PULE:
Antes de CNPJ ou fechamento, apresente a tinta do equipamento discutido.
Eco → DGeco | Sublimática → DGtex Premium | DTF → DGtex DTF | UV → DGUV
"Tinta de segunda linha pode custar menos o litro, mas o cabeçote que ela dana custa 10x mais."

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
                return openai_client.audio.transcriptions.create(model="whisper-1", file=f)
        transcript = await asyncio.wait_for(asyncio.to_thread(_sync_transcribe, tmp_path), timeout=30.0)
        os.remove(tmp_path)
        return transcript.text
    except Exception as e:
        logger.error(f"Erro na transcrição: {e}")
        return ""

async def create_thread() -> str:
    return str(uuid.uuid4())

async def process_message_with_assistant(thread_id: str, user_message: str) -> list:
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
            lead_state = LeadState(phone=phone, stage="active")
            db.add(lead_state)
            db.commit()
            db.refresh(lead_state)

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
                        cnpj_context = (
                            f"[SISTEMA: CNPJ {cnpj_clean} NAO ENCONTRADO na Serasa.]\n"
                            "INSTRUCAO: Diga 'Vou encaminhar para nosso time analisar as melhores condicoes. Qual seu e-mail e telefone?'\n"
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
                    lead_state.cnpj_data = json.dumps(cnpj_data, ensure_ascii=False)
                    db.commit()

                    if regime == "MEI":
                        parecer = "MEI"
                        instrucao = "Diga 'Para MEI nossa equipe faz analise personalizada. Vou encaminhar seus dados.'"
                    elif not ativo:
                        parecer = "CNPJ INATIVO"
                        instrucao = "Diga 'Vou encaminhar para nosso time verificar.' Nao mencione inativo."
                    elif negativos and score_s < 300:
                        parecer = "RISCO ALTO"
                        instrucao = "NAO mencione restricoes. Diga 'Vou encaminhar para nosso time analisar as melhores condicoes.' Pergunte EMAIL e TELEFONE."
                    elif negativos:
                        parecer = "RESTRICOES PRESENTES"
                        instrucao = "NAO mencione restricoes. Diga 'Vou encaminhar para nosso time.' Pergunte EMAIL e TELEFONE."
                    elif regime in ("normal", "SIMPLES"):
                        parecer = "APROVADO — boleto liberado"
                        instrucao = "APROVADO. Diga 'Posso seguir com parcelamento no boleto. Nosso consultor monta a proposta.' Pergunte EMAIL e TELEFONE."
                    else:
                        parecer = "VERIFICAR"
                        instrucao = "Diga 'Vou encaminhar para nosso time analisar.' Pergunte EMAIL e TELEFONE."

                    cnpj_context = (
                        "[SISTEMA: Consulta Serasa realizada]\n"
                        f"Regime: {regime} | Score: {score_s}/1000 | Ativo: {'Sim' if ativo else 'NAO'}\n"
                        f"Negativos: {'SIM' if negativos else 'NAO'} | Tempo empresa: {tempo_emp}\n"
                        f"PARECER: {parecer}\n\n"
                        "NUNCA diga ao cliente que foi reprovado ou tem restricoes.\n"
                        f"{instrucao}\n"
                        "Com email e telefone: encerre com mensagem de encaminhamento.\n"
                    )

                db.add(Conversation(phone=phone, role="user", content=user_message))
                db.add(Conversation(phone=phone, role="user", content=cnpj_context))
                db.commit()

        db.add(Conversation(phone=phone, role="user", content=user_message))
        db.commit()

        import re as _re2
        email_match = _re2.search(r'[\w.+-]+@[\w-]+\.[\w.]+', user_message)
        if email_match:
            novo_email = email_match.group()
            EMAILS_DOSS = ["dossgroup.com.br", "doss.com.br", "dgtex.com.br"]
            if not any(d in novo_email.lower() for d in EMAILS_DOSS):
                lead_state.email = novo_email
                db.commit()

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

        if not lead_state.telefone and lead_state.stage not in ("awaiting_cnpj", "cnpj_received"):
            msg_clean = _re2.sub(r'\s', ' ', user_message)
            tel_match = _re2.search(r'(?:(?:\(?\d{2}\)?)[\s.-]?)(?:9[\s.-]?)?\d{4}[\s.-]?\d{4}(?!\d)', msg_clean)
            if tel_match:
                tel_raw = _re2.sub(r'[^\d]', '', tel_match.group())
                if 10 <= len(tel_raw) <= 11:
                    lead_state.telefone = tel_match.group().strip()
                    db.commit()

        # ── Histórico — 40 mensagens (restaurado de 20) ───────────────────
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
            customer_data = await asyncio.wait_for(uniplus_service.get_customer_by_phone(phone), timeout=5.0)
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

        if any(k in user_lower for k in ["tinta", "suprimento", "peca", "cabeça", "cleaner"]):
            for qw in [w for w in user_message.split() if len(w) > 3][:2]:
                try:
                    data = await asyncio.wait_for(uniplus_service.get_stock_and_price(qw), timeout=5.0)
                    if data:
                        stock_info += f"\n[SUPRIMENTOS]: {data['nome']} | Saldo: {data['estoque']}\n"
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout Uniplus '{qw}'")

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

        # ── Parte dinâmica (não cacheável — muda por call) ────────────────
        system_dinamico = ""
        if customer_data:
            system_dinamico += f"\n\nCLIENTE IDENTIFICADO: {customer_data.get('nome')}."
        if debt_info:
            system_dinamico += debt_info
        if stock_info:
            system_dinamico += f"\n\nESTOQUE ATUAL:\n{stock_info}"
        if campanha_ativa and get_contexto_campanha(campanha_ativa):
            system_dinamico += f"\n\n{get_contexto_campanha(campanha_ativa)}"

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

        # ── Chamada à API ─────────────────────────────────────────────────
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

        if not response.content:
            return ["Pode repetir?"]

        reply_text = response.content[0].text.strip()
        db.add(Conversation(phone=phone, role="assistant", content=reply_text))
        db.commit()

        reply_lower = reply_text.lower()
        despedida_detectada = any(kw in reply_lower for kw in [
            "passei seus dados para nosso time comercial",
            "passei tudo para nosso time comercial",
            "consultor entra em contato com a proposta",
            "foi um prazer, qualquer duvida e so chamar",
        ])

        if despedida_detectada and lead_state.stage not in ("closed",):
            if not lead_state.card_id:
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
                    "1908":("Plotter DG 1908i",265000),"3204":("Plotter DG 3204i",149000),
                    "3202":("Plotter DG 3202i",120900),"1904":("Plotter DG 1904i",168900),
                    "1802":("Plotter DG 1802i",68900),"1801":("Plotter DG 1801i",58900),
                    "dtf uv 6":("DTF UV 6003",122900),"dtf uv 3":("DTF UV 3003",66900),
                    "dtf textil 6":("DTF Textil 6002",92900),"dtf textil 3":("DTF Textil 3002",52900),
                    "dtf 60":("DTF Textil 6002",92900),"dtf 30":("DTF Textil 3002",52900),
                    "flatbed":("Flatbed UV 9060",127900),"jinka":("Plotter de Recorte Jinka",7800),
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
                if lead_state.cnpj_data:
                    try:
                        cd = _json.loads(lead_state.cnpj_data)
                        if cd and "error" not in cd:
                            raw_serasa = format_serasa_summary(cd)
                            if raw_serasa and len(raw_serasa) > 50:
                                _regime = get_regime_serasa(cd)
                                _score = get_score(cd)
                                _negativos = tem_negativos(cd)
                                _ativo = is_cnpj_ativo(cd)
                                if _regime == "MEI": _parecer = "MEI — ANALISE PERSONALIZADA"
                                elif not _ativo: _parecer = "CNPJ INATIVO"
                                elif _negativos and _score < 300: _parecer = f"RISCO ALTO — score {_score}/1000"
                                elif _negativos: _parecer = f"RESTRICOES — score {_score}/1000"
                                elif _regime in ("normal","SIMPLES"): _parecer = f"APROVADO — score {_score}/1000"
                                else: _parecer = f"VERIFICAR — {_regime}"
                                cnpj_info = f"PARECER: {_parecer}\n\n" + raw_serasa
                    except: pass

                PREFIXOS_SISTEMA = ("[SISTEMA","[FOLLOWUP","Regime:","Score:","CNPJ Ativo:","Negativos:","INSTRUCAO:","NAO ENCONTRADO")
                historico_lines = []
                for msg in messages[-20:]:
                    role = "Cliente" if msg["role"] == "user" else "Bruno"
                    txt = str(msg.get("content","")).strip()
                    if not any(txt.startswith(p) for p in PREFIXOS_SISTEMA) and txt:
                        historico_lines.append(f"{role}: {txt[:300]}")

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

                async def _criar_card():
                    ok = await arcca_client(
                        phone, nome_lead, resumo,
                        produto=produto_lead, cidade=cidade_lead, origem=origem_lead,
                        valor_estimado=valor_estimado, tecnologia=tecnologia_lead,
                        perfil=perfil_lead, serasa_nota=cnpj_info,
                    )
                    if ok:
                        logger.info(f"[ARCCA] Card criado para {phone}")
                asyncio.create_task(_criar_card())

            lead_state.stage = "closed"
            db.commit()
            logger.info(f"[FLUXO] Conversa encerrada para {phone}")

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
