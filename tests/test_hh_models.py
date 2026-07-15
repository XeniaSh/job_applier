from app.collectors.models import HHVacancyDetails, strip_html


def test_hh_vacancy_details_mapping() -> None:
    payload = {
        "id": "101",
        "name": "Java Backend Engineer",
        "employer": {"name": "Acme"},
        "alternate_url": "https://hh.ru/vacancy/101",
        "area": {"name": "Remote"},
        "employment": {"name": "Full-time"},
        "salary": {"from": 300000, "to": 400000, "currency": "RUR", "gross": False},
        "description": "<p>Разработка сервисов на <b>Java</b></p><ul><li>Kafka</li></ul>",
        "published_at": "2026-07-15T10:00:00+0300",
    }

    details = HHVacancyDetails.from_hh_payload(payload)

    assert details.external_id == "101"
    assert details.title == "Java Backend Engineer"
    assert details.company == "Acme"
    assert details.url == "https://hh.ru/vacancy/101"
    assert details.location == "Remote"
    assert details.employment == "Full-time"
    assert details.salary == "300000-400000 RUR net"
    assert "Разработка сервисов на Java" in details.description
    assert "Kafka" in details.description
    assert details.published_at == "2026-07-15T10:00:00+0300"


def test_html_stripping_removes_tags() -> None:
    cleaned = strip_html("<div>Hello <b>world</b><br/>line2</div>")
    assert cleaned == "Hello world\nline2"
