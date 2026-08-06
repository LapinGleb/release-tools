from urllib.parse import quote

from release_tools.common.http import raise_for_status
from release_tools.schemas import (
    GitLabProject,
    RepositoryComparison,
)
from release_tools.settings.gitlab import get_gitlab_settings


class GitLabClient:
    def __init__(self, session, base_url: str, group: str):
        settings = get_gitlab_settings()
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.group = group
        self.per_page = settings.PER_PAGE

    async def get_all_projects(self) -> list[GitLabProject]:
        page = 1
        projects: list[GitLabProject] = []

        while True:
            group = quote(self.group, safe="")
            url = f"{self.base_url}/api/v4/groups/{group}/projects"
            params = {
                "namespace": self.group,
                "membership": "true",
                "simple": "true",
                "per_page": str(self.per_page),
                "page": str(page),
            }
            async with self.session.get(url, params=params) as resp:
                await raise_for_status(resp, "GitLab projects request failed")
                data = await resp.json()

            if not data:
                break

            projects.extend(GitLabProject.from_api(item) for item in data)
            page += 1

        return projects

    async def get_branch_names(self, project_id: int) -> set[str]:
        page = 1
        branches: set[str] = set()

        while True:
            url = f"{self.base_url}/api/v4/projects/{project_id}/repository/branches"
            params = {
                "per_page": str(self.per_page),
                "page": str(page),
            }
            async with self.session.get(url, params=params) as resp:
                await raise_for_status(resp, f"GitLab branches request failed for project {project_id}")
                data = await resp.json()

            if not data:
                break

            branches.update(str(branch["name"]) for branch in data if branch.get("name"))
            page += 1

        return branches

    async def branch_exists(self, project_id: int, branch_name: str) -> bool:
        encoded_branch = quote(branch_name, safe="")
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/branches/{encoded_branch}"
        async with self.session.get(url) as resp:
            if resp.status == 200:
                return True
            if resp.status == 404:
                return False
            await raise_for_status(resp, f"GitLab branch check failed for {branch_name}")
            return False

    async def create_branch(self, project_id: int, branch_name: str, ref: str) -> None:
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/branches"
        async with self.session.post(
            url,
            params={"branch": branch_name, "ref": ref},
        ) as resp:
            await raise_for_status(resp, f"GitLab branch create failed for {branch_name}")

    async def find_open_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
    ) -> dict | None:
        url = f"{self.base_url}/api/v4/projects/{project_id}/merge_requests"
        params = {
            "state": "opened",
            "source_branch": source_branch,
            "target_branch": target_branch,
            "per_page": "1",
        }
        async with self.session.get(url, params=params) as resp:
            await raise_for_status(resp, f"GitLab merge request search failed for project {project_id}")
            data = await resp.json()

        return data[0] if data else None

    async def create_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
    ) -> dict:
        url = f"{self.base_url}/api/v4/projects/{project_id}/merge_requests"
        async with self.session.post(
            url,
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
            },
        ) as resp:
            await raise_for_status(resp, f"GitLab merge request create failed for project {project_id}")
            return await resp.json()

    async def compare_repository(
        self,
        project_id: int,
        base: str,
        head: str,
    ) -> RepositoryComparison:
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/compare"
        params = {
            "from": base,
            "to": head,
            "straight": "false",
            "unidiff": "true",
        }
        async with self.session.get(url, params=params) as resp:
            if resp.status == 404:
                raise RuntimeError(f"GitLab compare ref not found: {base}...{head}")
            await raise_for_status(resp, f"GitLab repository compare failed for project {project_id}")
            data = await resp.json()
        raw_diffs = data.get("diffs") if isinstance(data, dict) else None
        diffs = tuple(item for item in (raw_diffs or []) if isinstance(item, dict))
        incomplete = bool(data.get("compare_timeout")) or any(
            bool(item.get("too_large") or item.get("collapsed")) for item in diffs
        )
        return RepositoryComparison(diffs=diffs, incomplete=incomplete)

    async def download_repository_archive(self, project_id: int, ref: str) -> bytes:
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/archive.tar.gz"
        params = {"sha": ref, "include_lfs_blobs": "false"}
        async with self.session.get(url, params=params) as resp:
            if resp.status == 404:
                raise RuntimeError(f"GitLab repository ref not found: {ref}")
            await raise_for_status(resp, f"GitLab repository archive failed for project {project_id}")
            return await resp.read()

    async def get_merge_base(self, project_id: int, base: str, head: str) -> str:
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/merge_base"
        params = {"refs[]": [base, head]}
        async with self.session.get(url, params=params) as resp:
            if resp.status == 404:
                raise RuntimeError(f"GitLab merge base ref not found: {base}...{head}")
            await raise_for_status(resp, f"GitLab merge base request failed for project {project_id}")
            try:
                data = await resp.json()
            except (TypeError, ValueError) as error:
                raise RuntimeError(f"GitLab returned invalid merge base response for project {project_id}") from error
        merge_base = data.get("id") if isinstance(data, dict) else None
        if not isinstance(merge_base, str) or not merge_base.strip():
            raise RuntimeError(f"GitLab returned invalid merge base response for project {project_id}")
        return merge_base.strip()
