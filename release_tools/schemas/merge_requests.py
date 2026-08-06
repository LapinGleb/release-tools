from pydantic import (
    BaseModel,
    ConfigDict,
)

from release_tools.schemas.tasks import TaskInfo


class MergeRequestLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: TaskInfo
    project_name: str
    project_path: str
    project_web_url: str
    source_branch: str
    target_branch: str
    url: str


class ReleaseMergeRequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    project_name: str
    project_path: str
    project_web_url: str
    source_branch: str
    target_branch: str
    url: str
    message: str = ""
