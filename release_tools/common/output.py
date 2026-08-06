def section(title: str) -> str:
    return title


def bullet(text: str) -> str:
    return f"• {text}"


def detail(text: str) -> str:
    return f"  {text}"


def key_value(key: str, value: object) -> str:
    return f"{key}: {value}"


def status_line(status: str, text: str) -> str:
    return bullet(f"[{status}] {text}")
