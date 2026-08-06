from release_tools.common.output import (
    bullet,
    detail,
    section,
    status_line,
)
from release_tools.schemas import ReleaseMergeRequestResult


def print_report(results: list[ReleaseMergeRequestResult]) -> None:
    print()
    print(section("🔗 Release merge requests"))
    if not results:
        print(bullet("No merge requests to create."))
        return

    for result in sorted(results, key=lambda item: (item.status, item.project_path)):
        print(status_line(result.status, result.project_path))
        print(detail(f"{result.source_branch} -> {result.target_branch}"))
        if result.url:
            print(detail(result.url))
        if result.message:
            print(detail(result.message))
