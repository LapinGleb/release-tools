from uuid import uuid4

from release_tools.settings.eva import get_eva_settings


class EvaClient:
    def __init__(self, session, base_url: str, api_path: str | None = None):
        settings = get_eva_settings()
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path or settings.API_PATH
        self.jsonrpc_version = settings.JSONRPC_VERSION
        self.response_preview_length = settings.RESPONSE_PREVIEW_LENGTH

    async def call(self, method: str, params: dict | None = None) -> dict:
        payload = {
            "callid": str(uuid4()),
            "args": None,
            "kwargs": params or {},
            "method": method,
            "jsonrpc": self.jsonrpc_version,
        }
        if not params:
            payload.pop("kwargs")

        async with self.session.post(f"{self.base_url}{self.api_path}", json=payload) as resp:
            content_type = resp.headers.get("Content-Type", "")

            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                body = await resp.text()
                raise RuntimeError(
                    f"EVA API redirected to {location!r}. "
                    f"Проверьте EVA_TOKEN и права. Body preview={body[: self.response_preview_length]!r}"
                )

            if resp.status >= 300:
                body = await resp.text()
                raise RuntimeError(
                    f"EVA API returned HTTP {resp.status}. "
                    f"Content-Type={content_type}. Body preview={body[: self.response_preview_length]!r}"
                )

            if "application/json" not in content_type.lower():
                body = await resp.text()
                raise RuntimeError(
                    f"EVA API returned non-JSON response. "
                    f"Content-Type={content_type}. Body preview={body[: self.response_preview_length]!r}"
                )

            return await resp.json()

    async def get_release_task_codes(self, release_code: str) -> list[str]:
        tasks = await self.get_release_tasks(release_code)

        return sorted(task["code"] for task in tasks if task.get("code"))

    async def get_release_tasks(self, release_code: str) -> list[dict]:
        settings = get_eva_settings()
        release = await self.call(
            settings.RELEASE_LIST_METHOD,
            {"filter": ["code", "==", release_code]},
        )
        release_result = release.get("result")
        if not release_result:
            raise RuntimeError(f"Не найден релиз EVA: {release_code}")

        tasks = await self.call(
            settings.TASK_LIST_METHOD,
            {
                "filter": ["fix_versions", "IN", [release_result["id"]]],
                "include_archived": "true",
                "fields": ["code", "name", "responsible"],
            },
        )

        return tasks.get("result", [])
