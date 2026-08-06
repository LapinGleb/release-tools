from enum import StrEnum


class CliOptionEnum(StrEnum):
    EVA_RELEASE = "--eva-release"
    RELEASE = "--release"
    APPLY = "--apply"
    GROUP = "--group"
    BASE = "--base"
    PREFIX = "--prefix"
    ASSIGNEE = "--assignee"
    TARGET = "--target"
    HEAD = "--head"
    PROJECT = "--project"
    OUTPUT = "--output"
    CONCURRENCY = "--concurrency"
    MAX_CONTEXT_CHARS = "--max-context-chars"
    VERBOSE = "--verbose"


def normalize_release_branch(release: str) -> str:
    release = release.strip("/")
    if release.startswith("release/"):
        return release
    return f"release/{release}"


def resolve_prefixes(
    prefixes: list[str] | None,
    default_prefixes: list[str],
) -> list[str]:
    return prefixes or default_prefixes
