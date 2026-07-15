from __future__ import annotations

from typing import Any

import httpx


class HHRequestError(Exception):
    """Raised when HeadHunter API request fails."""


class HHClient:
    def __init__(
        self,
        user_agent: str,
        base_url: str = "https://api.hh.ru",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"User-Agent": user_agent}
        self._timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
        self._provided_client = http_client

    def search_vacancies(
        self,
        query: str,
        page: int = 0,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        payload = self._request_json(
            path="/vacancies",
            params={
                "text": query,
                "page": page,
                "per_page": per_page,
                "order_by": "publication_time",
                "period": 7,
            },
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise HHRequestError("HH vacancies response has unexpected structure.")
        return [item for item in items if isinstance(item, dict)]

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        payload = self._request_json(path=f"/vacancies/{vacancy_id}")
        if not isinstance(payload, dict):
            raise HHRequestError("HH vacancy details response has unexpected structure.")
        return payload

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            if self._provided_client is not None:
                response = self._provided_client.get(
                    url,
                    headers=self._headers,
                    params=params,
                    timeout=self._timeout,
                )
            else:
                with httpx.Client() as client:
                    response = client.get(
                        url,
                        headers=self._headers,
                        params=params,
                        timeout=self._timeout,
                    )

            response.raise_for_status()

        except httpx.TimeoutException as exc:
            raise HHRequestError("HH API request timed out.") from exc

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            response_text = exc.response.text[:500]

            raise HHRequestError(
                f"HH API returned HTTP {status_code}: {response_text}"
            ) from exc

        except httpx.RequestError as exc:
            raise HHRequestError(
                f"HH API network error: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise HHRequestError("HH API returned non-JSON response.") from exc
        if not isinstance(payload, dict):
            raise HHRequestError("HH API returned invalid JSON structure.")
        return payload
