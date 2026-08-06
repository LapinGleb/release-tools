def build_task_branch_candidates(task_codes: list[str], prefixes: list[str]) -> list[str]:
    return [f"{prefix.strip('/')}/{task_code}" for task_code in task_codes for prefix in prefixes]
