from urllib.parse import (
    parse_qs,
    urlparse,
)

import pytest
from pydantic import ValidationError

from release_tools.commands.create_merge_request_links import (
    parse_args,
    print_merge_request_report,
)
from release_tools.schemas import (
    MergeRequestLink,
    TaskInfo,
)
from release_tools.task_merge_requests import (
    MergeRequestLinkPlanner,
    build_merge_request_url,
    filter_tasks_by_assignee,
)


class FakeGitLabClient:
    def __init__(self, branches):
        self.branches = branches
        self.branch_name_requests = []

    async def branch_exists(self, project_id, branch_name):
        return branch_name in self.branches.get(project_id, set())

    async def get_branch_names(self, project_id):
        self.branch_name_requests.append(project_id)
        return self.branches.get(project_id, set())


def build_link(
    task: TaskInfo,
    project_path: str,
    source_branch: str,
    url: str,
) -> MergeRequestLink:
    return MergeRequestLink(
        task=task,
        project_name=project_path.rsplit("/", 1)[-1],
        project_path=project_path,
        project_web_url=f"https://gitlab.example/{project_path}",
        source_branch=source_branch,
        target_branch="release/20260604",
        url=url,
    )


def test_filter_tasks_by_assignee_matches_responsible_full_name():
    tasks = [
        TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
        TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович"),
    ]

    filtered = filter_tasks_by_assignee(tasks, ["  лапин глеб александрович  "])

    assert filtered == [tasks[0]]


def test_filter_tasks_by_assignee_includes_all_without_filter():
    tasks = [
        TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
        TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович"),
    ]

    assert filter_tasks_by_assignee(tasks, []) == tasks


def test_task_info_and_merge_request_link_are_frozen():
    task = TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович")
    link = build_link(
        task=task,
        project_path="fo/crm",
        source_branch="feature/CRM-1",
        url="https://gitlab.example/fo/crm/-/merge_requests/new",
    )

    with pytest.raises(ValidationError):
        task.name = "changed"
    with pytest.raises(ValidationError):
        link.source_branch = "bugfix/CRM-1"


def test_build_merge_request_url_encodes_gitlab_new_mr_params():
    url = build_merge_request_url(
        project={
            "id": 42,
            "web_url": "https://gitlab.example/fo/crm",
        },
        source_branch="feature/CRM-1",
        target_branch="release/20260604",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.geturl().split("?", 1)[0] == ("https://gitlab.example/fo/crm/-/merge_requests/new")
    assert params["merge_request[source_project_id]"] == ["42"]
    assert params["merge_request[source_branch]"] == ["feature/CRM-1"]
    assert params["merge_request[target_project_id]"] == ["42"]
    assert params["merge_request[target_branch]"] == ["release/20260604"]


def test_parse_args_supports_assignee_prefix_and_release():
    args = parse_args(
        [
            "--eva-release",
            "REL-005056",
            "--release",
            "20260604",
            "--assignee",
            "Лапин Глеб Александрович",
            "--assignee",
            "Иванов Иван Иванович",
            "--prefix",
            "feature",
            "--prefix",
            "hotfix",
        ]
    )

    assert args.eva_release == "REL-005056"
    assert args.release == "20260604"
    assert args.assignees == [
        "Лапин Глеб Александрович",
        "Иванов Иван Иванович",
    ]
    assert args.prefixes == ["feature", "hotfix"]


def test_print_merge_request_report_uses_telegram_friendly_task_groups(capsys):
    links = [
        build_link(
            task=TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
            project_path="fo/crm",
            source_branch="feature/CRM-1",
            url="https://gitlab.example/fo/crm/-/merge_requests/new?merge_request[source_branch]=feature%2FCRM-1",
        ),
        build_link(
            task=TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
            project_path="fo/api",
            source_branch="bugfix/CRM-1",
            url="https://gitlab.example/fo/api/-/merge_requests/new?merge_request[source_branch]=bugfix%2FCRM-1",
        ),
    ]

    print_merge_request_report(
        links,
        [TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович")],
    )

    assert capsys.readouterr().out == (
        "\n"
        "🔗 Merge request links\n"
        "\n"
        "CRM-1 — First task\n"
        "Responsible: Лапин Глеб Александрович\n"
        "• fo/api\n"
        "  bugfix/CRM-1 -> release/20260604\n"
        "  https://gitlab.example/fo/api/-/merge_requests/new?merge_request[source_branch]=bugfix%2FCRM-1\n"
        "• fo/crm\n"
        "  feature/CRM-1 -> release/20260604\n"
        "  https://gitlab.example/fo/crm/-/merge_requests/new?merge_request[source_branch]=feature%2FCRM-1\n"
        "\n"
        "⚠️ Tasks not found\n"
        "• CRM-2 — Second task (Иванов Иван Иванович)\n"
    )


def test_print_merge_request_report_shows_empty_links_block(capsys):
    print_merge_request_report([], [])

    assert capsys.readouterr().out == ("\n" "🔗 Merge request links\n" "• No links generated.\n")


@pytest.mark.asyncio
async def test_planner_returns_links_for_matching_branches():
    gitlab = FakeGitLabClient({1: {"feature/CRM-1"}, 2: {"hotfix/CRM-2"}})
    planner = MergeRequestLinkPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        prefixes=["feature", "bugfix", "hotfix"],
    )

    results = await planner.plan_links(
        projects=[
            {
                "id": 1,
                "name": "crm",
                "path": "crm",
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            },
            {
                "id": 2,
                "name": "api",
                "path": "api",
                "path_with_namespace": "fo/api",
                "web_url": "https://gitlab.example/fo/api",
            },
        ],
        tasks=[
            TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
            TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович"),
        ],
    )

    assert [result.task.code for result in results] == ["CRM-1", "CRM-2"]
    assert results[0].project_path == "fo/crm"
    assert results[0].source_branch == "feature/CRM-1"
    assert results[1].project_path == "fo/api"
    assert results[1].source_branch == "hotfix/CRM-2"
    assert gitlab.branch_name_requests == [1, 2]


@pytest.mark.asyncio
async def test_planner_uses_prefix_priority_per_task():
    gitlab = FakeGitLabClient({1: {"bugfix/CRM-1", "feature/CRM-1"}})
    planner = MergeRequestLinkPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        prefixes=["feature", "bugfix", "hotfix"],
    )

    results = await planner.plan_links(
        projects=[
            {
                "id": 1,
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            },
        ],
        tasks=[TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович")],
    )

    assert [result.source_branch for result in results] == ["feature/CRM-1"]


@pytest.mark.asyncio
async def test_planner_tracks_tasks_without_matching_branches():
    gitlab = FakeGitLabClient({1: {"feature/CRM-1"}})
    planner = MergeRequestLinkPlanner(
        gitlab=gitlab,
        release_branch="release/20260604",
        prefixes=["feature", "bugfix", "hotfix"],
    )

    results = await planner.plan_links(
        projects=[
            {
                "id": 1,
                "path_with_namespace": "fo/crm",
                "web_url": "https://gitlab.example/fo/crm",
            },
        ],
        tasks=[
            TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
            TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович"),
        ],
    )

    missing_tasks = planner.not_found_tasks(
        results,
        [
            TaskInfo(code="CRM-1", name="First task", responsible="Лапин Глеб Александрович"),
            TaskInfo(code="CRM-2", name="Second task", responsible="Иванов Иван Иванович"),
        ],
    )

    assert [result.task.code for result in results] == ["CRM-1"]
    assert [task.code for task in missing_tasks] == ["CRM-2"]
