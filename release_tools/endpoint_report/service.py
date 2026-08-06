import asyncio
from collections.abc import Callable
from typing import Any

from release_tools.endpoint_report.archive_source import ArchiveSource
from release_tools.endpoint_report.change_context import ChangeContextBuilder
from release_tools.endpoint_report.llm_client import OpenAICompatibleClient
from release_tools.endpoint_report.route_registry import RouteRegistry
from release_tools.schemas import (
    EndpointChange,
    GitLabProject,
    ProjectReport,
    ReviewFinding,
)


class EndpointReportService:
    def __init__(
        self,
        gitlab: Any,
        llm: OpenAICompatibleClient,
        concurrency: int = 3,
        max_context_chars: int = 60_000,
        progress: Callable[[str, str], None] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be at least 1")
        self.gitlab = gitlab
        self.llm = llm
        self.project_semaphore = asyncio.Semaphore(concurrency)
        self.max_context_chars = max_context_chars
        self.progress = progress or (lambda _project, _message: None)

    async def generate(
        self,
        base: str,
        head: str,
        requested_projects: tuple[str, ...],
    ) -> tuple[ProjectReport, ...]:
        available = await self.gitlab.get_all_projects()
        matches = _match_projects(available, requested_projects)
        tasks = [
            self._analyze_project(project, base, head) if project else _missing_project(name, self.gitlab.group)
            for name, project in matches
        ]
        return tuple(await asyncio.gather(*tasks))

    async def _analyze_project(self, project: GitLabProject, base: str, head: str) -> ProjectReport:
        async with self.project_semaphore:
            try:
                comparison = await self.gitlab.compare_repository(project.id, base, head)
                self.progress(project.display_path, f"compare: {len(comparison.diffs)} files")
                if not comparison.incomplete and not comparison.changed_python_paths:
                    self.progress(project.display_path, "completed")
                    return ProjectReport(project=project.display_path)
                merge_base = await self.gitlab.get_merge_base(project.id, base, head)
                base_archive, head_archive = await asyncio.gather(
                    self.gitlab.download_repository_archive(project.id, merge_base),
                    self.gitlab.download_repository_archive(project.id, head),
                )
                self.progress(project.display_path, "archives loaded")
                source = ArchiveSource.from_archives({merge_base: base_archive, head: head_archive})
                base_registry = RouteRegistry.build(source, merge_base)
                head_registry = RouteRegistry.build(source, head)
                base_routes = base_registry.definitions
                head_routes = head_registry.definitions
                self.progress(project.display_path, f"routes: {len(head_routes)}")
                added = tuple(
                    EndpointChange(key=key, reason="маршрут добавлен в head")
                    for key in sorted(head_routes.keys() - base_routes.keys())
                )
                removed = tuple(
                    EndpointChange(key=key, reason="маршрут отсутствует в head")
                    for key in sorted(base_routes.keys() - head_routes.keys())
                )
                candidates = ChangeContextBuilder(source, self.max_context_chars).build(
                    merge_base,
                    head,
                    base_routes,
                    head_routes,
                )
                self.progress(project.display_path, f"candidates: {len(candidates)}")
                changed_items = []
                total = len(candidates)
                if not candidates:
                    self.progress(project.display_path, "LLM: 0/0")
                for index, candidate in enumerate(candidates, start=1):
                    result = await asyncio.to_thread(self.llm.classify, candidate)
                    changed_items.extend(result.changed_endpoints)
                    self.progress(project.display_path, f"LLM: {index}/{total}")
                changed = tuple(
                    EndpointChange(key=item.key, reason=item.reason)
                    for item in sorted(changed_items, key=lambda item: item.key)
                )
                report = ProjectReport(
                    project=project.display_path,
                    added=added,
                    changed=changed,
                    removed=removed,
                    review=_merge_review_findings(base_registry.review_findings, head_registry.review_findings),
                )
                self.progress(project.display_path, "completed")
                return report
            except Exception as error:
                self.progress(project.display_path, f"error: {error}")
                return ProjectReport(project=project.display_path, error=str(error))


def _match_projects(
    available: list[GitLabProject],
    requested: tuple[str, ...],
) -> list[tuple[str, GitLabProject | None]]:
    result: list[tuple[str, GitLabProject | None]] = []
    for name in requested:
        matches = [project for project in available if name in {project.path, project.path_with_namespace}]
        result.append((name, matches[0] if len(matches) == 1 else None))
    return result


async def _missing_project(name: str, group: str) -> ProjectReport:
    project = name if "/" in name else f"{group}/{name}"
    return ProjectReport(project=project, error="project not found")


def _merge_review_findings(*groups: tuple[ReviewFinding, ...]) -> tuple[ReviewFinding, ...]:
    unique = {
        (finding.key.method if finding.key else "", finding.key.path if finding.key else "", finding.reason): finding
        for group in groups
        for finding in group
    }
    return tuple(unique[key] for key in sorted(unique))
