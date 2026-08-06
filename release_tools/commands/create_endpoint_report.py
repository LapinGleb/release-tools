import argparse
import asyncio
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path

from release_tools.clients.gitlab import GitLabClient
from release_tools.common import (
    CliOptionEnum,
    create_gitlab_session,
)
from release_tools.endpoint_report.llm_client import OpenAICompatibleClient
from release_tools.endpoint_report.reporting import render_report
from release_tools.endpoint_report.service import EndpointReportService
from release_tools.schemas import ProjectReport
from release_tools.settings import get_llm_settings
from release_tools.settings.gitlab import get_gitlab_settings


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = get_gitlab_settings()
    parser = argparse.ArgumentParser(description="Generate a TXT report of changed FastAPI endpoints.")
    parser.add_argument(CliOptionEnum.HEAD, required=True, help="Exact branch, tag, or commit to analyze")
    parser.add_argument(CliOptionEnum.BASE, required=True, help="Exact base branch, tag, or commit")
    parser.add_argument(
        CliOptionEnum.PROJECT, action="append", dest="projects", required=True, help="GitLab project path"
    )
    parser.add_argument(CliOptionEnum.GROUP, default=settings.GROUP, help="GitLab group name")
    parser.add_argument(CliOptionEnum.OUTPUT, type=Path, default=Path("endpoint-changes.txt"))
    parser.add_argument(CliOptionEnum.CONCURRENCY, type=_positive_int, default=3)
    parser.add_argument(CliOptionEnum.MAX_CONTEXT_CHARS, type=_positive_int, default=60_000)
    parser.add_argument(CliOptionEnum.VERBOSE, action="store_true")
    args = parser.parse_args(argv)
    args.projects = _unique(args.projects)
    return args


def write_report(output: Path, content: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)


def report_exit_code(reports: tuple[ProjectReport, ...]) -> int:
    return int(any(report.error or report.review for report in reports))


def print_progress(project: str, message: str) -> None:
    print(f"[{project}] {message}", file=sys.stderr, flush=True)


async def run(
    args: argparse.Namespace,
    *,
    progress: Callable[[str, str], None] | None = print_progress,
) -> tuple[ProjectReport, ...]:
    gitlab_settings = get_gitlab_settings()
    llm = OpenAICompatibleClient(get_llm_settings())
    async with create_gitlab_session() as session:
        gitlab = GitLabClient(session, gitlab_settings.BASE_URL, args.group)
        service = EndpointReportService(
            gitlab,
            llm,
            concurrency=args.concurrency,
            max_context_chars=args.max_context_chars,
            progress=progress,
        )
        reports = await service.generate(args.base, args.head, tuple(args.projects))
    write_report(args.output, render_report(args.base, args.head, reports))
    return reports


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reports = asyncio.run(run(args))
    except Exception as error:
        if args.verbose:
            traceback.print_exc()
        else:
            print(f"endpoint report failed: {error}; rerun with --verbose for traceback", file=sys.stderr)
        return 1
    print(f"Endpoint report written to {args.output}")
    return report_exit_code(reports)


if __name__ == "__main__":
    raise SystemExit(main())
