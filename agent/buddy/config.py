from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    backend_url: str = ""
    buddy_service_key: SecretStr = SecretStr("")
    demo_mode: bool = True
    telegram_bot_token: SecretStr = SecretStr("")
    llm_provider: Literal["fake", "openrouter", "compatible"] = "fake"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=20, gt=0, le=20)
    embeddings_model: str = "intfloat/multilingual-e5-small"
    health_host: str = "0.0.0.0"
    health_port: int = Field(default=8081, ge=1, le=65535)
    access_monitor_seconds: int = Field(default=3600, ge=60, le=86400)
    # H3 search: bm25 needs no model and no database; vector and rrf load e5 lazily and use pgvector.
    rag_search_mode: Literal["bm25", "vector", "rrf"] = "bm25"
    rag_database_url: SecretStr = SecretStr("")
    rag_min_similarity: float = Field(default=0.82, ge=0, le=1)
    # H0: default send time and time zone of the morning brief; public base of the /r redirect.
    brief_time: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    brief_timezone: str = "Europe/Moscow"
    redirect_base_url: str = ""
    # ST4-GRAPH-2: request pipeline — deterministic LangGraph StateGraph or the original Router → Executor.
    agent_engine: Literal["legacy", "graph"] = "graph"

    @model_validator(mode="after")
    def validate_live(self) -> "Settings":
        if bool(self.backend_url) != bool(self.buddy_service_key.get_secret_value()):
            raise ValueError("BACKEND_URL and BUDDY_SERVICE_KEY must be configured together")
        if not self.demo_mode:
            if not self.telegram_bot_token.get_secret_value():
                raise ValueError("Live mode requires TELEGRAM_BOT_TOKEN")
            if self.llm_provider == "fake" or not self.llm_model or not self.llm_api_key.get_secret_value():
                raise ValueError("Live mode requires LLM_PROVIDER, LLM_MODEL and LLM_API_KEY")
            if self.llm_provider == "compatible" and not self.llm_base_url:
                raise ValueError("Compatible provider requires LLM_BASE_URL")
        return self
