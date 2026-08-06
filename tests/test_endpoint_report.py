import ast
import io
import json
import tarfile
import tomllib
from pathlib import Path

import pytest
from pydantic import (
    BaseModel,
    ValidationError,
)
from pydantic_settings import BaseSettings

from release_tools.commands.create_endpoint_report import (
    parse_args,
    print_progress,
    report_exit_code,
    write_report,
)
from release_tools.endpoint_report import llm_client
from release_tools.endpoint_report.archive_source import ArchiveSource
from release_tools.endpoint_report.change_context import ChangeContextBuilder
from release_tools.endpoint_report.reporting import render_report
from release_tools.endpoint_report.route_registry import RouteRegistry
from release_tools.endpoint_report.service import EndpointReportService
from release_tools.schemas import (
    AnalysisResult,
    ChangeCandidate,
    ChangedEndpoint,
    EndpointChange,
    GitLabProject,
    ProjectReport,
    ReviewFinding,
    RouteDefinition,
    RouteKey,
)
from release_tools.schemas.endpoint_report import (
    ParsedModule,
    ParsedRoute,
    RouteSymbol,
    SymbolDefinition,
)
from release_tools.settings import (
    LLMSettings,
    get_llm_settings,
)


class MemorySource:
    def __init__(self, revisions: dict[str, dict[str, str]]) -> None:
        self.revisions = revisions

    def list_python_files(self, revision: str) -> tuple[str, ...]:
        return tuple(sorted(self.revisions[revision]))

    def read_file(self, revision: str, path: str) -> str:
        return self.revisions[revision][path]

    def read_files(self, revision: str, paths: tuple[str, ...]) -> dict[str, str]:
        return {path: self.revisions[revision][path] for path in paths}


@pytest.mark.parametrize(
    "model",
    [
        RouteKey,
        EndpointChange,
        ReviewFinding,
        RouteDefinition,
        ChangedEndpoint,
        AnalysisResult,
        ProjectReport,
        ChangeCandidate,
        SymbolDefinition,
        RouteSymbol,
        ParsedRoute,
        ParsedModule,
    ],
)
def test_endpoint_report_schemas_are_pydantic_models(model: type[BaseModel]) -> None:
    assert issubclass(model, BaseModel)


def test_llm_settings_are_pydantic_settings() -> None:
    assert issubclass(LLMSettings, BaseSettings)


def test_endpoint_report_package_contains_no_dataclasses_or_legacy_models_module() -> None:
    package = Path("release_tools/endpoint_report")

    assert not (package / "models.py").exists()
    assert all("dataclass" not in path.read_text(encoding="utf-8") for path in package.glob("*.py"))


def test_endpoint_report_does_not_reexport_models_from_legacy_modules() -> None:
    from release_tools.endpoint_report import change_context

    assert not hasattr(change_context, "ChangeCandidate")
    assert not hasattr(llm_client, "LLMSettings")


def test_llm_settings_are_mutable() -> None:
    settings = LLMSettings(
        api_key="key",
        base_url="https://llm.example/v1",
        model="model",
    )

    settings.model = "another-model"

    assert settings.model == "another-model"


def test_regular_endpoint_report_models_are_mutable() -> None:
    report = ProjectReport(project="fo/partners")

    report.error = "analysis failed"

    assert report.error == "analysis failed"


def test_route_key_is_frozen_hashable_and_ordered() -> None:
    key = RouteKey(method="get", path="//partners//{id}/")

    assert key == RouteKey(method="GET", path="/partners/{id}")
    assert hash(key) == hash(RouteKey(method="GET", path="/partners/{id}"))
    assert sorted((RouteKey(method="POST", path="/a"), key)) == [
        key,
        RouteKey(method="POST", path="/a"),
    ]
    with pytest.raises(ValidationError):
        key.path = "/changed"


def test_route_symbol_is_frozen_and_hashable() -> None:
    symbol = RouteSymbol(module="src/api.py", name="router")

    assert symbol in {symbol}
    with pytest.raises(ValidationError):
        symbol.name = "another_router"


def test_parsed_module_has_independent_mutable_defaults() -> None:
    first = ParsedModule(path="first.py", tree=ast.parse(""), source="")
    second = ParsedModule(path="second.py", tree=ast.parse(""), source="")

    first.routers["router"] = "/v1"

    assert second.routers == {}


