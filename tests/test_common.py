import pytest
from pydantic import ValidationError

from release_tools.commands.create_release_branches import parse_args
from release_tools.common import (
    CliOptionEnum,
    build_task_branch_candidates,
    create_eva_session,
    create_gitlab_session,
    normalize_release_branch,
    resolve_prefixes,
    runtime,
)
from release_tools.schemas import GitLabProject
from release_tools.settings.eva import get_eva_settings
from release_tools.settings.gitlab import get_gitlab_settings
from release_tools.settings.http import get_http_settings
from release_tools.settings.release import get_release_settings


@pytest.fixture(autouse=True)
def clear_settings_caches():
    get_eva_settings.cache_clear()
    get_gitlab_settings.cache_clear()
    get_release_settings.cache_clear()
    get_http_settings.cache_clear()
    yield
    get_eva_settings.cache_clear()
    get_gitlab_settings.cache_clear()
    get_release_settings.cache_clear()
    get_http_settings.cache_clear()


def test_normalize_release_branch_adds_release_prefix():
    assert normalize_release_branch("20260604") == "release/20260604"


def test_normalize_release_branch_keeps_existing_release_prefix():
    assert normalize_release_branch("release/20260604") == "release/20260604"


def test_resolve_prefixes_uses_defaults_when_cli_prefixes_are_missing():
    assert resolve_prefixes(None, ["feature", "bugfix", "hotfix"]) == [
        "feature",
        "bugfix",
        "hotfix",
    ]


def test_resolve_prefixes_uses_cli_prefixes_when_provided():
    assert resolve_prefixes(["feature", "hotfix"], ["feature", "bugfix", "hotfix"]) == [
        "feature",
        "hotfix",
    ]


def test_cli_option_values_work_with_argparse():
    args = parse_args(
        [
            CliOptionEnum.EVA_RELEASE,
            "REL-005063",
            CliOptionEnum.RELEASE,
            "20260618",
            CliOptionEnum.PREFIX,
            "feature",
        ]
    )

    assert args.eva_release == "REL-005063"
    assert args.release == "20260618"
    assert args.prefixes == ["feature"]


def test_build_task_branch_candidates_uses_task_order_then_prefix_order():
    assert build_task_branch_candidates(["CRM-1", "CRM-2"], ["feature", "/hotfix/"]) == [
        "feature/CRM-1",
        "hotfix/CRM-1",
        "feature/CRM-2",
        "hotfix/CRM-2",
    ]


def test_gitlab_project_accepts_minimal_api_shape():
    project = GitLabProject.from_api(
        {
            "id": "42",
            "name": "crm",
            "path": "crm",
            "path_with_namespace": "fo/crm",
            "web_url": "https://gitlab.example/fo/crm",
        }
    )

    assert project.id == 42
    assert project.display_path == "fo/crm"
    assert project.web_url == "https://gitlab.example/fo/crm"


def test_gitlab_project_rejects_invalid_id():
    with pytest.raises(ValidationError):
        GitLabProject.from_api({"id": "not-a-number"})


def test_gitlab_project_is_frozen():
    project = GitLabProject.from_api({"id": 42})

    with pytest.raises(ValidationError):
        project.name = "changed"


def test_gitlab_project_falls_back_to_id_for_missing_optional_paths():
    project = GitLabProject.from_api({"id": 42})

    assert project.name == "42"
    assert project.path == "42"
    assert project.path_with_namespace == "42"
    assert project.web_url == ""
    assert project.display_path == "42"


class FakeAiohttp:
    class ClientTimeout:
        def __init__(self, total):
            self.total = total

    class TCPConnector:
        def __init__(self, ssl):
            self.ssl = ssl

    class ClientSession:
        def __init__(self, *, connector, headers, timeout):
            self.connector = connector
            self.headers = headers
            self.timeout = timeout


def test_create_gitlab_session_raises_when_token_is_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EVA_TOKEN=eva-token\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.delenv("EVA_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setattr(runtime, "aiohttp", FakeAiohttp)

    with pytest.raises(RuntimeError, match="GITLAB_TOKEN"):
        create_gitlab_session()


def test_create_sessions_load_tokens_from_env(monkeypatch):
    monkeypatch.setenv("EVA_TOKEN", "eva-token")
    monkeypatch.setenv("GITLAB_TOKEN", "gitlab-token")
    monkeypatch.setenv("HTTP_VERIFY_SSL", "false")
    monkeypatch.setattr(runtime, "aiohttp", FakeAiohttp)

    eva_session = create_eva_session()
    gitlab_session = create_gitlab_session()

    assert eva_session.headers == {"Authorization": "Bearer eva-token"}
    assert gitlab_session.headers == {"PRIVATE-TOKEN": "gitlab-token"}
    assert eva_session.timeout.total == 300
    assert not eva_session.connector.ssl


def test_create_sessions_load_tokens_from_pydantic_settings_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EVA_TOKEN=eva-from-file\nGITLAB_TOKEN=gitlab-from-file\nHTTP_VERIFY_SSL=false\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.delenv("EVA_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("HTTP_VERIFY_SSL", raising=False)
    monkeypatch.setattr(runtime, "aiohttp", FakeAiohttp)

    eva_session = create_eva_session()
    gitlab_session = create_gitlab_session()

    assert eva_session.headers == {"Authorization": "Bearer eva-from-file"}
    assert gitlab_session.headers == {"PRIVATE-TOKEN": "gitlab-from-file"}


def test_create_sessions_prefer_environment_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EVA_TOKEN=eva-from-file\nGITLAB_TOKEN=gitlab-from-file\nHTTP_VERIFY_SSL=true\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("EVA_TOKEN", "eva-from-env")
    monkeypatch.setenv("GITLAB_TOKEN", "gitlab-from-env")
    monkeypatch.setenv("HTTP_VERIFY_SSL", "false")
    monkeypatch.setattr(runtime, "aiohttp", FakeAiohttp)

    eva_session = create_eva_session()
    gitlab_session = create_gitlab_session()

    assert eva_session.headers == {"Authorization": "Bearer eva-from-env"}
    assert gitlab_session.headers == {"PRIVATE-TOKEN": "gitlab-from-env"}


def test_domain_settings_load_overrides_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EVA_BASE_URL=https://eva.example.test",
                "GITLAB_BASE_URL=https://gitlab.example.test",
                "GITLAB_GROUP=bar",
                "RELEASE_BASE_BRANCH=main",
                "RELEASE_PREFIXES=feature,bugfix,hotfix",
                "HTTP_TIMEOUT_SECONDS=42",
                "HTTP_VERIFY_SSL=false",
            ]
        )
    )
    monkeypatch.setenv("ENV_FILE", str(env_file))
    get_eva_settings.cache_clear()
    get_gitlab_settings.cache_clear()
    get_release_settings.cache_clear()
    get_http_settings.cache_clear()

    assert get_eva_settings().BASE_URL == "https://eva.example.test"
    assert get_gitlab_settings().BASE_URL == "https://gitlab.example.test"
    assert get_gitlab_settings().GROUP == "bar"
    assert get_release_settings().BASE_BRANCH == "main"
    assert get_release_settings().PREFIXES == ["feature", "bugfix", "hotfix"]
    assert get_http_settings().TIMEOUT_SECONDS == 42
    assert not get_http_settings().VERIFY_SSL
