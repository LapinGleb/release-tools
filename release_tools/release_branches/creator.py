from release_tools.common import (
    GitLabLike,
    build_task_branch_candidates,
)
from release_tools.schemas import (
    GitLabProject,
    ProjectResult,
)


class ReleaseBranchCreator:
    def __init__(
        self,
        gitlab: GitLabLike,
        release_branch: str,
        base_branch: str,
        prefixes: list[str],
        apply: bool,
    ):
        self.gitlab = gitlab
        self.release_branch = release_branch
        self.base_branch = base_branch
        self.prefixes = prefixes
        self.apply = apply

    async def process_project(
        self,
        project: GitLabProject | dict,
        task_codes: list[str],
    ) -> ProjectResult:
        project = GitLabProject.from_api(project)
        candidates = build_candidate_branch_names(task_codes, self.prefixes)
        branch_names = await self.gitlab.get_branch_names(project.id)
        matched_branches = [branch for branch in candidates if branch in branch_names]

        if not matched_branches:
            return ProjectResult(
                project_path=project.display_path,
                status="NO_MATCH",
                message="task branches not found",
                matched_branches=[],
            )

        if self.base_branch not in branch_names:
            return ProjectResult(
                project_path=project.display_path,
                status="ERROR",
                message=f"base branch {self.base_branch} not found",
                matched_branches=matched_branches,
            )

        if self.release_branch in branch_names:
            return ProjectResult(
                project_path=project.display_path,
                status="SKIP",
                message=f"{self.release_branch} already exists",
                matched_branches=matched_branches,
            )

        if not self.apply:
            return ProjectResult(
                project_path=project.display_path,
                status="DRY-RUN",
                message=f"would create {self.release_branch} from {self.base_branch}",
                matched_branches=matched_branches,
            )

        await self.gitlab.create_branch(project.id, self.release_branch, self.base_branch)
        return ProjectResult(
            project_path=project.display_path,
            status="CREATED",
            message=f"created {self.release_branch} from {self.base_branch}",
            matched_branches=matched_branches,
        )


def build_candidate_branch_names(task_codes: list[str], prefixes: list[str]) -> list[str]:
    return build_task_branch_candidates(task_codes, prefixes)