def test_route_definition_equality_includes_source() -> None:
    key = RouteKey(method="GET", path="/partners")
    common = {
        "key": key,
        "module": "src/entrypoints/api/__init__.py",
        "function": "partners",
    }

    assert RouteDefinition(**common, source="old") != RouteDefinition(**common, source="new")


def test_cli_requires_head_base_and_project() -> None:
    with pytest.raises(SystemExit):
        parse_args([])


def test_cli_deduplicates_projects_and_uses_defaults() -> None:
    args = parse_args(
        [
            "--head",
            "release/20260730",
            "--base",
            "master",
            "--project",
            "external-clients",
            "--project",
            "external-clients",
            "--project",
            "payment",
        ]
    )

    assert args.head == "release/20260730"
    assert args.base == "master"
    assert args.projects == ["external-clients", "payment"]
    assert args.output == Path("endpoint-changes.txt")
    assert args.concurrency == 3
    assert args.max_context_chars == 60_000


@pytest.mark.parametrize("option", ["--concurrency", "--max-context-chars"])
def test_cli_rejects_non_positive_limits(option: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--head",
                "release/1",
                "--base",
                "master",
                "--project",
                "payment",
                option,
                "0",
            ]
        )


def test_cli_prints_progress_to_stderr(capsys) -> None:
    print_progress("fo/crm", "LLM: 2/7")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[fo/crm] LLM: 2/7\n"


def test_txt_report_preserves_methods_reasons_and_stable_order() -> None:
    report = ProjectReport(
        project="fo/external-clients",
        added=(
            EndpointChange(
                key=RouteKey(method="POST", path="/partners"),
                reason="добавлен маршрут",
            ),
            EndpointChange(
                key=RouteKey(method="GET", path="/partners"),
                reason="добавлено чтение",
            ),
        ),
        changed=(
            EndpointChange(
                key=RouteKey(method="GET", path="/partners/{id}"),
                reason="добавлено поле status",
            ),
        ),
        removed=(
            EndpointChange(
                key=RouteKey(method="DELETE", path="/partners/{id}"),
                reason="маршрут отсутствует в head",
            ),
        ),
        review=(
            EndpointChange(
                key=RouteKey(method="PATCH", path="/partners/{id}"),
                reason="динамическая зависимость",
            ),
        ),
    )

    content = render_report("master", "release/20260730", (report,))

    assert content == (
        "Endpoint changes\n"
        "BASE: master\n"
        "HEAD: release/20260730\n"
        "\n"
        "[fo/external-clients]\n"
        "\n"
        "ADDED\n"
        "- GET /partners — добавлено чтение\n"
        "- POST /partners — добавлен маршрут\n"
        "\n"
        "CHANGED\n"
        "- GET /partners/{id} — добавлено поле status\n"
        "\n"
        "REMOVED\n"
        "- DELETE /partners/{id} — маршрут отсутствует в head\n"
        "\n"
        "REVIEW\n"
        "- PATCH /partners/{id} — динамическая зависимость\n"
    )


def test_txt_report_shows_no_changes_and_errors() -> None:
    content = render_report(
        "master",
        "release/20260730",
        (
            ProjectReport(project="fo/payment"),
            ProjectReport(project="fo/billing", error="branch release/20260730 not found"),
        ),
    )

    assert "[fo/payment]\n\nNO ENDPOINT CHANGES" in content
    assert "[fo/billing]" not in content
    assert "ERRORS\n- fo/billing — branch release/20260730 not found" in content


def test_txt_report_marks_unresolved_dynamic_route_as_unknown_review() -> None:
    content = render_report(
        "master",
        "release/1",
        (
            ProjectReport(
                project="fo/payment",
                review=(ReviewFinding(key=None, reason="dynamic route path cannot be resolved"),),
            ),
        ),
    )

    assert "REVIEW\n- UNKNOWN — dynamic route path cannot be resolved" in content


def test_write_report_replaces_output_and_exit_code_marks_review(tmp_path: Path) -> None:
    output = tmp_path / "endpoint-changes.txt"
    output.write_text("old", encoding="utf-8")
    reports = (
        ProjectReport(
            project="fo/payment",
            review=(
                EndpointChange(
                    key=RouteKey(method="GET", path="/payments"),
                    reason="ambiguous dependency",
                ),
            ),
        ),
    )

    write_report(output, "new")

    assert output.read_text(encoding="utf-8") == "new"
    assert report_exit_code(reports) == 1
    assert report_exit_code((ProjectReport(project="fo/payment"),)) == 0


