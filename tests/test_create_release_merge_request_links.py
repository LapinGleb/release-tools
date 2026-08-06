import pytest

from release_tools.commands.create_release_merge_request_links import parse_args
from release_tools.release_merge_requests import ReleaseMergeRequestPlanner
from release_tools.release_merge_requests.reporting import print_report


class FakeGitLabClient:
    def __init__(self, branches, existing_merge_requests=None):
        self.branches = branches
        self.existing_merge_requests = existing_merge_requests or {}
        self.branch_checks = []
        self.find_merge_request_calls = []
        self.create_merge_request_calls = []

    async def branch_exists(self, project_id, branch_name):
        self.branch_checks.append((project_id, branch_name))
        return branch_name in self.branches.get(project_id, set())

    async def find_open_merge_request(self, project_id, source_branch, target_branch):
        self.find_merge_request_calls.append((project_id, source_branch, target_branch))
        return self.existing_merge_requests.get((project_id, source_branch, target_branch))

    async def create_merge_request(self, project_id, source_branch, target_branch, title):
        self.create_merge_request_calls.append((project_id, source_branch, target_branch, title))
        return {
            "web_url": f"https://gitlab.example/project-{project_id}/-/merge_requests/1",
        }


def test_parse_args_supports_release_group_target_and_apply():
    args = parse_args(
        [
            "--release",
            "20260604",
            "--group",
            "fo/backend",
            "--target",
            "main",
            "--apply",
        ]
    )

    assert args.release == "20260604"
    assert args.group == "fo/backend"
    assert args.target == "main"
    assert args.apply


@pytest.mark.asyncio
async def test_planner_dry_run_returns_links_only_for_projects_with_release_branch():
    gitlab = FakeGitLabClient({1: {"release/20260604"}, 2: {"master"}})
    planner = ReleaseMergeRequestPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        target_branch="master",
        apply=False,
    )

    results = await planner.plan(
        [
            {
                "id": 1,
                "name": "crm",
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            },
            {
                "id": 2,
                "name": "api",
                "path_with_namespace": "fo/api",
                "web_url": "https://gitlab.example/fo/api",
            },
        ]
    )

    assert [(result.status, result.project_path) for result in results] == [("DRY-RUN", "fo/crm")]
    assert results[0].source_branch == "release/20260604"
    assert results[0].target_branch == "master"
    assert "merge_request%5Bsource_branch%5D=release%2F20260604" in results[0].url
    assert "merge_request%5Btarget_branch%5D=master" in results[0].url
    assert gitlab.branch_checks == [(1, "release/20260604"), (2, "release/20260604")]
    assert gitlab.find_merge_request_calls == [(1, "release/20260604", "master")]
    assert gitlab.create_merge_request_calls == []


@pytest.mark.asyncio
async def test_planner_apply_creates_merge_request_when_release_branch_exists():
    gitlab = FakeGitLabClient({1: {"release/20260604"}})
    planner = ReleaseMergeRequestPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        target_branch="master",
        apply=True,
    )

    results = await planner.plan(
        [
            {
                "id": 1,
                "name": "crm",
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            }
        ]
    )

    assert [(result.status, result.url) for result in results] == [
        ("CREATED", "https://gitlab.example/project-1/-/merge_requests/1")
    ]
    assert gitlab.create_merge_request_calls == [
        (1, "release/20260604", "master", "Merge release/20260604 into master")
    ]


@pytest.mark.asyncio
async def test_planner_skips_existing_open_merge_request_without_creating_duplicate():
    existing = {
        (1, "release/20260604", "master"): {
            "web_url": "https://gitlab.example/fo/crm/-/merge_requests/7",
        }
    }
    gitlab = FakeGitLabClient({1: {"release/20260604"}}, existing)
    planner = ReleaseMergeRequestPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        target_branch="master",
        apply=True,
    )

    results = await planner.plan(
        [
            {
                "id": 1,
                "name": "crm",
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            }
        ]
    )

    assert [(result.status, result.url) for result in results] == [
        ("SKIP", "https://gitlab.example/fo/crm/-/merge_requests/7")
    ]
    assert gitlab.create_merge_request_calls == []


def test_print_release_merge_request_report_shows_statuses(capsys):
    gitlab = FakeGitLabClient({})
    planner = ReleaseMergeRequestPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        target_branch="master",
        apply=False,
    )
    results = [
        planner.result(
            project={
                "id": 1,
                "name": "crm",
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            },
            status="DRY-RUN",
            url="https://gitlab.example/fo/crm/-/merge_requests/new",
        )
    ]

    print_report(results)

    assert capsys.readouterr().out == (
        "\n"
        "🔗 Release merge requests\n"
        "• [DRY-RUN] fo/crm\n"
        "  release/20260604 -> master\n"
        "  https://gitlab.example/fo/crm/-/merge_requests/new\n"
    )


def test_print_release_merge_request_report_shows_empty_message(capsys):
    print_report([])

    assert capsys.readouterr().out == ("\n" "🔗 Release merge requests\n" "• No merge requests to create.\n")
