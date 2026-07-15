from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel


class _HTMLToTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        return text.strip()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLToTextParser()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def _normalize_salary(salary: dict[str, Any] | None) -> str:
    if not salary:
        return ""
    salary_from = salary.get("from")
    salary_to = salary.get("to")
    currency = salary.get("currency") or ""
    gross = salary.get("gross")
    gross_text = ""
    if gross is True:
        gross_text = " gross"
    elif gross is False:
        gross_text = " net"

    if salary_from is not None and salary_to is not None:
        return f"{salary_from}-{salary_to} {currency}{gross_text}".strip()
    if salary_from is not None:
        return f"from {salary_from} {currency}{gross_text}".strip()
    if salary_to is not None:
        return f"up to {salary_to} {currency}{gross_text}".strip()
    return ""


class HHVacancyPreview(BaseModel):
    external_id: str
    title: str
    company: str
    url: str
    location: str
    employment: str
    salary: str
    description: str
    published_at: str

    @classmethod
    def from_hh_payload(cls, payload: dict[str, Any]) -> "HHVacancyPreview":
        snippet = payload.get("snippet") or {}
        description = "\n".join(
            item for item in [snippet.get("requirement"), snippet.get("responsibility")] if item
        )
        return cls(
            external_id=str(payload.get("id") or ""),
            title=payload.get("name") or "",
            company=((payload.get("employer") or {}).get("name") or ""),
            url=payload.get("alternate_url") or "",
            location=((payload.get("area") or {}).get("name") or ""),
            employment=((payload.get("employment") or {}).get("name") or ""),
            salary=_normalize_salary(payload.get("salary")),
            description=strip_html(description),
            published_at=payload.get("published_at") or "",
        )


class HHVacancyDetails(BaseModel):
    external_id: str
    title: str
    company: str
    url: str
    location: str
    employment: str
    salary: str
    description: str
    published_at: str

    @classmethod
    def from_hh_payload(cls, payload: dict[str, Any]) -> "HHVacancyDetails":
        return cls(
            external_id=str(payload.get("id") or ""),
            title=payload.get("name") or "",
            company=((payload.get("employer") or {}).get("name") or ""),
            url=payload.get("alternate_url") or "",
            location=((payload.get("area") or {}).get("name") or ""),
            employment=((payload.get("employment") or {}).get("name") or ""),
            salary=_normalize_salary(payload.get("salary")),
            description=strip_html(payload.get("description")),
            published_at=payload.get("published_at") or "",
        )

    def to_analysis_text(self) -> str:
        parts = [
            f"Title: {self.title}",
            f"Company: {self.company}",
            f"Location: {self.location}",
            f"Employment: {self.employment}",
        ]
        if self.salary:
            parts.append(f"Salary: {self.salary}")
        parts.append(f"Published at: {self.published_at}")
        parts.append("Description:")
        parts.append(self.description)
        return "\n".join(parts).strip()
