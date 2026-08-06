import pytest

from release_tools.commands.create_release_branches import parse_args
from release_tools.common import normalize_release_branch
from release_tools.common.ssl import create_ssl_context
from release_tools.release_branches import (
    ReleaseBranchCreator,
    build_candidate_branch_names,
)
from release_tools.release_branches.reporting import print_report
from release_tools.schemas import ProjectResult


class FakeGitLabClient:
    def __init__(self, branches):
        self.branches = branches
        self.created = []
        self.branch_name_requests = []

    async def branch_exists(self, project_id, branch_name):
        return branch_name in self.branches.get(project_id, set())

    async def get_branch_names(self, project_id):
        self.branch_name_requests.append(project_id)
        return self.branches.get(project_id, set())

    async def create_branch(self, project_id, branch_name, ref):
        self.created.append((project_id, branch_name, ref))
        self.branches.setdefault(project_id, set()).add(branch_name)


def test_normalize_release_branch_adds_release_prefix():
    assert normalize_release_branch("20260604") == "release/20260604"


def test_build_candidate_branch_names_uses_all_prefixes():
    branches = build_candidate_branch_names(
        ["CRM-1"],
        ["feature", "bugfix", "hotfix"],
    )

    assert branches == ["feature/CRM-1", "bugfix/CRM-1", "hotfix/CRM-1"]


def test_parse_args_uses_required_release_options():
    args = parse_args(
        [
            "--eva-release",
            "REL-005063",
            "--release",
            "20260618",
        ]
    )

    assert args.eva_release == "REL-005063"
    assert args.release == "20260618"


def test_create_ssl_context_can_disable_verification():
    assert not create_ssl_context(False)


@pytest.mark.asyncio
async def test_dry_run_does_not_create_branch():
    gitlab = FakeGitLabClient({1: {"master", "feature/CRM-1"}})
    creator = ReleaseBranchCreator(
        gitlab=gitlab,
        release_branch="release/20260604",
        base_branch="master",
        prefixes=["feature", "bugfix", "hotfix"],
        apply=False,
    )

    result = await creator.process_project(
        {"id": 1, "path_with_namespace": "fo/crm"},
        ["CRM-1"],
    )

    assert result.status == "DRY-RUN"
    assert result.matched_branches == ["feature/CRM-1"]
    assert gitlab.branch_name_requests == [1]
    assert gitlab.created == []


@pytest.mark.asyncio
async def test_existing_release_branch_is_skipped():
    gitlab = FakeGitLabClient({1: {"master", "feature/CRM-1", "release/20260604"}})
    creator = ReleaseBranchCreator(
        gitlab=gitlab,
        release_branch="release/20260604",
        base_branch="master",
        prefixes=["feature", "bugfix", "hotfix"],
        apply=True,
    )

    result = await creator.process_project(
        {"id": 1, "path_with_namespace": "fo/crm"},
        ["CRM-1"],
    )

    assert result.status == "SKIP"
    assert "already exists" in result.message
    assert gitlab.branch_name_requests == [1]
    assert gitlab.created == []


def test_print_report_uses_telegram_friendly_sections(capsys):
    print_report(
        [
            ProjectResult(
                project_path="fo/crm",
                status="DRY-RUN",
                message="would create release/20260604 from master",
                matched_branches=["feature/CRM-1"],
            ),
            ProjectResult(
                project_path="fo/api",
                status="SKIP",
                message="release/20260604 already exists",
                matched_branches=["hotfix/CRM-2"],
            ),
            ProjectResult(
                project_path="fo/billing",
                status="ERROR",
                message="base branch master not found",
                matched_branches=["bugfix/CRM-3"],
            ),
        ],
        ["CRM-1", "CRM-2", "CRM-3", "CRM-4"],
    )

    assert capsys.readouterr().out == (
        "\n"
        "📦 Projects\n"
        "• [DRY-RUN] fo/crm\n"
        "  would create release/20260604 from master\n"
        "  matched: feature/CRM-1\n"
        "• [SKIP] fo/api\n"
        "  release/20260604 already exists\n"
        "  matched: hotfix/CRM-2\n"
        "• [ERROR] fo/billing\n"
        "  base branch master not found\n"
        "  matched: bugfix/CRM-3\n"
        "\n"
        "⚠️ Tasks not found\n"
        "• CRM-4\n"
    )


def test_print_report_shows_no_matches_block(capsys):
    print_report(
        [
            ProjectResult(
                project_path="fo/crm",
                status="NO_MATCH",
                message="task branches not found",
                matched_branches=[],
            )
        ],
        ["CRM-1"],
    )

    assert capsys.readouterr().out == (
        "\n"
        "📦 Projects\n"
        "• [NO_MATCH] task branches not found in any project\n"
        "\n"
        "⚠️ Tasks not found\n"
        "• CRM-1\n"
    )
