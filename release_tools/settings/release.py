import os
from functools import lru_cache

from pydantic import (
    Field,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class ReleaseSettings(BaseSettings):
    """Defaults for release branch tooling."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="RELEASE_", extra="ignore", enable_decoding=False)

    BASE_BRANCH: str = Field(
        "master",
        description="Default release source branch",
    )
    PREFIXES: list[str] = Field(
        ["feature", "bugfix", "hotfix"],
        description="Default task branch prefixes",
    )

    @field_validator("PREFIXES", mode="before")
    @classmethod
    def decode_default_prefixes(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        return [prefix.strip() for prefix in value.split(",") if prefix.strip()]


@lru_cache
def get_release_settings() -> ReleaseSettings:
    return ReleaseSettings(_env_file=os.getenv("ENV_FILE", ".env"))
