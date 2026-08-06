from release_tools.common.output import (
    bullet,
    detail,
    section,
    status_line,
)
from release_tools.schemas import ProjectResult


def print_report(results: list[ProjectResult], task_codes: list[str]) -> None:
    matched_results = [result for result in results if result.status != "NO_MATCH"]
    found_task_codes = {
        branch.split("/", 1)[1] for result in matched_results for branch in result.matched_branches if "/" in branch
    }

    print()
    print(section("📦 Projects"))
    if not matched_results:
        print(status_line("NO_MATCH", "task branches not found in any project"))
    for result in matched_results:
        branches = ", ".join(result.matched_branches)
        print(status_line(result.status, result.project_path))
        print(detail(result.message))
        print(detail(f"matched: {branches}"))

    not_found_tasks = [code for code in task_codes if code not in found_task_codes]
    if not_found_tasks:
        print()
        print(section("⚠️ Tasks not found"))
        for code in not_found_tasks:
            print(bullet(code))
