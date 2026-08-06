import importlib

from pydantic import BaseModel

from release_tools.schemas import (
    GitLabProject,
    MergeRequestLink,
    ProjectResult,
    TaskInfo,
)


def test_schemas_public_api_exports_domain_models():
    assert issubclass(GitLabProject, BaseModel)
    assert issubclass(TaskInfo, BaseModel)
    assert issubclass(MergeRequestLink, BaseModel)
    assert issubclass(ProjectResult, BaseModel)


def test_old_modules_do_not_export_domain_models():
    modules = {
        "release_tools.common": ["GitLabProject"],
        "release_tools.task_merge_requests": ["TaskInfo", "MergeRequestLink"],
        "release_tools.release_branches": ["ProjectResult"],
    }

    for module_name, model_names in modules.items():
        module = importlib.import_module(module_name)
        for model_name in model_names:
            assert not hasattr(module, model_name)
