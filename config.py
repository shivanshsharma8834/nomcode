from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_ID: str
    WEBHOOK_SECRET: str
    GROQ_API_KEY: str
    PRIVATE_KEY_PATH: str
    REDIS_URL: str = "redis://localhost:6379/0"

    # Loads variables from a .env file if present
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

@lru_cache
def get_settings():
    return Settings()