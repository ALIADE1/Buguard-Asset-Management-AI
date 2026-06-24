"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration — every value can be overridden via env vars."""

    # ── Database ────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://asm_user:asm_password@db:5432/asm_db",
        description="Async SQLAlchemy database URL",
    )

    # ── Groq / LangChain ───────────────────────────────
    groq_api_key: str = Field(default="", description="Groq API key")
    llm_model: str = Field(default="llama3-70b-8192", description="LLM model name")

    # ── App ─────────────────────────────────────────────
    log_level: str = Field(default="info", description="Logging level")
    api_default_page_size: int = Field(default=50, description="Default pagination size")
    api_max_page_size: int = Field(default=200, description="Maximum pagination size")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
