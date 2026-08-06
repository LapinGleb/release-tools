import asyncio
from urllib.parse import urlencode

from release_tools.common import GitLabLike
from release_tools.common import build_task_branch_candidates as build_branch_candidates
from release_tools.schemas import (
    GitLabProject,
    MergeRequestLink,
    TaskInfo,
)


def normalize_assignee(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def filter_tasks_by_assignee(
    tasks: list[TaskInfo],
    assignees: list[str],
) -> list[TaskInfo]:
    normalized_assignees = {normalize_assignee(assignee) for assignee in assignees if normalize_assignee(assignee)}
    if not normalized_assignees:
        return tasks

    return [task for task in tasks if normalize_assignee(task.responsible) in normalized_assignees]


def build_task_branch_candidates(task_code: str, prefixes: list[str]) -> list[str]:
    return build_branch_candidates([task_code], prefixes)


def build_merge_request_url(
    project: GitLabProject | dict,
    source_branch: str,
    target_branch: str,
) -> str:
    project = GitLabProject.from_api(project)
    project_id = str(project.id)
    params = {
        "merge_request[source_project_id]": project_id,
        "merge_request[source_branch]": source_branch,
        "merge_request[target_project_id]": project_id,
        "merge_request[target_branch]": target_branch,
    }
    return f"{project.web_url.rstrip('/')}/-/merge_requests/new?{urlencode(params)}"


class MergeRequestLinkPlanner:
    def __init__(
        self,
        gitlab: GitLabLike,
        release_branch: str,
        prefixes: list[str],
    ):
        self.gitlab = gitlab
        self.release_branch = release_branch
        self.prefixes = prefixes

    async def plan_links(
        self,
        projects: list[GitLabProject | dict],
        tasks: list[TaskInfo],
    ) -> list[MergeRequestLink]:
        per_project = await asyncio.gather(*(self._plan_project_links(project, tasks) for project in projects))
        return [link for project_links in per_project for link in project_links]

    async def _plan_project_links(
        self,
        project: GitLabProject | dict,
        tasks: list[TaskInfo],
    ) -> list[MergeRequestLink]:
        project = GitLabProject.from_api(project)
        branch_names = await self.gitlab.get_branch_names(project.id)
        links: list[MergeRequestLink] = []
        for task in tasks:
            source_branch = self._first_existing_task_branch(branch_names, task)
            if not source_branch:
                continue

            links.append(
                MergeRequestLink(
                    task=task,
                    project_name=project.name,
                    project_path=project.display_path,
                    project_web_url=project.web_url,
                    source_branch=source_branch,
                    target_branch=self.release_branch,
                    url=build_merge_request_url(
                        project,
                        source_branch,
                        self.release_branch,
                    ),
                )
            )
        return links

    def _first_existing_task_branch(
        self,
        branch_names: set[str],
        task: TaskInfo,
    ) -> str | None:
        for branch in build_task_branch_candidates(task.code, self.prefixes):
            if branch in branch_names:
                return branch
        return None

    def not_found_tasks(
        self,
        links: list[MergeRequestLink],
        tasks: list[TaskInfo],
    ) -> list[TaskInfo]:
        found_codes = {link.task.code for link in links}
        return [task for task in tasks if task.code not in found_codes]
