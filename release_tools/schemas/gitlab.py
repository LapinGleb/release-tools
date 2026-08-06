from dataclasses import dataclass
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    model_validator,
)


class GitLabProject(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str | None = None
    path: str | None = None
    path_with_namespace: str | None = None
    web_url: str = ""

    @classmethod
    def from_api(cls, value: "GitLabProject | dict[str, Any]") -> "GitLabProject":
        return cls.model_validate(value)

    @model_validator(mode="after")
    def fill_display_defaults(self) -> "GitLabProject":
        fallback = str(self.id)
        name = self.name or fallback
        path = self.path or name
        path_with_namespace = self.path_with_namespace or path or fallback
        web_url = self.web_url or ""
        return self.model_copy(
            update={
                "name": name,
                "path": path,
                "path_with_namespace": path_with_namespace,
                "web_url": web_url,
            }
        )

    @property
    def display_path(self) -> str:
        return self.path_with_namespace or self.path or self.name or str(self.id)


@dataclass(frozen=True)
class RepositoryComparison:
    diffs: tuple[dict[str, Any], ...]
    incomplete: bool

    @property
    def changed_python_paths(self) -> tuple[str, ...]:
        paths = {
            path
            for diff in self.diffs
            for path in (diff.get("old_path"), diff.get("new_path"))
            if isinstance(path, str) and path.endswith(".py")
        }
        return tuple(sorted(paths))
