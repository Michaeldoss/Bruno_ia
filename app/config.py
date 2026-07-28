from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TWILIO_ACCOUNT_SID: str = "stub"
    TWILIO_AUTH_TOKEN: str = "stub"
    TWILIO_PHONE_NUMBER: str = "stub"
    TWILIO_VALIDATE_SIGNATURE: bool = False

    OPENAI_API_KEY: str = "stub"
    OPENAI_ASSISTANT_ID: str = "stub"
    ANTHROPIC_API_KEY: str = "stub"

    UNIPLUS_ACCOUNT: str = "stub"
    UNIPLUS_ACCESS_KEY: str = "stub"
    UNIPLUS_AUTH_CODE: str = "stub"
    UNIPLUS_BASE_URL: str = "https://vzan-getcard01.getcard.uniplusweb.com"
    UNIPLUS_CLIENT_ID: str = "stub"
    UNIPLUS_CLIENT_SECRET: str = "stub"
    UNIPLUS_FILIAL: str = "2"
    UNIPLUS_LOCAL_ESTOQUE: str = "2"
    UNIPLUS_OS_ENDPOINT: str = "/public-api/v1/ordem-servico"
    UNIPLUS_STATUS_FINALIZADA: str = "3"

    SUPABASE_URL: str = "https://dojisexdgitoxluuawgc.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY: str = "stub"

    GOOGLE_SHEET_ID: str = "stub"
    GOOGLE_SHEET_CSV_URL: str = "stub"
    GOOGLE_SHEET_SUPRIMENTOS_URL: str = "stub"

    DATABASE_URL: str = "sqlite:///./test.db"

    SERASA_API_KEY: str = "stub"
    ARCCA_API_KEY: str = "stub"

    DOSS_CRM_LEADS_URL: str = "https://doss-crm.vercel.app/api/leads/create"
    BRUNO_API_KEY: str = ""

    TIMEZONE: str = "America/Sao_Paulo"
    HTTP_RETRY_ATTEMPTS: int = 4
    HTTP_CONNECT_TIMEOUT_SECONDS: float = 6.0
    HTTP_TOTAL_TIMEOUT_SECONDS: float = 20.0

    # Templates aprovados no Twilio/Meta para mensagens iniciadas fora da
    # janela de 24h. Sem esses SIDs os steps 4 e 5 ficam suspensos, em vez
    # de falharem silenciosamente com erro 63016.
    FOLLOWUP_TEMPLATE_STEP_4: str = ""
    FOLLOWUP_TEMPLATE_STEP_5: str = ""

    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings():
    return Settings()
