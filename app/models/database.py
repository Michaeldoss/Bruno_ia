from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, MetaData
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings
import datetime

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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

    id      = Column(Integer, primary_key=True, index=True)
    phone   = Column(String, unique=True, index=True)

    # ── Fluxo principal ────────────────────────────────────────────────────
    stage   = Column(String, default="active")
    # Estágios: active | awaiting_cnpj | cnpj_received | closed | followup_closed

    # ── Dados coletados ────────────────────────────────────────────────────
    cnpj      = Column(String, nullable=True)
    cnpj_data = Column(Text, nullable=True)    # JSON com dados Serasa
    email     = Column(String, nullable=True)
    telefone  = Column(String, nullable=True)
    card_id   = Column(Integer, nullable=True) # ID da oportunidade no Arcca

    # ── Follow-up automático ───────────────────────────────────────────────
    followup_step     = Column(Integer, default=0)     # step atual (0 = não iniciado)
    followup_sent_at  = Column(DateTime, nullable=True) # quando o último follow-up foi enviado

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# Cria tabelas (inclui colunas novas via ALTER automático no SQLite)
Base.metadata.create_all(bind=engine)

# ── Migração segura para banco existente (SQLite) ──────────────────────────
# Adiciona colunas novas se o banco já existia sem elas
import sqlalchemy as _sa

def _add_column_if_missing(table: str, col_name: str, col_type: str):
    try:
        with engine.connect() as conn:
            conn.execute(_sa.text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            conn.commit()
    except Exception:
        pass  # coluna já existe — ignora

_add_column_if_missing("lead_states", "followup_step",    "INTEGER DEFAULT 0")
_add_column_if_missing("lead_states", "followup_sent_at", "DATETIME")
