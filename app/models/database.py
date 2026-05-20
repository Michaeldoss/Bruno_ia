from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings
import datetime

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine — suporta SQLite (local) e PostgreSQL (produção no Render)
# ---------------------------------------------------------------------------
# SQLite: connect_args necessário para evitar erro de thread
# PostgreSQL: sem connect_args, mas precisa de pool_pre_ping para reconexão
is_sqlite = "sqlite" in settings.DATABASE_URL.lower()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=True,   # reconecta automaticamente se conexão cair
    pool_size=5,          # conexões simultâneas (ignorado no SQLite)
    max_overflow=10,      # conexões extras se pool estiver cheio
) if not is_sqlite else create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Lead(Base):
    __tablename__ = "leads"

    id        = Column(Integer, primary_key=True, index=True)
    phone     = Column(String, unique=True, index=True)
    name      = Column(String, nullable=True)
    city      = Column(String, nullable=True)
    stage     = Column(String, default="diagnostico")
    thread_id = Column(String, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(Integer, primary_key=True, index=True)
    phone      = Column(String, index=True)
    role       = Column(String)   # user ou assistant
    content    = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Document(Base):
    __tablename__ = "knowledge_documents"

    id      = Column(Integer, primary_key=True, index=True)
    title   = Column(String)
    content = Column(Text)


class LeadState(Base):
    """Controla estado do fluxo de escalada e follow-up por telefone."""
    __tablename__ = "lead_states"

    id    = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True)

    # ── Fluxo principal ────────────────────────────────────────────────────
    stage = Column(String, default="active")
    # Estágios: active | awaiting_cnpj | cnpj_received | closed | followup_closed

    # ── Dados coletados ────────────────────────────────────────────────────
    cnpj      = Column(String, nullable=True)
    cnpj_data = Column(Text,   nullable=True)    # JSON com dados Serasa
    email     = Column(String, nullable=True)
    telefone  = Column(String, nullable=True)
    card_id   = Column(Integer, nullable=True)   # ID da oportunidade no Arcca

    # ── Follow-up automático ───────────────────────────────────────────────
    followup_step    = Column(Integer,  default=0)      # step atual (0 = não iniciado)
    followup_sent_at = Column(DateTime, nullable=True)  # quando o último follow-up foi enviado

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Cria tabelas automaticamente se não existirem
# No PostgreSQL isso é idempotente — não apaga dados existentes
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)

class MediaSent(Base):
    """Rastreia mídias já enviadas por conversa — persiste entre reinicializações."""
    __tablename__ = "media_sent"

    id         = Column(Integer, primary_key=True, index=True)
    phone      = Column(String, index=True)
    product_key = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
