from __future__ import annotations

import html

from app.telegram.models import TelegramVacancyCard

MAX_MESSAGE_LEN = 3500


def format_telegram_card_html(card: TelegramVacancyCard) -> str:
    decision = _escape(card.decision)
    title = _escape(card.title)
    lines = [f"<b>{decision} · {title}</b>"]

    if card.company:
        lines.append(_escape(card.company))
    if card.location:
        lines.append(_escape(card.location))

    lines.append("")
    lines.append(f"Стек: {_format_percentage(card.match_percentage)}")
    lines.append("")

    for gap in _clean_limited(card.gaps, limit=2):
        lines.append(f"— {_escape(gap)}")
    for nuance in _clean_limited(card.nuances, limit=3):
        lines.append(f"⚠️ {_escape(nuance)}")

    if lines[-1] != "":
        lines.append("")
    lines.append(f"Резюме: {_escape(card.recommended_resume)}")

    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def format_prepared_application_html(
    *,
    title: str,
    company: str | None,
    language: str,
    recommended_resume: str,
    cover_letter: str,
    warnings: list[str],
) -> str:
    is_ru = language.strip().lower() == "ru"
    lines = ["✅ Application Ready", "", _escape(title)]
    if company:
        lines.append(_escape(company))
    lines.append("")
    lines.append("<b>📄 Resume</b>")
    lines.append(_escape(_human_resume_name(recommended_resume)))
    lines.append("")
    lines.append("<b>📄 Сопроводительное письмо</b>" if is_ru else "<b>📄 Cover Letter (EN)</b>")
    lines.append(_escape(" ".join(cover_letter.strip().split())))
    cleaned_warnings = _shorten_warnings(warnings, is_ru=is_ru)
    if cleaned_warnings:
        lines.append("")
        lines.append("<b>⚠️ Notes</b>" if not is_ru else "<b>⚠️ Примечания</b>")
        for warning in cleaned_warnings[:3]:
            lines.append(f"• {_escape(warning)}")
    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def format_preparing_application_html(*, title: str, company: str | None) -> str:
    lines = ["⏳ Preparing application...", "", _escape(title)]
    if company:
        lines.append(_escape(company))
    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def format_application_ready_card_html(*, title: str, company: str | None, recommended_resume: str) -> str:
    lines = ["✅ Application Ready", "", _escape(title)]
    if company:
        lines.append(_escape(company))
    lines.append("")
    lines.append("Resume:")
    lines.append(_escape(_human_resume_name(recommended_resume)))
    lines.append("Cover letter: Ready")
    lines.append("Resume PDF: Ready")
    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def format_preparation_failed_html(*, title: str, company: str | None) -> str:
    lines = ["⚠️ Application preparation failed", "", _escape(title)]
    if company:
        lines.append(_escape(company))
    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def format_archived_vacancy_html(*, applied: bool, title: str, company: str | None) -> str:
    header = "✅ Applied" if applied else "❌ Skipped"
    lines = [header, "", _escape(title)]
    if company:
        lines.append(_escape(company))
    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def _format_percentage(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _clean_limited(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _human_resume_name(value: str) -> str:
    mapping = {
        "java-backend": "Java Backend",
        "kotlin-backend": "Kotlin Backend",
        "fintech-backend": "FinTech Backend",
        "generic-backend": "Backend General",
    }
    if value in mapping:
        return mapping[value]
    return value.replace("-", " ").title()


def _shorten_warnings(values: list[str], *, is_ru: bool) -> list[str]:
    cleaned = _clean_limited(values, limit=3)
    result: list[str] = []
    for item in cleaned:
        lower = item.lower()
        if "непол" in lower or "incomplete" in lower:
            mapped = "⚠️ Неполное описание" if is_ru else "⚠️ Incomplete description"
        elif "локац" in lower or "location" in lower:
            mapped = "⚠️ Уточнить локацию" if is_ru else "⚠️ Verify location"
        elif "lead" in lower or "лид" in lower:
            mapped = "⚠️ Уточнить Lead-ожидания" if is_ru else "⚠️ Verify Lead responsibilities"
        else:
            mapped = item
            if len(mapped) > 50:
                mapped = mapped[:49].rstrip() + "…"
        result.append(mapped)
    return _clean_limited(result, limit=3)
