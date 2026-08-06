import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import (
    HTTPError,
    URLError,
)
from urllib.parse import urlsplit
from urllib.request import (
    Request,
    urlopen,
)

from release_tools import (
    schemas,
    settings,
)


class LLMError(RuntimeError):
    """OpenAI-compatible LLM request or response failed."""


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: settings.LLMSettings,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.sleep = sleep
        self.prompt = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")

    def classify(self, candidate: schemas.ChangeCandidate) -> schemas.AnalysisResult:
        request = self._request(candidate)
        for attempt in range(1, self.settings.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:  # noqa: S310
                    payload = json.loads(response.read())
                content = self._extract_content(payload)
                return schemas.AnalysisResult.from_payload(
                    json.loads(_strip_json_fence(content)),
                    {candidate.key},
                )
            except HTTPError as error:
                if not _is_retryable_status(error.code) or attempt == self.settings.max_attempts:
                    body = error.read(2_000).decode(errors="replace").strip()
                    raise LLMError(f"LLM HTTP error {error.code}: {body}") from error
            except (URLError, TimeoutError) as error:
                if attempt == self.settings.max_attempts:
                    reason = getattr(error, "reason", error)
                    raise LLMError(f"LLM network request failed: {reason}") from error
            except (KeyError, TypeError, ValueError) as error:
                raise LLMError("LLM returned an invalid response") from error
            self.sleep(2 ** (attempt - 1))
        raise LLMError("LLM request failed")

    def _request(self, candidate: schemas.ChangeCandidate) -> Request:
        url = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        if urlsplit(url).scheme not in {"http", "https"}:
            raise LLMError("LLM_BASE_URL must use http or https")
        user_content = (
            f"Кандидат: {candidate.key.method} {candidate.key.path}\n"
            f"Связанные символы: {', '.join(candidate.symbols)}\n\n"
            f"Diff:\n{candidate.diff}"
        )
        body = json.dumps(
            {
                "model": self.settings.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": user_content},
                ],
            }
        ).encode()
        return Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    @staticmethod
    def _extract_content(payload: Any) -> str:
        return str(payload["choices"][0]["message"]["content"])


def _is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600  # noqa: PLR2004


def _strip_json_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped
