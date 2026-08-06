import pytest

from release_tools.clients.eva import EvaClient
from release_tools.clients.gitlab import GitLabClient
from release_tools.settings.gitlab import get_gitlab_settings


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        json_data=None,
        text_data: str = "",
        bytes_data: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self._bytes_data = bytes_data
        self.headers = headers or {"Content-Type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data

    async def read(self):
        return self._bytes_data


class FakeGitLabSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.get_responses: list[FakeResponse] = []
        self.post_responses: list[FakeResponse] = []

    def get(self, url, params=None):
        self.get_calls.append((url, params))
        return self.get_responses.pop(0)

    def post(self, url, params=None, json=None):
        self.post_calls.append((url, params, json))
        return self.post_responses.pop(0)


class InvalidJsonResponse(FakeResponse):
    async def json(self):
        raise ValueError("invalid JSON")


class FakeEvaSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.post_calls = []

    def post(self, url, json=None):
        self.post_calls.append((url, json))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def clear_settings(monkeypatch):
    monkeypatch.setenv("GITLAB_PER_PAGE", "2")
    get_gitlab_settings.cache_clear()
    yield
    get_gitlab_settings.cache_clear()


@pytest.mark.asyncio
async def test_gitlab_client_paginates_projects(monkeypatch):
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(200, json_data=[{"id": 1}, {"id": 2}]),
        FakeResponse(200, json_data=[{"id": 3}]),
        FakeResponse(200, json_data=[]),
    ]
    client = GitLabClient(session, "https://gitlab.example/", "fo/backend")

    projects = await client.get_all_projects()

    assert [project.id for project in projects] == [1, 2, 3]
    assert session.get_calls[0][0] == "https://gitlab.example/api/v4/groups/fo%2Fbackend/projects"
    assert [call[1]["page"] for call in session.get_calls] == ["1", "2", "3"]
    assert [call[1]["per_page"] for call in session.get_calls] == ["2", "2", "2"]


@pytest.mark.asyncio
async def test_gitlab_client_branch_exists_handles_200_and_404():
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(200),
        FakeResponse(404),
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    assert await client.branch_exists(42, "feature/CRM-1")
    assert not await client.branch_exists(42, "feature/CRM-2")
    assert session.get_calls[0][0].endswith("/repository/branches/feature%2FCRM-1")


@pytest.mark.asyncio
async def test_gitlab_client_branch_exists_raises_for_unexpected_status():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(500, text_data="broken")]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    with pytest.raises(RuntimeError, match="GitLab branch check failed"):
        await client.branch_exists(42, "feature/CRM-1")


@pytest.mark.asyncio
async def test_gitlab_client_create_branch_posts_branch_and_ref():
    session = FakeGitLabSession()
    session.post_responses = [FakeResponse(201)]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    await client.create_branch(42, "release/20260604", "master")

    assert session.post_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/repository/branches",
            {"branch": "release/20260604", "ref": "master"},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_gitlab_client_finds_open_merge_request_by_source_and_target():
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(
            200,
            json_data=[
                {
                    "id": 1001,
                    "iid": 7,
                    "web_url": "https://gitlab.example/fo/crm/-/merge_requests/7",
                }
            ],
        )
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    merge_request = await client.find_open_merge_request(42, "release/20260604", "master")

    assert merge_request == {
        "id": 1001,
        "iid": 7,
        "web_url": "https://gitlab.example/fo/crm/-/merge_requests/7",
    }
    assert session.get_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/merge_requests",
            {
                "state": "opened",
                "source_branch": "release/20260604",
                "target_branch": "master",
                "per_page": "1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_gitlab_client_returns_none_when_open_merge_request_is_missing():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(200, json_data=[])]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    assert await client.find_open_merge_request(42, "release/20260604", "master") is None


@pytest.mark.asyncio
async def test_gitlab_client_creates_merge_request():
    session = FakeGitLabSession()
    session.post_responses = [
        FakeResponse(
            201,
            json_data={
                "id": 1001,
                "iid": 7,
                "web_url": "https://gitlab.example/fo/crm/-/merge_requests/7",
            },
        )
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    merge_request = await client.create_merge_request(
        42,
        source_branch="release/20260604",
        target_branch="master",
        title="Merge release/20260604 into master",
    )

    assert merge_request["web_url"] == "https://gitlab.example/fo/crm/-/merge_requests/7"
    assert session.post_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/merge_requests",
            None,
            {
                "source_branch": "release/20260604",
                "target_branch": "master",
                "title": "Merge release/20260604 into master",
            },
        )
    ]


@pytest.mark.asyncio
async def test_gitlab_client_lists_branch_names_with_pagination():
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(200, json_data=[{"name": "feature/CRM-1"}, {"name": "master"}]),
        FakeResponse(200, json_data=[]),
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    branches = await client.get_branch_names(42)

    assert branches == {"feature/CRM-1", "master"}
    assert session.get_calls[0][0] == "https://gitlab.example/api/v4/projects/42/repository/branches"
    assert [call[1]["page"] for call in session.get_calls] == ["1", "2"]


