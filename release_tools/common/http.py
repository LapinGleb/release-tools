from release_tools.settings.http import get_http_settings


async def raise_for_status(resp, message: str) -> None:
    if resp.status < 300:
        return
    body = await resp.text()
    preview_length = get_http_settings().RESPONSE_PREVIEW_LENGTH
    raise RuntimeError(f"{message}: HTTP {resp.status}. Body preview={body[:preview_length]!r}")
