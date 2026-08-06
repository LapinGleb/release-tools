import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class EvaSettings(BaseSettings):
    """Settings for EVA API access and JSON-RPC payloads."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVA_", extra="ignore")

    TOKEN: str = Field("", description="Token for EVA API")
    BASE_URL: str = Field("https://eva.dfotech.ru", description="Base URL for EVA API")
    API_PATH: str = Field("/api/", description="EVA API path")
    JSONRPC_VERSION: str = Field("2.2", description="EVA JSON-RPC version")
    RELEASE_LIST_METHOD: str = Field("CmfList.get", description="EVA release lookup method")
    TASK_LIST_METHOD: str = Field("CmfTask.list", description="EVA task list method")
    RESPONSE_PREVIEW_LENGTH: int = Field(300, description="Response body preview length for EVA errors")


@lru_cache
def get_eva_settings() -> EvaSettings:
    return EvaSettings(_env_file=os.getenv("ENV_FILE", ".env"))
