"""Application configuration via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM providers
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")

    # Auth
    api_key: str = Field(default="", alias="API_KEY")

    # Hugging Face
    hf_token: str = Field(default="", alias="HF_TOKEN")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/review_iq.db",
        alias="DATABASE_URL",
    )

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_review_length: int = Field(default=5000, alias="MAX_REVIEW_LENGTH")
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # LLM model names
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        alias="GROQ_MODEL",
    )
    gemini_model: str = Field(
        default="gemini-1.5-flash",
        alias="GEMINI_MODEL",
    )

    # Extraction limits
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
