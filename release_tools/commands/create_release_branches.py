import argparse
import asyncio
import sys

from release_tools.clients.eva import EvaClient
from release_tools.clients.gitlab import GitLabClient
from release_tools.common import (
    CliOptionEnum,
    create_eva_session,
    create_gitlab_session,
    normalize_release_branch,
    resolve_prefixes,
)
from release_tools.common.output import (
    bullet,
    key_value,
    section,
)
from release_tools.release_branches.creator import ReleaseBranchCreator
from release_tools.release_branches.reporting import print_report
from release_tools.settings.eva import get_eva_settings
from release_tools.settings.gitlab import get_gitlab_settings
from release_tools.settings.release import get_release_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    gitlab_settings = get_gitlab_settings()
    release_settings = get_release_settings()
    parser = argparse.ArgumentParser(
        description="Create GitLab release branches for projects with EVA task branches.",
    )
    parser.add_argument(CliOptionEnum.EVA_RELEASE, required=True, help="EVA release code, e.g. REL-005056")
    parser.add_argument(CliOptionEnum.RELEASE, required=True, help="Release suffix, e.g. 20260604")
    parser.add_argument(CliOptionEnum.APPLY, action="store_true", help="Create branches; default is dry-run")
    parser.add_argument(CliOptionEnum.GROUP, default=gitlab_settings.GROUP, help="GitLab group name")
    parser.add_argument(CliOptionEnum.BASE, default=release_settings.BASE_BRANCH, help="Base branch")
    parser.add_argument(
        CliOptionEnum.PREFIX,
        action="append",
        dest="prefixes",
        help="Task branch prefix. Can be passed multiple times.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace):
    eva_settings = get_eva_settings()
    gitlab_settings = get_gitlab_settings()
    release_settings = get_release_settings()
    release_branch = normalize_release_branch(args.release)
    prefixes = resolve_prefixes(args.prefixes, release_settings.PREFIXES)

    async with create_eva_session() as eva_session:
        eva = EvaClient(eva_session, eva_settings.BASE_URL)
        task_codes = await eva.get_release_task_codes(args.eva_release)

    print(section("🚀 Release branches"))
    print(key_value("EVA release", args.eva_release))
    print(key_value("Release branch", release_branch))
    print(key_value("Base branch", args.base))
    print(key_value("Mode", "APPLY" if args.apply else "DRY-RUN"))
    print(key_value("Tasks from EVA", len(task_codes)))
    if task_codes:
        print(bullet(", ".join(task_codes)))

    async with create_gitlab_session() as gitlab_session:
        gitlab = GitLabClient(gitlab_session, gitlab_settings.BASE_URL, args.group)
        projects = await gitlab.get_all_projects()
        print(key_value("GitLab projects", len(projects)))

        creator = ReleaseBranchCreator(
            gitlab=gitlab,
            release_branch=release_branch,
            base_branch=args.base,
            prefixes=prefixes,
            apply=args.apply,
        )
        results = await asyncio.gather(*(creator.process_project(project, task_codes) for project in projects))

    print_report(results, task_codes)
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except RuntimeError as exc:
        print(f"❌ ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
