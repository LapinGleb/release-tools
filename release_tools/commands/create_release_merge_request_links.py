import argparse
import asyncio
import sys

from release_tools.clients.gitlab import GitLabClient
from release_tools.common import (
    CliOptionEnum,
    create_gitlab_session,
    normalize_release_branch,
)
from release_tools.common.output import (
    key_value,
    section,
)
from release_tools.release_merge_requests import ReleaseMergeRequestPlanner
from release_tools.release_merge_requests.reporting import print_report
from release_tools.settings.gitlab import get_gitlab_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    gitlab_settings = get_gitlab_settings()
    parser = argparse.ArgumentParser(
        description="Print or create GitLab merge requests from a release branch into a target branch.",
    )
    parser.add_argument(CliOptionEnum.RELEASE, required=True, help="Release suffix or branch, e.g. 20260604")
    parser.add_argument(CliOptionEnum.GROUP, default=gitlab_settings.GROUP, help="GitLab group name")
    parser.add_argument(CliOptionEnum.TARGET, default="master", help="Target branch")
    parser.add_argument(
        CliOptionEnum.APPLY, action="store_true", help="Create merge requests; default is dry-run links"
    )
    return parser.parse_args(argv)


def print_context(args: argparse.Namespace, release_branch: str) -> None:
    print(section("🔗 Release merge requests"))
    print(key_value("Release branch", release_branch))
    print(key_value("Target branch", args.target))
    print(key_value("GitLab group", args.group))
    print(key_value("Mode", "APPLY" if args.apply else "DRY-RUN links only"))


async def run(args: argparse.Namespace):
    gitlab_settings = get_gitlab_settings()
    release_branch = normalize_release_branch(args.release)

    print_context(args, release_branch)
    async with create_gitlab_session() as gitlab_session:
        gitlab = GitLabClient(gitlab_session, gitlab_settings.BASE_URL, args.group)
        projects = await gitlab.get_all_projects()
        print(key_value("GitLab projects", len(projects)))

        planner = ReleaseMergeRequestPlanner(
            gitlab=gitlab,
            release_branch=release_branch,
            target_branch=args.target,
            apply=args.apply,
        )
        results = await planner.plan(projects)

    print_report(results)
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except RuntimeError as exc:
        print(f"❌ ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
