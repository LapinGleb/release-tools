from pydantic import (
    BaseModel,
    ConfigDict,
)


class TaskInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    responsible: str
