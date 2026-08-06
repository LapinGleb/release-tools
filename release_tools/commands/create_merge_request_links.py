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
    detail,
    key_value,
    section,
)
from release_tools.schemas import (
    MergeRequestLink,
    TaskInfo,
)
from release_tools.settings.eva import get_eva_settings
from release_tools.settings.gitlab import get_gitlab_settings
from release_tools.settings.release import get_release_settings
from release_tools.task_merge_requests.planner import (
    MergeRequestLinkPlanner,
    filter_tasks_by_assignee,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    gitlab_settings = get_gitlab_settings()
    parser = argparse.ArgumentParser(
        description="Print dry-run GitLab merge request creation links for EVA release tasks.",
    )
    parser.add_argument(CliOptionEnum.EVA_RELEASE, required=True, help="EVA release code, e.g. REL-005056")
    parser.add_argument(CliOptionEnum.RELEASE, required=True, help="Release suffix, e.g. 20260604")
    parser.add_argument(CliOptionEnum.GROUP, default=gitlab_settings.GROUP, help="GitLab group name")
    parser.add_argument(
        CliOptionEnum.ASSIGNEE,
        action="append",
        dest="assignees",
        default=[],
        help="Responsible full name from EVA. Can be passed multiple times.",
    )
    parser.add_argument(
        CliOptionEnum.PREFIX,
        action="append",
        dest="prefixes",
        help="Task branch prefix. Can be passed multiple times.",
    )
    return parser.parse_args(argv)


def task_from_eva(raw_task: dict) -> TaskInfo | None:
    code = raw_task.get("code")
    if not code:
        return None

    responsible = raw_task.get("responsible") or {}
    return TaskInfo(
        code=code,
        name=raw_task.get("name", ""),
        responsible=responsible.get("name", "Исполнитель не назначен"),
    )


def print_context(
    args: argparse.Namespace,
    release_branch: str,
    prefixes: list[str],
    tasks: list[TaskInfo],
) -> None:
    print(section("🔗 Merge request links"))
    print(key_value("EVA release", args.eva_release))
    print(key_value("Release branch", release_branch))
    print(key_value("GitLab group", args.group))
    print(key_value("Mode", "DRY-RUN links only"))
    print(key_value("Prefixes", ", ".join(prefixes)))
    if args.assignees:
        print(key_value("Assignee filter", ", ".join(args.assignees)))
    else:
        print(key_value("Assignee filter", "all responsible users"))
    print(key_value("Tasks after filter", len(tasks)))
    if tasks:
        print(bullet(", ".join(task.code for task in tasks)))


def print_merge_request_report(
    links: list[MergeRequestLink],
    not_found_tasks: list[TaskInfo],
) -> None:
    print()
    print(section("🔗 Merge request links"))
    if not links:
        print(bullet("No links generated."))
    else:
        current_task_code = None
        for link in sorted(links, key=lambda item: (item.task.code, item.project_path)):
            if link.task.code != current_task_code:
                current_task_code = link.task.code
                task_title = f"{link.task.code} — {link.task.name}" if link.task.name else link.task.code
                print()
                print(task_title)
                print(key_value("Responsible", link.task.responsible))

            print(bullet(link.project_path))
            print(detail(f"{link.source_branch} -> {link.target_branch}"))
            print(detail(link.url))

    if not_found_tasks:
        print()
        print(section("⚠️ Tasks not found"))
        for task in not_found_tasks:
            title = f" — {task.name}" if task.name else ""
            print(bullet(f"{task.code}{title} ({task.responsible})"))


async def run(args: argparse.Namespace):
    eva_settings = get_eva_settings()
    gitlab_settings = get_gitlab_settings()
    release_settings = get_release_settings()
    release_branch = normalize_release_branch(args.release)
    prefixes = resolve_prefixes(args.prefixes, release_settings.PREFIXES)

    async with create_eva_session() as eva_session:
        eva = EvaClient(eva_session, eva_settings.BASE_URL)
        raw_tasks = await eva.get_release_tasks(args.eva_release)

    all_tasks = [task for raw_task in raw_tasks if (task := task_from_eva(raw_task)) is not None]
    tasks = filter_tasks_by_assignee(all_tasks, args.assignees)

    print_context(args, release_branch, prefixes, tasks)
    if not tasks:
        print()
        print(section("⚠️ No EVA tasks matched the selected assignee filter."))
        return []

    async with create_gitlab_session() as gitlab_session:
        gitlab = GitLabClient(gitlab_session, gitlab_settings.BASE_URL, args.group)
        projects = await gitlab.get_all_projects()
        print(key_value("GitLab projects", len(projects)))

        planner = MergeRequestLinkPlanner(
            gitlab=gitlab,
            release_branch=release_branch,
            prefixes=prefixes,
        )
        links = await planner.plan_links(projects, tasks)

    print_merge_request_report(links, planner.not_found_tasks(links, tasks))
    return links


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except RuntimeError as exc:
        print(f"❌ ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
