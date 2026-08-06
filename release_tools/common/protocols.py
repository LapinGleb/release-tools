from typing import Protocol


class GitLabLike(Protocol):
    async def get_branch_names(self, project_id: int) -> set[str]:
        ...

    async def branch_exists(self, project_id: int, branch_name: str) -> bool:
        ...

    async def create_branch(self, project_id: int, branch_name: str, ref: str) -> None:
        ...


class Source(Protocol):
    def list_python_files(self, revision: str) -> tuple[str, ...]:
        ...

    def read_file(self, revision: str, path: str) -> str:
        ...

    def read_files(self, revision: str, paths: tuple[str, ...]) -> dict[str, str]:
        ...