def test_endpoint_report_console_script_is_registered() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["create-endpoint-report"]
        == "release_tools.commands.create_endpoint_report:main"
    )


def test_route_registry_applies_include_prefix() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter\n"
                    "from src.entrypoints.api.v1 import router as v1_router\n"
                    "api_router = APIRouter(prefix='/api')\n"
                    "api_router.include_router(v1_router, prefix='/v1')\n"
                ),
                "src/entrypoints/api/v1.py": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter(prefix='/partners')\n"
                    "@router.get('')\n"
                    "async def list_partners():\n"
                    "    return []\n"
                    "@router.post('')\n"
                    "async def create_partner():\n"
                    "    return {}\n"
                ),
            }
        }
    )

    result = RouteRegistry.build(source, "head")

    assert set(result.definitions) == {
        RouteKey(method="GET", path="/api/v1/partners"),
        RouteKey(method="POST", path="/api/v1/partners"),
    }
    assert result.review_findings == ()


def test_route_registry_reports_dynamic_paths_for_review_and_keeps_static_routes() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter\n"
                    "api_router = APIRouter()\n"
                    "PATH = '/dynamic'\n"
                    "@api_router.get(PATH)\n"
                    "async def dynamic():\n"
                    "    return {}\n"
                    "@api_router.get('/static')\n"
                    "async def static():\n"
                    "    return {}\n"
                )
            }
        }
    )

    result = RouteRegistry.build(source, "head")

    assert set(result.definitions) == {RouteKey(method="GET", path="/static")}
    assert result.review_findings == (
        ReviewFinding(key=None, reason="Route path must be a literal in src/entrypoints/api/__init__.py:4"),
    )


def test_route_registry_applies_nested_include_prefixes() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter\n"
                    "from src.entrypoints.api.v1 import router as v1_router\n"
                    "api_router = APIRouter(prefix='/api')\n"
                    "api_router.include_router(v1_router, prefix='/v1')\n"
                ),
                "src/entrypoints/api/v1.py": (
                    "from fastapi import APIRouter\n"
                    "from src.entrypoints.api.partners import router as partners_router\n"
                    "router = APIRouter()\n"
                    "router.include_router(partners_router, prefix='/partners')\n"
                ),
                "src/entrypoints/api/partners.py": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter()\n"
                    "@router.get('/{id}')\n"
                    "async def partner():\n"
                    "    return {}\n"
                ),
            }
        }
    )

    result = RouteRegistry.build(source, "head")

    assert set(result.definitions) == {RouteKey(method="GET", path="/api/v1/partners/{id}")}


def test_route_registry_reports_dynamic_include_prefix_and_unresolved_router() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter\n"
                    "from src.entrypoints.api.missing import router as missing_router\n"
                    "api_router = APIRouter()\n"
                    "PREFIX = '/v1'\n"
                    "api_router.include_router(missing_router, prefix=PREFIX)\n"
                    "api_router.include_router(missing_router)\n"
                    "@api_router.get('/static')\n"
                    "async def static():\n"
                    "    return {}\n"
                ),
            }
        }
    )

    result = RouteRegistry.build(source, "head")

    assert set(result.definitions) == {RouteKey(method="GET", path="/static")}
    assert result.review_findings == (
        ReviewFinding(
            key=None,
            reason="Cannot resolve router src/entrypoints/api/missing.py:router",
        ),
        ReviewFinding(key=None, reason="Router prefix must be a literal in src/entrypoints/api/__init__.py:5"),
    )


def test_route_registry_keeps_syntax_errors_fatal() -> None:
    source = MemorySource({"head": {"src/entrypoints/api/__init__.py": "api_router = APIRouter(\n"}})

    with pytest.raises(Exception, match="Cannot parse"):
        RouteRegistry.build(source, "head")


def test_route_registry_keeps_include_cycles_fatal() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter\n"
                    "api_router = APIRouter()\n"
                    "api_router.include_router(api_router)\n"
                )
            }
        }
    )

    with pytest.raises(Exception, match="Router include cycle"):
        RouteRegistry.build(source, "head")


