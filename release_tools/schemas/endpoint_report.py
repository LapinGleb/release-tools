import ast
from functools import total_ordering
from typing import (
    Any,
    Self,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})


def normalize_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Endpoint must use an internal path")
    if "://" in path:
        raise ValueError("Endpoint must use an internal path")
    normalized = "/" + "/".join(part for part in path.split("/") if part)
    return normalized or "/"


@total_ordering
class RouteKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str
    path: str

    @model_validator(mode="after")
    def normalize(self) -> Self:
        method = self.method.upper()
        if method not in HTTP_METHODS:
            raise ValueError(f"Unsupported HTTP method: {method}")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", normalize_path(self.path))
        return self

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RouteKey):
            return NotImplemented
        return (self.method, self.path) < (other.method, other.path)


class EndpointChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: RouteKey
    reason: str


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: RouteKey | None
    reason: str


class RouteDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: RouteKey
    module: str
    function: str
    request_models: tuple[str, ...] = ()
    response_model: str | None = None
    direct_calls: tuple[str, ...] = ()
    source: str = ""


class ChangedEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: RouteKey
    reason: str


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_endpoints: tuple[ChangedEndpoint, ...]

    @classmethod
    def from_payload(cls, payload: object, candidates: set[RouteKey]) -> "AnalysisResult":
        if not isinstance(payload, dict) or set(payload) != {"changed_endpoints"}:
            raise ValueError("Payload must contain only changed_endpoints")
        entries = payload["changed_endpoints"]
        if not isinstance(entries, list):
            raise ValueError("changed_endpoints must be a list")

        endpoints: list[ChangedEndpoint] = []
        seen: set[RouteKey] = set()
        for raw_entry in entries:
            entry = cls._validate_entry(raw_entry)
            key = RouteKey(method=entry["method"], path=entry["path"])
            if key.path == "/crm" or key.path.startswith("/crm/"):
                raise ValueError("Endpoint path must not include /crm")
            if key not in candidates:
                raise ValueError(f"Endpoint {key} is not among candidates")
            if key in seen:
                raise ValueError(f"Endpoint {key} is duplicate")
            seen.add(key)
            endpoints.append(ChangedEndpoint(key=key, reason=entry["reason"]))
        return cls(changed_endpoints=tuple(endpoints))

    @staticmethod
    def _validate_entry(raw_entry: object) -> dict[str, str]:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"method", "path", "reason"}:
            raise ValueError("Endpoint entry must contain method, path and reason")
        entry: dict[str, Any] = raw_entry
        if not all(isinstance(entry[key], str) for key in ("method", "path", "reason")):
            raise ValueError("Endpoint entry values must be strings")
        if not entry["reason"].strip():
            raise ValueError("Endpoint reason must not be empty")
        if "://" in entry["path"]:
            raise ValueError("Endpoint must use an internal path")
        return {
            "method": entry["method"],
            "path": entry["path"],
            "reason": entry["reason"].strip(),
        }


class ProjectReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    added: tuple[EndpointChange, ...] = ()
    changed: tuple[EndpointChange, ...] = ()
    removed: tuple[EndpointChange, ...] = ()
    review: tuple[EndpointChange | ReviewFinding, ...] = ()
    error: str | None = None

    @property
    def has_findings(self) -> bool:
        return bool(self.added or self.changed or self.removed or self.review)


class ChangeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: RouteKey
    diff: str
    symbols: tuple[str, ...]


class SymbolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    qualified_name: str
    source: str
    calls: tuple[tuple[str, str], ...]
    bindings: tuple[tuple[str, str], ...] = ()


class RouteSymbol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    module: str
    name: str


class RouterInclude(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: RouteSymbol
    prefix: str = ""


class RouteRegistryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definitions: dict[RouteKey, RouteDefinition] = Field(default_factory=dict)
    review_findings: tuple[ReviewFinding, ...] = ()


class ParsedRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    router: str
    method: str
    path: str
    function: ast.AsyncFunctionDef | ast.FunctionDef
    response_model: str | None


class ParsedModule(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    path: str
    tree: ast.Module
    source: str
    imports: dict[str, RouteSymbol] = Field(default_factory=dict)
    routers: dict[str, str] = Field(default_factory=dict)
    includes: dict[str, list[RouterInclude]] = Field(default_factory=dict)
    routes: list[ParsedRoute] = Field(default_factory=list)
    review_findings: list[ReviewFinding] = Field(default_factory=list)
