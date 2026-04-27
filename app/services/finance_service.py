import logging
import asyncio
from typing import List, Dict
from app.services.uniplus_client import uniplus_service
from app.services.twilio_client import twilio_service

logger = logging.getLogger(__name__)

# Configuração da Régua de Cobrança (Dias relativos ao vencimento: negativo = futuro, positivo = passado)
COLLECTION_RULE = {
    -5: "Olá {nome}! Tudo bem? Passando para lembrar que sua fatura vence em 5 dias. Posso te ajudar com o boleto?",
    -2: "Oi {nome}! Sua fatura vence depois de amanhã. Segue o lembrete para evitar qualquer transtorno.",
    -1: "Bom dia {nome}! Amanhã vence sua fatura Doss Group. Já se programou para o pagamento?",
    0:  "Olá {nome}! Sua fatura vence hoje. Caso precise da segunda via, me avise aqui!",
    1:  "Oi {nome}! Notamos que sua fatura de ontem ainda não consta como paga. Houve algum problema?",
    2:  "Olá {nome}. Tudo bem? Consta uma pendência de 2 dias no sistema. Vamos regularizar isso hoje?",
    5:  "Fala {nome}. Michael aqui. Vi que sua fatura está com 5 dias de atraso. O que houve? Me dá um alô para não termos que encaminhar para o financeiro.",
    15: "AVISO IMPORTANTE: {nome}, sua fatura está com 15 dias de atraso e entrará em processo de protesto em breve. Por favor, regularize urgentemente."
}

# MODO DE TESTE: Se True, envia APENAS para os nomes nesta lista
TEST_MODE = True
TEST_MODE_ONLY_CUSTOMERS = ["SO REVENDO", "MICHAEL"] 

class FinanceService:
    async def run_daily_collection(self):
        """
        Executa a varredura diária da régua de cobrança com trava de segurança.
        """
        logger.info(f"Iniciando régua de cobrança (MODO TESTE: {TEST_MODE})...")
        
        for days, template in COLLECTION_RULE.items():
            receivables = await uniplus_service.list_receivables(days_offset=days)
            
            for rec in receivables:
                nome_cliente = rec.get("contato", {}).get("nome", "Cliente").upper()
                
                # TRAVA DE SEGURANÇA: No modo teste, pula quem não está na lista
                if TEST_MODE and not any(target in nome_cliente for target in TEST_MODE_ONLY_CUSTOMERS):
                    continue

                telefone = rec.get("contato", {}).get("celular", "")
                
                if not telefone:
                    continue
                
                # Limpa telefone para formato Twilio
                telefone_limpo = telefone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                if not telefone_limpo.startswith("+"):
                    telefone_limpo = f"+55{telefone_limpo}"

                mensagem = template.format(nome=nome_cliente)
                
                logger.info(f"Enviando cobrança ({days} dias) para {nome_cliente} ({telefone_limpo})")
                
                # Envia via Twilio
                # Usamos um pequeno delay entre envios para evitar bloqueio
                await twilio_service.send_whatsapp_message(telefone_limpo, mensagem)
                await asyncio.sleep(2) 

finance_service = FinanceService()