def test_route_registry_separates_depends_from_request_models() -> None:
    source = MemorySource(
        {
            "head": {
                "src/entrypoints/api/__init__.py": (
                    "from fastapi import APIRouter, Depends\n"
                    "api_router = APIRouter()\n"
                    "@api_router.post('/partners', response_model=list[Partner])\n"
                    "async def partners(payload: PartnerRequest, settings: AppSettings = Depends()):\n"
                    "    return await settings.load()\n"
                )
            }
        }
    )

    route = RouteRegistry.build(source, "head").definitions[RouteKey(method="POST", path="/partners")]

    assert route.request_models == ("PartnerRequest",)
    assert route.response_model == "Partner"
    assert route.direct_calls == ("AppSettings.load",)


def test_change_context_follows_dependency_service_and_repository() -> None:
    base_files = {
        "src/entrypoints/api/__init__.py": (
            "from fastapi import APIRouter, Depends\n"
            "from src.modules.partners import PartnerService\n"
            "api_router = APIRouter()\n"
            "@api_router.get('/partners')\n"
            "async def partners(service: PartnerService = Depends()):\n"
            "    return await service.list()\n"
        ),
        "src/modules/partners.py": (
            "class PartnerService:\n"
            "    def __init__(self, repository: PartnerRepository):\n"
            "        self.repository = repository\n"
            "    async def list(self):\n"
            "        return await self.repository.list()\n"
        ),
        "src/repositories/partners.py": (
            "class PartnerRepository:\n" "    async def list(self):\n" "        return await self.fetch(active=True)\n"
        ),
    }
    head_files = dict(base_files)
    head_files["src/repositories/partners.py"] = (
        "class PartnerRepository:\n" "    async def list(self):\n" "        return await self.fetch(active=False)\n"
    )
    source = MemorySource({"base": base_files, "head": head_files})
    base_routes = RouteRegistry.build(source, "base").definitions
    head_routes = RouteRegistry.build(source, "head").definitions

    result = ChangeContextBuilder(source).build("base", "head", base_routes, head_routes)

    assert len(result) == 1
    assert result[0].key == RouteKey(method="GET", path="/partners")
    assert "active=False" in result[0].diff


def test_change_context_does_not_follow_nested_response_models() -> None:
    base_files = {
        "src/entrypoints/api/__init__.py": (
            "from fastapi import APIRouter\n"
            "api_router = APIRouter()\n"
            "@api_router.get('/partners', response_model=PartnerResponse)\n"
            "async def partners():\n"
            "    return {}\n"
        ),
        "src/schemas/partners.py": (
            "class Address(BaseModel):\n"
            "    city: str\n"
            "class PartnerResponse(BaseModel):\n"
            "    address: Address\n"
        ),
    }
    head_files = dict(base_files)
    head_files["src/schemas/partners.py"] = (
        "class Address(BaseModel):\n"
        "    city: str\n"
        "    country: str\n"
        "class PartnerResponse(BaseModel):\n"
        "    address: Address\n"
    )
    source = MemorySource({"base": base_files, "head": head_files})

    result = ChangeContextBuilder(source).build(
        "base",
        "head",
        RouteRegistry.build(source, "base").definitions,
        RouteRegistry.build(source, "head").definitions,
    )

    assert result == ()


def test_change_context_ignores_app_settings_dependency_change() -> None:
    base_files = {
        "src/entrypoints/api/__init__.py": (
            "from fastapi import APIRouter, Depends\n"
            "api_router = APIRouter()\n"
            "@api_router.get('/partners')\n"
            "async def partners(settings: AppSettings = Depends()):\n"
            "    return settings.VALUE\n"
        ),
        "src/settings/app.py": "class AppSettings:\n    VALUE = 1\n",
    }
    head_files = dict(base_files)
    head_files["src/settings/app.py"] = "class AppSettings:\n    VALUE = 2\n"
    source = MemorySource({"base": base_files, "head": head_files})

    result = ChangeContextBuilder(source).build(
        "base",
        "head",
        RouteRegistry.build(source, "base").definitions,
        RouteRegistry.build(source, "head").definitions,
    )

    assert result == ()


