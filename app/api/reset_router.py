from fastapi import APIRouter
from app.models.database import SessionLocal, Lead, Conversation, LeadState

router = APIRouter()

@router.get("/reset/{phone}")
def reset_lead(phone: str, token: str = ""):
    if token != "doss2025":
        return {"error": "não autorizado"}
    db = SessionLocal()
    db.query(Conversation).filter(Conversation.phone == phone).delete()
    db.query(LeadState).filter(LeadState.phone == phone).delete()
    db.query(Lead).filter(Lead.phone == phone).delete()
    db.commit()
    db.close()
    return {"ok": f"{phone} resetado"}

@router.get("/listar")
def listar_leads(token: str = ""):
    if token != "doss2025":
        return {"error": "não autorizado"}
    db = SessionLocal()
    leads = db.query(Lead).all()
    result = [{"phone": l.phone, "name": l.name} for l in leads]
    db.close()
    return result
    
