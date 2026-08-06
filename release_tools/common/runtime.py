import aiohttp

from release_tools.common.ssl import create_ssl_context
from release_tools.settings.eva import get_eva_settings
from release_tools.settings.gitlab import get_gitlab_settings
from release_tools.settings.http import get_http_settings


def require_token(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Не найден обязательный токен: {name}")
    return value


def create_timeout():
    return aiohttp.ClientTimeout(total=get_http_settings().TIMEOUT_SECONDS)


def create_connector():
    return aiohttp.TCPConnector(ssl=create_ssl_context(get_http_settings().VERIFY_SSL))


def create_eva_session():
    token = require_token("EVA_TOKEN", get_eva_settings().TOKEN)
    return aiohttp.ClientSession(
        connector=create_connector(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=create_timeout(),
    )


def create_gitlab_session():
    token = require_token("GITLAB_TOKEN", get_gitlab_settings().TOKEN)
    return aiohttp.ClientSession(
        connector=create_connector(),
        headers={"PRIVATE-TOKEN": token},
        timeout=create_timeout(),
    )