def test_change_context_includes_handler_only_change() -> None:
    base_files = {
        "src/entrypoints/api/__init__.py": (
            "from fastapi import APIRouter\n"
            "api_router = APIRouter()\n"
            "@api_router.get('/partners')\n"
            "async def partners():\n"
            "    return 1\n"
        )
    }
    head_files = {
        "src/entrypoints/api/__init__.py": base_files["src/entrypoints/api/__init__.py"].replace("return 1", "return 2")
    }
    source = MemorySource({"base": base_files, "head": head_files})

    result = ChangeContextBuilder(source).build(
        "base",
        "head",
        RouteRegistry.build(source, "base").definitions,
        RouteRegistry.build(source, "head").definitions,
    )

    assert len(result) == 1
    assert result[0].key == RouteKey(method="GET", path="/partners")
    assert "return 2" in result[0].diff


def test_archive_source_strips_gitlab_archive_root() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        content = b"VALUE = 1\n"
        info = tarfile.TarInfo("project-deadbeef/src/example.py")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))

    source = ArchiveSource.from_archives({"head": buffer.getvalue()})

    assert source.list_python_files("head") == ("src/example.py",)
    assert source.read_file("head", "src/example.py") == "VALUE = 1\n"


def test_llm_result_rejects_invented_endpoint() -> None:
    candidates = {RouteKey(method="GET", path="/partners")}

    with pytest.raises(ValueError, match="not among candidates"):
        AnalysisResult.from_payload(
            {
                "changed_endpoints": [
                    {
                        "method": "POST",
                        "path": "/invented",
                        "reason": "invented",
                    }
                ]
            },
            candidates,
        )