@pytest.mark.asyncio
async def test_gitlab_client_compares_from_merge_base():
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(
            200,
            json_data={
                "compare_timeout": False,
                "diffs": [
                    {
                        "old_path": "src/api.py",
                        "new_path": "src/api.py",
                        "new_file": False,
                        "deleted_file": False,
                        "renamed_file": False,
                        "too_large": False,
                        "diff": "@@ changed",
                    }
                ],
            },
        )
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    comparison = await client.compare_repository(42, "master", "release/1")

    assert not comparison.incomplete
    assert comparison.changed_python_paths == ("src/api.py",)
    assert session.get_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/repository/compare",
            {
                "from": "master",
                "to": "release/1",
                "straight": "false",
                "unidiff": "true",
            },
        )
    ]


@pytest.mark.asyncio
async def test_gitlab_client_marks_timed_out_or_large_compare_incomplete():
    session = FakeGitLabSession()
    session.get_responses = [
        FakeResponse(
            200,
            json_data={
                "compare_timeout": True,
                "diffs": [{"new_path": "src/api.py", "old_path": "src/api.py", "too_large": True}],
            },
        )
    ]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    comparison = await client.compare_repository(42, "master", "release/1")

    assert comparison.incomplete


@pytest.mark.asyncio
async def test_gitlab_client_gets_merge_base_sha():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(200, json_data={"id": "merge-base-sha"})]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    merge_base = await client.get_merge_base(42, "master", "release/1")

    assert merge_base == "merge-base-sha"
    assert session.get_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/repository/merge_base",
            {"refs[]": ["master", "release/1"]},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"id": ""}, [], "invalid"])
async def test_gitlab_client_rejects_merge_base_without_sha(payload):
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(200, json_data=payload)]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    with pytest.raises(RuntimeError, match="invalid merge base response"):
        await client.get_merge_base(42, "master", "release/1")


@pytest.mark.asyncio
async def test_gitlab_client_rejects_malformed_merge_base_json():
    session = FakeGitLabSession()
    session.get_responses = [InvalidJsonResponse(200)]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    with pytest.raises(RuntimeError, match="invalid merge base response"):
        await client.get_merge_base(42, "master", "release/1")


@pytest.mark.asyncio
async def test_gitlab_client_reports_missing_merge_base_ref():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(404, text_data="not found")]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    with pytest.raises(RuntimeError, match="master.*release/1"):
        await client.get_merge_base(42, "master", "release/1")


@pytest.mark.asyncio
async def test_gitlab_client_downloads_repository_archive_for_exact_ref():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(200, bytes_data=b"archive")]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    content = await client.download_repository_archive(42, "release/1")

    assert content == b"archive"
    assert session.get_calls == [
        (
            "https://gitlab.example/api/v4/projects/42/repository/archive.tar.gz",
            {"sha": "release/1", "include_lfs_blobs": "false"},
        )
    ]


@pytest.mark.asyncio
async def test_gitlab_client_reports_missing_compare_ref():
    session = FakeGitLabSession()
    session.get_responses = [FakeResponse(404, text_data="not found")]
    client = GitLabClient(session, "https://gitlab.example", "fo")

    with pytest.raises(RuntimeError, match="master.*release/1"):
        await client.compare_repository(42, "master", "release/1")


@pytest.mark.asyncio
async def test_eva_client_raises_for_redirect():
    session = FakeEvaSession(
        [
            FakeResponse(
                302,
                text_data="<html>login</html>",
                headers={"Location": "/login", "Content-Type": "text/html"},
            )
        ]
    )
    client = EvaClient(session, "https://eva.example")

    with pytest.raises(RuntimeError, match="redirected to '/login'"):
        await client.call("CmfList.get")


@pytest.mark.asyncio
async def test_eva_client_raises_for_http_error():
    session = FakeEvaSession([FakeResponse(500, text_data="broken")])
    client = EvaClient(session, "https://eva.example")

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await client.call("CmfList.get")


@pytest.mark.asyncio
async def test_eva_client_raises_for_non_json_response():
    session = FakeEvaSession([FakeResponse(200, text_data="<html>oops</html>", headers={"Content-Type": "text/html"})])
    client = EvaClient(session, "https://eva.example")

    with pytest.raises(RuntimeError, match="non-JSON"):
        await client.call("CmfList.get")


@pytest.mark.asyncio
async def test_eva_client_raises_when_release_is_missing():
    session = FakeEvaSession([FakeResponse(200, json_data={"result": None})])
    client = EvaClient(session, "https://eva.example")

    with pytest.raises(RuntimeError, match="Не найден релиз EVA"):
        await client.get_release_tasks("REL-404")


@pytest.mark.asyncio
async def test_eva_client_returns_release_tasks():
    session = FakeEvaSession(
        [
            FakeResponse(200, json_data={"result": {"id": 123}}),
            FakeResponse(
                200,
                json_data={
                    "result": [
                        {"code": "CRM-1", "name": "First"},
                        {"code": "CRM-2", "name": "Second"},
                    ]
                },
            ),
        ]
    )
    client = EvaClient(session, "https://eva.example")

    tasks = await client.get_release_tasks("REL-1")

    assert [task["code"] for task in tasks] == ["CRM-1", "CRM-2"]
    assert session.post_calls[0][1]["method"] == "CmfList.get"
    assert session.post_calls[1][1]["method"] == "CmfTask.list"
