import asyncio
from typing import Protocol

from release_tools.schemas import (
    GitLabProject,
    ReleaseMergeRequestResult,
)
from release_tools.task_merge_requests import build_merge_request_url


class ReleaseMergeRequestGitLabLike(Protocol):
    async def branch_exists(self, project_id: int, branch_name: str) -> bool:
        ...

    async def find_open_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
    ) -> dict | None:
        ...

    async def create_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
    ) -> dict:
        ...


class ReleaseMergeRequestPlanner:
    def __init__(
        self,
        gitlab: ReleaseMergeRequestGitLabLike,
        release_branch: str,
        target_branch: str,
        apply: bool,
    ):
        self.gitlab = gitlab
        self.release_branch = release_branch
        self.target_branch = target_branch
        self.apply = apply

    async def plan(self, projects: list[GitLabProject | dict]) -> list[ReleaseMergeRequestResult]:
        per_project = await asyncio.gather(*(self._plan_project(project) for project in projects))
        return [result for result in per_project if result is not None]

    async def _plan_project(self, project: GitLabProject | dict) -> ReleaseMergeRequestResult | None:
        project = GitLabProject.from_api(project)
        try:
            if not await self.gitlab.branch_exists(project.id, self.release_branch):
                return None

            existing_merge_request = await self.gitlab.find_open_merge_request(
                project.id,
                self.release_branch,
                self.target_branch,
            )
            if existing_merge_request:
                return self.result(
                    project,
                    "SKIP",
                    str(existing_merge_request.get("web_url", "")),
                    "open merge request already exists",
                )

            if not self.apply:
                return self.result(
                    project,
                    "DRY-RUN",
                    build_merge_request_url(project, self.release_branch, self.target_branch),
                )

            merge_request = await self.gitlab.create_merge_request(
                project.id,
                self.release_branch,
                self.target_branch,
                self.merge_request_title,
            )
            return self.result(project, "CREATED", str(merge_request.get("web_url", "")))
        except RuntimeError as exc:
            return self.result(project, "ERROR", "", str(exc))

    @property
    def merge_request_title(self) -> str:
        return f"Merge {self.release_branch} into {self.target_branch}"

    def result(
        self,
        project: GitLabProject | dict,
        status: str,
        url: str,
        message: str = "",
    ) -> ReleaseMergeRequestResult:
        project = GitLabProject.from_api(project)
        return ReleaseMergeRequestResult(
            status=status,
            project_name=project.name or "",
            project_path=project.display_path,
            project_web_url=project.web_url,
            source_branch=self.release_branch,
            target_branch=self.target_branch,
            url=url,
            message=message,
        )