def test_llm_settings_load_from_env_file(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_KEY=file-key\n" "LLM_BASE_URL=https://llm.example/v1\n" "LLM_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_FILE", str(env_file))
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    get_llm_settings.cache_clear()
    settings = get_llm_settings()

    assert settings.api_key == "file-key"
    assert settings.base_url == "https://llm.example/v1"
    assert settings.model == "file-model"


def test_llm_settings_prefer_process_environment_over_env_file(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_KEY=file-key\n" "LLM_BASE_URL=https://file.example/v1\n" "LLM_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")

    get_llm_settings.cache_clear()
    settings = get_llm_settings()

    assert settings.api_key == "env-key"
    assert settings.base_url == "https://env.example/v1"
    assert settings.model == "env-model"
    assert settings.timeout_seconds == 60
    assert settings.max_attempts == 3


def test_llm_settings_report_missing_required_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    get_llm_settings.cache_clear()

    with pytest.raises(ValueError, match="Missing required variables: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL"):
        get_llm_settings()


def test_llm_settings_reject_whitespace_required_variables() -> None:
    with pytest.raises(ValueError, match="Missing required variables: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL"):
        LLMSettings(api_key="  ", base_url="\t", model="\n")


def test_llm_result_allows_empty_changed_endpoints() -> None:
    result = AnalysisResult.from_payload(
        {"changed_endpoints": []},
        {RouteKey(method="GET", path="/partners")},
    )

    assert result.changed_endpoints == ()


def test_llm_client_sends_one_candidate_and_returns_changed_endpoint(monkeypatch) -> None:
    candidate = ChangeCandidate(
        key=RouteKey(method="GET", path="/one"),
        diff="repository diff",
        symbols=("Service.list",),
    )
    requests = []

    class FakeLLMResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "changed_endpoints": [
                                            {
                                                "method": "GET",
                                                "path": "/one",
                                                "reason": "изменён ответ",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, timeout):
        requests.append(request.data.decode())
        return FakeLLMResponse()

    monkeypatch.setattr(llm_client, "urlopen", fake_urlopen)
    client = llm_client.OpenAICompatibleClient(
        LLMSettings(
            api_key="key",
            base_url="https://llm.example/v1",
            model="model",
        ),
        sleep=lambda _: None,
    )

    result = client.classify(candidate)

    assert result.changed_endpoints[0].key == candidate.key
    assert result.changed_endpoints[0].reason == "изменён ответ"
    assert len(requests) == 1
    assert requests[0].count("repository diff") == 1
    assert "GET /one" in requests[0]


class FakeReportGitLab:
    group = "fo"

    def __init__(self):
        self.archive_refs = []

    async def get_all_projects(self):
        return [
            GitLabProject.from_api({"id": 1, "path": "external-clients", "path_with_namespace": "fo/external-clients"}),
            GitLabProject.from_api({"id": 2, "path": "payment", "path_with_namespace": "fo/payment"}),
        ]

    async def compare_repository(self, project_id, base, head):
        from release_tools.schemas import RepositoryComparison

        return RepositoryComparison(
            diffs=({"new_path": "src/entrypoints/api/__init__.py", "old_path": "src/entrypoints/api/__init__.py"},),
            incomplete=False,
        )

    async def get_merge_base(self, project_id, base, head):
        return "merge-base-sha"

    async def download_repository_archive(self, project_id, ref):
        self.archive_refs.append(ref)
        route = "get" if ref == "merge-base-sha" else "post"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            content = (
                "from fastapi import APIRouter\n"
                "api_router = APIRouter()\n"
                f"@api_router.{route}('/partners')\n"
                "async def partners():\n"
                "    return {}\n"
            ).encode()
            info = tarfile.TarInfo("project-sha/src/entrypoints/api/__init__.py")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return buffer.getvalue()


class UnexpectedLLM:
    def classify(self, candidates):
        raise AssertionError("Added and removed routes must not use LLM")


class NoPythonChangesGitLab(FakeReportGitLab):
    async def compare_repository(self, project_id, base, head):
        from release_tools.schemas import RepositoryComparison

        return RepositoryComparison(diffs=({"new_path": "README.md", "old_path": "README.md"},), incomplete=False)

    async def get_merge_base(self, project_id, base, head):
        raise AssertionError("merge base must not be requested when Python files did not change")


class DynamicRouteGitLab(FakeReportGitLab):
    async def download_repository_archive(self, project_id, ref):
        self.archive_refs.append(ref)
        dynamic_route = ""
        if ref != "merge-base-sha":
            dynamic_route = "PATH = '/dynamic'\n" "@api_router.get(PATH)\n" "async def dynamic():\n" "    return {}\n"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            content = (
                "from fastapi import APIRouter\n"
                "api_router = APIRouter()\n"
                "@api_router.get('/static')\n"
                "async def static():\n"
                "    return {}\n"
                f"{dynamic_route}"
            ).encode()
            info = tarfile.TarInfo("project-sha/src/entrypoints/api/__init__.py")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return buffer.getvalue()


@pytest.mark.asyncio
async def test_service_selects_exact_projects_and_reports_added_removed() -> None:
    gitlab = FakeReportGitLab()
    service = EndpointReportService(gitlab, UnexpectedLLM(), concurrency=2)

    reports = await service.generate("master", "release/1", ("external-clients", "missing"))

    assert reports[0].project == "fo/external-clients"
    assert reports[0].added[0].key == RouteKey(method="POST", path="/partners")
    assert reports[0].removed[0].key == RouteKey(method="GET", path="/partners")
    assert reports[1] == ProjectReport(project="fo/missing", error="project not found")
    assert gitlab.archive_refs == ["merge-base-sha", "release/1"]


@pytest.mark.asyncio
async def test_service_skips_merge_base_and_archives_without_python_changes() -> None:
    gitlab = NoPythonChangesGitLab()
    service = EndpointReportService(gitlab, UnexpectedLLM())

    reports = await service.generate("master", "release/1", ("external-clients",))

    assert reports == (ProjectReport(project="fo/external-clients"),)
    assert gitlab.archive_refs == []


@pytest.mark.asyncio
async def test_service_keeps_static_routes_and_reports_dynamic_routes_for_review() -> None:
    service = EndpointReportService(DynamicRouteGitLab(), UnexpectedLLM())

    reports = await service.generate("master", "release/1", ("external-clients",))

    assert reports[0].error is None
    assert reports[0].added == ()
    assert reports[0].removed == ()
    assert reports[0].review == (
        ReviewFinding(
            key=None,
            reason="Route path must be a literal in src/entrypoints/api/__init__.py:7",
        ),
    )
    assert report_exit_code(reports) == 1


@pytest.mark.asyncio
async def test_service_reports_analysis_stage_progress() -> None:
    progress = []
    service = EndpointReportService(
        FakeReportGitLab(),
        UnexpectedLLM(),
        progress=lambda project, message: progress.append((project, message)),
    )

    await service.generate("master", "release/1", ("external-clients",))

    messages = [message for project, message in progress if project == "fo/external-clients"]
    assert messages == [
        "compare: 1 files",
        "archives loaded",
        "routes: 1",
        "candidates: 0",
        "LLM: 0/0",
        "completed",
    ]
