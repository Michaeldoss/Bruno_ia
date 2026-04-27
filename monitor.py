"""
Monitor de Conversas em Tempo Real — Bruno IA
Mostra todas as conversas ativas atualizando a cada 10 segundos.
Rode em um CMD separado com: python monitor.py
"""

import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.models.database import SessionLocal, Conversation, Lead, LeadState

ATUALIZAR_A_CADA = 10  # segundos
HORAS_EXIBIR     = 24  # mostra conversas das últimas X horas

def limpar_tela():
    os.system('cls' if os.name == 'nt' else 'clear')

def formatar_tempo(dt):
    if not dt:
        return "—"
    agora = datetime.utcnow()
    diff = agora - dt
    if diff.total_seconds() < 60:
        return f"{int(diff.total_seconds())}s atrás"
    elif diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds()//60)}min atrás"
    else:
        return dt.strftime("%d/%m %H:%M")

def exibir_monitor():
    db = SessionLocal()
    try:
        corte = datetime.utcnow() - timedelta(hours=HORAS_EXIBIR)

        # Busca leads com atividade recente
        leads = db.query(Lead).all()

        limpar_tela()
        agora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        print(f"{'='*70}")
        print(f"  BRUNO IA — MONITOR DE CONVERSAS   {agora_str}")
        print(f"  Atualizando a cada {ATUALIZAR_A_CADA}s | Ctrl+C para sair")
        print(f"{'='*70}")

        if not leads:
            print("\n  Nenhuma conversa ainda.")
            return

        for lead in leads:
            # Busca conversas recentes deste lead
            convs = (
                db.query(Conversation)
                .filter(
                    Conversation.phone == lead.phone,
                    Conversation.created_at >= corte
                )
                .order_by(Conversation.created_at.asc())
                .all()
            )

            if not convs:
                continue

            # Busca estado do lead
            state = db.query(LeadState).filter(LeadState.phone == lead.phone).first()

            ultima = convs[-1]
            tempo_ultima = formatar_tempo(ultima.created_at)

            # Header do lead
            nome = lead.name or "Desconhecido"
            cidade = lead.city or "?"
            stage = state.stage if state else "—"
            followup = f"FU-{state.followup_step}" if state and state.followup_step else ""

            print(f"\n{'─'*70}")
            print(f"  📱 {lead.phone}  |  {nome} ({cidade})")
            print(f"  Stage: {stage}  {followup}  |  Última msg: {tempo_ultima}")
            if state and state.cnpj:
                print(f"  CNPJ: {state.cnpj}  |  Email: {state.email or '—'}")
            print(f"{'─'*70}")

            # Exibe últimas 10 mensagens
            PREFIXOS_OCULTAR = ("[SISTEMA", "[CAMPANHA", "[FOLLOWUP")
            msgs_exibir = [
                m for m in convs
                if not any(m.content.startswith(p) for p in PREFIXOS_OCULTAR)
            ][-12:]

            for msg in msgs_exibir:
                hora = msg.created_at.strftime("%H:%M")
                if msg.role == "user":
                    prefixo = f"  [{hora}] 👤 "
                    role_pad = "             "
                else:
                    prefixo = f"  [{hora}]    🤖 "
                    role_pad = "                "

                # Quebra o texto em linhas de 60 chars
                texto = msg.content
                palavras = texto.split()
                linhas = []
                linha_atual = ""
                for palavra in palavras:
                    if len(linha_atual) + len(palavra) + 1 <= 58:
                        linha_atual += (" " if linha_atual else "") + palavra
                    else:
                        if linha_atual:
                            linhas.append(linha_atual)
                        linha_atual = palavra
                if linha_atual:
                    linhas.append(linha_atual)

                print(prefixo + (linhas[0] if linhas else ""))
                for linha in linhas[1:]:
                    print(role_pad + linha)

        print(f"\n{'='*70}")
        total = len([l for l in leads if db.query(Conversation).filter(
            Conversation.phone == l.phone,
            Conversation.created_at >= corte
        ).first()])
        print(f"  Total de conversas ativas: {total}")
        print(f"{'='*70}\n")

    finally:
        db.close()


if __name__ == "__main__":
    print("Iniciando monitor... (Ctrl+C para sair)")
    try:
        while True:
            exibir_monitor()
            time.sleep(ATUALIZAR_A_CADA)
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")
