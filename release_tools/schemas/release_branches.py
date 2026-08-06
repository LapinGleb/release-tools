from pydantic import BaseModel


class ProjectResult(BaseModel):
    project_path: str
    status: str
    message: str
    matched_branches: list[str]
