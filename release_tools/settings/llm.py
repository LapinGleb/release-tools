import os
from functools import lru_cache

from pydantic import (
    Field,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class LLMSettings(BaseSettings):
    """Settings for the OpenAI-compatible endpoint report client."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LLM_",
        extra="ignore",
    )

    api_key: str = Field("", description="API key for the LLM gateway")
    base_url: str = Field("", description="Base URL for the LLM gateway")
    model: str = Field("", description="Model used to classify endpoint changes")
    timeout_seconds: float = Field(60, gt=0, description="Request timeout in seconds")
    max_attempts: int = Field(3, ge=1, description="Maximum request attempts")

    @field_validator("api_key", "base_url", "model", mode="after")
    @classmethod
    def strip_required_value(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_connection_values(self) -> "LLMSettings":
        missing = [
            env_name
            for env_name, value in (
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.base_url),
                ("LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required variables: {', '.join(missing)}")
        return self


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings(_env_file=os.getenv("ENV_FILE", ".env"))
