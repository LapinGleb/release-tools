from release_tools.schemas import (
    EndpointChange,
    ProjectReport,
    ReviewFinding,
)


def _render_changes(label: str, changes: tuple[EndpointChange, ...]) -> list[str]:
    if not changes:
        return []
    lines = [label]
    for change in sorted(changes, key=lambda item: item.key):
        lines.append(f"- {change.key.method} {change.key.path} — {change.reason}")
    return lines


def _render_review(changes: tuple[EndpointChange | ReviewFinding, ...]) -> list[str]:
    if not changes:
        return []
    lines = ["REVIEW"]
    for change in sorted(
        changes,
        key=lambda item: (item.key.method, item.key.path) if item.key else ("", ""),
    ):
        endpoint = f"{change.key.method} {change.key.path}" if change.key else "UNKNOWN"
        lines.append(f"- {endpoint} — {change.reason}")
    return lines


def render_report(base: str, head: str, reports: tuple[ProjectReport, ...]) -> str:
    lines = ["Endpoint changes", f"BASE: {base}", f"HEAD: {head}"]
    for report in sorted((item for item in reports if not item.error), key=lambda item: item.project):
        lines.extend(["", f"[{report.project}]", ""])
        sections = [
            _render_changes("ADDED", report.added),
            _render_changes("CHANGED", report.changed),
            _render_changes("REMOVED", report.removed),
            _render_review(report.review),
        ]
        populated = [section for section in sections if section]
        if not populated:
            lines.append("NO ENDPOINT CHANGES")
            continue
        for index, section in enumerate(populated):
            if index:
                lines.append("")
            lines.extend(section)

    errors = sorted((report for report in reports if report.error), key=lambda item: item.project)
    if errors:
        lines.extend(["", "ERRORS"])
        lines.extend(f"- {report.project} — {report.error}" for report in errors)
    return "\n".join(lines) + "\n"
