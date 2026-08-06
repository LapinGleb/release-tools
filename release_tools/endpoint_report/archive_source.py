import io
import tarfile


class ArchiveSource:
    def __init__(self, revisions: dict[str, dict[str, str]]) -> None:
        self.revisions = revisions

    @classmethod
    def from_archives(cls, archives: dict[str, bytes]) -> "ArchiveSource":
        revisions: dict[str, dict[str, str]] = {}
        for revision, content in archives.items():
            files: dict[str, str] = {}
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
                for member in archive.getmembers():
                    if not member.isfile() or not member.name.endswith(".py"):
                        continue
                    parts = member.name.split("/", 1)
                    if len(parts) != 2:
                        continue
                    path = parts[1]
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    files[path] = extracted.read().decode("utf-8")
            revisions[revision] = files
        return cls(revisions)

    def list_python_files(self, revision: str) -> tuple[str, ...]:
        return tuple(sorted(self.revisions[revision]))

    def read_file(self, revision: str, path: str) -> str:
        return self.revisions[revision][path]

    def read_files(self, revision: str, paths: tuple[str, ...]) -> dict[str, str]:
        return {path: self.revisions[revision][path] for path in paths}
