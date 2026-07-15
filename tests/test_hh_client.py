import httpx
import pytest
import respx

from app.collectors.hh_client import HHClient, HHRequestError


@respx.mock
def test_hh_search_request_parameters() -> None:
    route = respx.get("https://api.hh.ru/vacancies").mock(
        return_value=httpx.Response(status_code=200, json={"items": []})
    )
    client = HHClient(user_agent="job-vacancy-analyzer/0.1 contact@example.com")

    client.search_vacancies(query="Java Backend", page=1, per_page=10)

    assert route.called
    request = route.calls.last.request
    assert request.headers["User-Agent"] == "job-vacancy-analyzer/0.1 contact@example.com"
    assert request.url.params["text"] == "Java Backend"
    assert request.url.params["page"] == "1"
    assert request.url.params["per_page"] == "10"
    assert request.url.params["order_by"] == "publication_time"
    assert request.url.params["period"] == "7"


@respx.mock
def test_get_vacancy_http_failure_raises_hh_request_error() -> None:
    respx.get("https://api.hh.ru/vacancies/123").mock(return_value=httpx.Response(status_code=503))
    client = HHClient(user_agent="job-vacancy-analyzer/0.1 contact@example.com")

    with pytest.raises(HHRequestError):
        client.get_vacancy("123")
