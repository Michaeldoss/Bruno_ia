from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from app.config import get_settings
from app.api.webhooks import router as webhook_router
from app.services.followup_service import start_followup_service
from app.models.database import SessionLocal, Conversation, Lead, LeadState
from datetime import datetime, timedelta

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_followup_service()
    yield


app = FastAPI(
    title="DOSS AI BRAIN",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "DOSS AI BRAIN",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/monitor", response_class=HTMLResponse)
def monitor():
    """Painel de monitoramento de conversas em tempo real."""
    db = SessionLocal()
    try:
        corte = datetime.utcnow() - timedelta(hours=48)
        leads = db.query(Lead).all()

        html = """
        <html><head>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="15">
        <title>Bruno IA — Monitor</title>
        <style>
            body { font-family: monospace; background: #1a1a1a; color: #00ff00; padding: 20px; }
            h1 { color: #00ff00; }
            .lead { border: 1px solid #333; margin: 20px 0; padding: 15px; border-radius: 5px; }
            .lead-header { color: #ffff00; font-size: 14px; margin-bottom: 10px; }
            .msg-user { color: #00bfff; margin: 5px 0; }
            .msg-bruno { color: #00ff00; margin: 5px 0; padding-left: 20px; }
            .time { color: #666; font-size: 11px; }
            .total { color: #ff6600; margin-top: 20px; }
            hr { border-color: #333; }
        </style>
        </head><body>
        """
        html += f"<h1>🤖 Bruno IA — Monitor de Conversas</h1>"
        html += f"<p style='color:#666'>Atualiza a cada 15s | {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p><hr>"

        total = 0
        for lead in leads:
            convs = db.query(Conversation).filter(
                Conversation.phone == lead.phone,
                Conversation.created_at >= corte
            ).order_by(Conversation.created_at.asc()).all()

            if not convs:
                continue

            total += 1
            state = db.query(LeadState).filter(LeadState.phone == lead.phone).first()
            ultima = convs[-1]
            diff = datetime.utcnow() - ultima.created_at
            mins = int(diff.total_seconds() // 60)
            tempo = f"{mins}min atrás" if mins < 60 else f"{mins//60}h atrás"

            nome = lead.name or "Desconhecido"
            cidade = lead.city or "?"
            stage = state.stage if state else "—"
            email = state.email if state and state.email else "—"
            cnpj = state.cnpj if state and state.cnpj else "—"
            fu = f" | FU-{state.followup_step}" if state and state.followup_step else ""

            html += f"""<div class='lead'>
            <div class='lead-header'>
                📱 {lead.phone} | {nome} ({cidade}) | Stage: {stage}{fu}<br>
                Email: {email} | CNPJ: {cnpj} | Última msg: {tempo}
            </div>"""

            PREFIXOS = ("[SISTEMA", "[CAMPANHA", "[FOLLOWUP")
            msgs = [m for m in convs if not any(m.content.startswith(p) for p in PREFIXOS)][-15:]

            for msg in msgs:
                hora = msg.created_at.strftime("%H:%M")
                texto = msg.content.replace("<", "&lt;").replace(">", "&gt;")
                if msg.role == "user":
                    html += f"<div class='msg-user'><span class='time'>[{hora}]</span> 👤 {texto}</div>"
                else:
                    html += f"<div class='msg-bruno'><span class='time'>[{hora}]</span> 🤖 {texto}</div>"

            html += "</div>"

        html += f"<div class='total'>Total de conversas ativas (48h): {total}</div>"
        html += "</body></html>"
        return html
    finally:
        db.close()


app.include_router(webhook_router, prefix="/webhooks", tags=["Webhooks"])
