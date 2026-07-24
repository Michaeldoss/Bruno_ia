from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float
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
    """Controla estado do fluxo de escalada, origem e follow-up por telefone."""
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

    # ── Atribuição/origem do primeiro contato ─────────────────────────────
    # Estes campos são preenchidos pelo webhook do Twilio e permanecem até
    # a entrega ao CRM. Assim campanha/UTM não se perde durante a conversa.
    origin_channel = Column(String, nullable=True)
    campaign_name  = Column(String, nullable=True)
    adset_name     = Column(String, nullable=True)
    ad_name        = Column(String, nullable=True)
    form_name      = Column(String, nullable=True)
    utm_source     = Column(String, nullable=True)
    utm_medium     = Column(String, nullable=True)
    utm_campaign   = Column(String, nullable=True)
    utm_content    = Column(String, nullable=True)
    utm_term       = Column(String, nullable=True)
    landing_page   = Column(Text, nullable=True)
    referrer       = Column(Text, nullable=True)
    twilio_from    = Column(String, nullable=True)
    twilio_to      = Column(String, nullable=True)

    # ── Follow-up automático ───────────────────────────────────────────────
    followup_step    = Column(Integer,  default=0)
    followup_sent_at = Column(DateTime, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class InboundWebhookEvent(Base):
    """Idempotência persistente para webhooks recebidos do Twilio.

    O MessageSid é globalmente único. Ao persistir o SID, a deduplicação
    continua funcionando após reinício, troca de worker ou novo deploy.
    """
    __tablename__ = "inbound_webhook_events"

    id          = Column(Integer, primary_key=True, index=True)
    message_sid = Column(String, unique=True, index=True, nullable=False)
    phone       = Column(String, index=True, nullable=True)
    status      = Column(String, default="received", index=True)
    last_error  = Column(Text, nullable=True)
    received_at = Column(DateTime, default=datetime.datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class MediaSent(Base):
    """Rastreia mídias já enviadas por conversa — persiste entre reinicializações."""
    __tablename__ = "media_sent"

    id          = Column(Integer, primary_key=True, index=True)
    phone       = Column(String, index=True)
    product_key = Column(String)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)


class UsageLog(Base):
    """Registra uso de qualquer servico pago (Anthropic, Twilio, Whisper) em tempo real."""
    __tablename__ = "usage_logs"

    id      = Column(Integer, primary_key=True, index=True)
    agente  = Column(String, default="bruno", index=True)
    servico = Column(String, default="anthropic", index=True)
    model   = Column(String)

    input_tokens           = Column(Integer, default=0)
    output_tokens          = Column(Integer, default=0)
    cache_creation_tokens  = Column(Integer, default=0)
    cache_read_tokens      = Column(Integer, default=0)
    quantidade             = Column(Float, default=0.0)

    custo_usd = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Cria tabelas automaticamente se não existirem
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# Migração segura — adiciona colunas novas em tabelas que já existiam
# ---------------------------------------------------------------------------
import sqlalchemy as _sa


def _add_column_if_missing(table: str, col_name: str, col_type: str):
    try:
        with engine.connect() as conn:
            conn.execute(_sa.text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            conn.commit()
    except Exception:
        pass


_add_column_if_missing("usage_logs", "servico", "VARCHAR DEFAULT 'anthropic'")
_add_column_if_missing("usage_logs", "quantidade", "FLOAT DEFAULT 0.0")

for _column, _type in {
    "origin_channel": "VARCHAR",
    "campaign_name": "VARCHAR",
    "adset_name": "VARCHAR",
    "ad_name": "VARCHAR",
    "form_name": "VARCHAR",
    "utm_source": "VARCHAR",
    "utm_medium": "VARCHAR",
    "utm_campaign": "VARCHAR",
    "utm_content": "VARCHAR",
    "utm_term": "VARCHAR",
    "landing_page": "TEXT",
    "referrer": "TEXT",
    "twilio_from": "VARCHAR",
    "twilio_to": "VARCHAR",
}.items():
    _add_column_if_missing("lead_states", _column, _type)
