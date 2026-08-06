import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class GitLabSettings(BaseSettings):
    """Settings for GitLab API access."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GITLAB_", extra="ignore")

    TOKEN: str = Field("", description="Private token for GitLab API")
    BASE_URL: str = Field("https://gitlab.dfotech.ru", description="Base URL for GitLab")
    GROUP: str = Field("fo", description="Default GitLab group")
    PER_PAGE: int = Field(100, description="GitLab API pagination page size")


@lru_cache
def get_gitlab_settings() -> GitLabSettings:
    return GitLabSettings(_env_file=os.getenv("ENV_FILE", ".env"))
