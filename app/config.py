from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    paypal_client_id: str
    paypal_secret: str

    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""
    database_url: str = "sqlite:///./payday.db"
    cors_origins: str = "http://localhost:5173"

    gemini_api_key: str
    llm_model: str = "gemini-3.5-flash-lite"
    llm_fallback_model: str = "gemini-2.5-flash"


settings = Settings()