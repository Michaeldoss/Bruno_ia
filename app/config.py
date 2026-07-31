from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    TWILIO_ACCOUNT_SID: str = "stub"
    TWILIO_AUTH_TOKEN: str = "stub"
    TWILIO_PHONE_NUMBER: str = "stub"

    OPENAI_API_KEY: str = "stub"
    OPENAI_ASSISTANT_ID: str = "stub"
    ANTHROPIC_API_KEY: str = "stub"

    # Uniplus ERP Integration
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

    # Supabase (Doss CRM) — usado apenas pela Pesquisa de Satisfação,
    # pra gravar e ler o resultado que aparece no dashboard do CRM.
    SUPABASE_URL: str = "https://dojisexdgitoxluuawgc.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY: str = "stub"

    GOOGLE_SHEET_ID: str = "stub"
    GOOGLE_SHEET_CSV_URL: str = "stub"
    GOOGLE_SHEET_SUPRIMENTOS_URL: str = "stub"

    DATABASE_URL: str = "sqlite:///./test.db"

    SERASA_API_KEY: str = "stub"
    ARCCA_API_KEY: str = "stub"

    # Doss CRM (substitui a integracao antiga com Arcca)
    DOSS_CRM_LEADS_URL: str = "https://doss-crm.vercel.app/api/leads/create"
    BRUNO_API_KEY: str = ""

    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache()
def get_settings():
    return Settings()
