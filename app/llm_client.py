import json
import logging
from json import JSONDecodeError
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from app.models import VacancyExtraction


class LLMRequestError(Exception):
    """Raised when an LLM API request fails."""


class LLMResponseError(Exception):
    """Raised when an LLM response is malformed or cannot be validated."""


logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout or httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=15.0,
            pool=10.0,
        )
        self._provided_client = http_client

    def extract_vacancy(
        self,
        prompt: str,
        vacancy: str,
    ) -> VacancyExtraction:
        last_error: Exception | None = None

        for attempt in range(1, 3):
            try:
                raw_content = self._request_content(
                    prompt=prompt,
                    vacancy=vacancy,
                )
                payload = json.loads(raw_content)
                return VacancyExtraction.model_validate(payload)

            except (
                JSONDecodeError,
                ValidationError,
                LLMResponseError,
            ) as exc:
                last_error = exc
                logger.warning(
                    "Invalid LLM response on attempt %d of 2: %s",
                    attempt,
                    type(exc).__name__,
                )

        raise LLMResponseError(
            "LLM returned an invalid response twice."
        ) from last_error

    def _request_content(
        self,
        prompt: str,
        vacancy: str,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model,
            "response_format": {
                "type": "json_object",
            },
            "temperature": 0,
            "max_tokens": 1200,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": f"Vacancy text:\n{vacancy}\n",
                },
            ],
        }

        endpoint = f"{self._api_url}/chat/completions"
        request_started_at = perf_counter()

        try:
            response = self._send_request(
                endpoint=endpoint,
                headers=headers,
                payload=payload,
            )
            response.raise_for_status()

        except httpx.TimeoutException as exc:
            raise LLMRequestError(
                "LLM API request timed out."
            ) from exc

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise LLMRequestError(
                f"LLM API returned HTTP {status_code}."
            ) from exc

        except httpx.HTTPError as exc:
            raise LLMRequestError(
                "LLM API request failed."
            ) from exc

        finally:
            duration_seconds = perf_counter() - request_started_at
            logger.info(
                "LLM request took %.1fs",
                duration_seconds,
            )

        return self._extract_text_content(response)

    def _send_request(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        if self._provided_client is not None:
            return self._provided_client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )

        with httpx.Client() as client:
            return client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )

    @staticmethod
    def _extract_text_content(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMResponseError(
                "LLM API returned a non-JSON response."
            ) from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                "Unexpected LLM API response structure."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                "LLM response contained no textual content. "
                f"finish_reason={finish_reason!r}"
            )

        logger.info(
            "LLM response format: chat_completions; length=%d",
            len(content),
        )

        return content.strip()