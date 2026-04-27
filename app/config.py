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
    UNIPLUS_BASE_URL: str = "https://vzan-getcard01.getcard.uniplusweb.com/api/rest"
    GOOGLE_SHEET_ID: str = "stub"
    GOOGLE_SHEET_CSV_URL: str = "stub"
    GOOGLE_SHEET_SUPRIMENTOS_URL: str = "stub"
    
    DATABASE_URL: str = "sqlite:///./test.db"  # Defaults to internal sqlite if not provided
    
    SERASA_API_KEY: str = "stub"
    ARCCA_API_KEY: str = "stub"
    
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache()
def get_settings():
    return Settings()
