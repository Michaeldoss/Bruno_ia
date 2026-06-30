from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings
import datetime

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine — suporta SQLite (local) e PostgreSQL (produção no Render)
# ---------------------------------------------------------------------------
is_sqlite = "sqlite" in settings.DATABASE_URL.lower()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
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
    cnpj_data = Column(Text,   nullable=True)
    email     = Column(String, nullable=True)
    telefone  = Column(String, nullable=True)
    card_id   = Column(Integer, nullable=True)

    # ── Follow-up automático ───────────────────────────────────────────────
    followup_step    = Column(Integer,  default=0)
    followup_sent_at = Column(DateTime, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class MediaSent(Base):
    """Rastreia mídias já enviadas por conversa — persiste entre reinicializações."""
    __tablename__ = "media_sent"

    id          = Column(Integer, primary_key=True, index=True)
    phone       = Column(String, index=True)
    product_key = Column(String)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)
    

class UsageLog(Base):
    """Registra uso de tokens da API Anthropic a cada resposta do Bruno."""
    __tablename__ = "usage_logs"
 
    id          = Column(Integer, primary_key=True, index=True)
    agente      = Column(String, default="bruno", index=True)   # "bruno", "liz", etc — preparado p/ multi-agente
    model       = Column(String)                                  # "claude-haiku-4-5-20251001" ou "claude-sonnet-4-6"
    input_tokens        = Column(Integer, default=0)
    output_tokens       = Column(Integer, default=0)
    cache_creation_tokens = Column(Integer, default=0)
    cache_read_tokens      = Column(Integer, default=0)
    custo_usd   = Column(Float, default=0.0)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow, index=True)
 

# ---------------------------------------------------------------------------
# Cria tabelas automaticamente se não existirem
# No PostgreSQL isso é idempotente — não apaga dados existentes
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)
