from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Boolean
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
    produto_interesse = Column(String, nullable=True)
    ultima_sync_crm   = Column(DateTime, nullable=True)

    # ── Follow-up automático ───────────────────────────────────────────────
    followup_step    = Column(Integer,  default=0)
    followup_sent_at = Column(DateTime, nullable=True)
    recusas_count     = Column(Integer, default=0)
    # Conta quantas vezes o cliente recusou explicitamente. Na 1a recusa,
    # o Bruno tenta contornar com mais argumentacao (nao fecha). Só a
    # partir da 2a recusa e que transfere pra agente humano + pipeline.

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


class CrmSyncQueue(Base):
    """Fila durável de mensagens pendentes de espelhamento no CRM.

    Toda mensagem (do cliente ou do Bruno) grava aqui PRIMEIRO, nesse
    banco que o Bruno já depende de qualquer forma (praticamente nunca
    fica fora do ar). A sincronização com o CRM (Supabase) e' tentada
    na hora, mas se falhar, a linha fica com synced=False e um worker
    em segundo plano insiste ate conseguir -- nunca desiste, so' avisa
    se acumular tentativas demais. Isso elimina buraco de conversa no
    CRM causado por falha transitoria de rede.
    """
    __tablename__ = "crm_sync_queue"

    id              = Column(Integer, primary_key=True, index=True)
    phone           = Column(String, index=True)
    content         = Column(Text)
    is_from_contact = Column(Boolean, default=False)
    msg_type        = Column(String, default="text")
    media_url       = Column(String, nullable=True)
    whatsapp_id     = Column(String, nullable=True)
    nome            = Column(String, nullable=True)
    synced          = Column(Boolean, default=False, index=True)
    attempts        = Column(Integer, default=0)
    last_error      = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)
    synced_at       = Column(DateTime, nullable=True)


class PipelineSyncQueue(Base):
    """Fila durável para criação/atualização de card no pipeline (CRM).

    Mesmo problema que o CrmSyncQueue resolve pra mensagem, mas pro
    card em si: se o Supabase/CRM cair no meio de uma tentativa de
    handoff ou qualificação (ex: apagão do provedor, 04/08), a chamada
    falhava e o lead era PERDIDO de vez -- nada tentava de novo depois.
    Agora grava a intenção aqui primeiro, tenta na hora, e se falhar
    fica pendente pro worker reprocessar ate conseguir de verdade.
    """
    __tablename__ = "pipeline_sync_queue"

    id            = Column(Integer, primary_key=True, index=True)
    function_name = Column(String)   # 'enviar_lead_crm' ou 'criar_lead_no_pipeline'
    payload_json  = Column(Text)     # argumentos serializados (json.dumps)
    synced        = Column(Boolean, default=False)
    attempts      = Column(Integer, default=0)
    last_error    = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.datetime.utcnow)
    synced_at     = Column(DateTime, nullable=True)


class UsageLog(Base):
    """Registra uso de qualquer servico pago (Anthropic, Twilio, Whisper) em tempo real."""
    __tablename__ = "usage_logs"

    id      = Column(Integer, primary_key=True, index=True)
    agente  = Column(String, default="bruno", index=True)    # "bruno", "liz", etc
    servico = Column(String, default="anthropic", index=True)  # "anthropic", "twilio", "whisper"
    model   = Column(String)  # nome do modelo/servico especifico

    input_tokens           = Column(Integer, default=0)
    output_tokens          = Column(Integer, default=0)
    cache_creation_tokens  = Column(Integer, default=0)
    cache_read_tokens      = Column(Integer, default=0)
    quantidade             = Column(Float, default=0.0)  # mensagens (twilio) ou minutos (whisper)

    custo_usd = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Cria tabelas automaticamente se não existirem
# No PostgreSQL isso é idempotente — não apaga dados existentes
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# Migração segura — adiciona colunas novas em tabelas que já existiam
# (Base.metadata.create_all NÃO faz ALTER TABLE em colunas novas)
# ---------------------------------------------------------------------------
import sqlalchemy as _sa

def _add_column_if_missing(table: str, col_name: str, col_type: str):
    try:
        with engine.connect() as conn:
            conn.execute(_sa.text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            conn.commit()
    except Exception:
        pass  # coluna já existe — ignora

_add_column_if_missing("usage_logs", "servico", "VARCHAR DEFAULT 'anthropic'")
_add_column_if_missing("usage_logs", "quantidade", "FLOAT DEFAULT 0.0")

# FIX: essas colunas foram adicionadas no modelo Python (CrmSyncQueue e
# PipelineSyncQueue) mas nunca migradas de verdade pra tabela do
# Postgres em producao -- create_all() so cria tabela nova, nao altera
# tabela existente. Resultado: toda tentativa de gravar na fila
# durável quebrava com "invalid keyword argument" / "has no attribute",
# silenciosamente, e NENHUMA mensagem do Bruno chegava a ser
# espelhada no CRM (nem direto, nem via retry) por quase 24h.
_add_column_if_missing("crm_sync_queue", "is_from_contact", "BOOLEAN DEFAULT false")
_add_column_if_missing("crm_sync_queue", "msg_type", "VARCHAR DEFAULT 'text'")
_add_column_if_missing("crm_sync_queue", "media_url", "VARCHAR")
_add_column_if_missing("crm_sync_queue", "whatsapp_id", "VARCHAR")
_add_column_if_missing("crm_sync_queue", "nome", "VARCHAR")
_add_column_if_missing("crm_sync_queue", "synced", "BOOLEAN DEFAULT false")
_add_column_if_missing("crm_sync_queue", "attempts", "INTEGER DEFAULT 0")
_add_column_if_missing("crm_sync_queue", "last_error", "TEXT")
_add_column_if_missing("crm_sync_queue", "created_at", "TIMESTAMP")
_add_column_if_missing("crm_sync_queue", "synced_at", "TIMESTAMP")

_add_column_if_missing("pipeline_sync_queue", "synced", "BOOLEAN DEFAULT false")
_add_column_if_missing("pipeline_sync_queue", "attempts", "INTEGER DEFAULT 0")
_add_column_if_missing("pipeline_sync_queue", "last_error", "TEXT")
_add_column_if_missing("pipeline_sync_queue", "created_at", "TIMESTAMP")
_add_column_if_missing("pipeline_sync_queue", "synced_at", "TIMESTAMP")
