import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class HttpSettings(BaseSettings):
    """Shared HTTP client settings."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HTTP_", extra="ignore")

    TIMEOUT_SECONDS: int = Field(300, description="Total HTTP client timeout in seconds")
    RESPONSE_PREVIEW_LENGTH: int = Field(300, description="Response body preview length for HTTP errors")
    VERIFY_SSL: bool = Field(True, description="Verify SSL certificates for HTTP clients")


@lru_cache
def get_http_settings() -> HttpSettings:
    return HttpSettings(_env_file=os.getenv("ENV_FILE", ".env"))
