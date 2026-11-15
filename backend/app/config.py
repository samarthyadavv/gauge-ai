from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    admin_key: str
    database_url: str = "sqlite:///./eval.db"
    base_url: str = "http://localhost:9200"
    environment: str = "development"
    openai_api_key: str = ""
    openai_models: str = "gpt-4.1-mini"
    worker_poll_seconds: float = 1

    @field_validator("database_url")
    @classmethod
    def normalize(cls, value):
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value


@lru_cache
def settings():
    s = Settings()
    if len(s.admin_key) < 32 or s.admin_key.startswith("replace"):
        raise RuntimeError(
            "ADMIN_KEY must be a random secret of at least 32 characters"
        )
    if s.environment == "production" and not s.base_url.startswith("https://"):
        raise RuntimeError("Production BASE_URL must use HTTPS")
    return s
