from __future__ import annotations

import html

from app.telegram.models import TelegramVacancyCard

MAX_MESSAGE_LEN = 3500


def card_display_sections(evaluation) -> tuple[list[str], list[str]]:
    """Split evaluation into Telegram warnings vs informational metadata."""
    warnings: list[str] = []
    info_items = list(getattr(evaluation, "info_items", []) or [])
    seen_info = {" ".join(item.split()).lower() for item in info_items if item}
    seen_warn: set[str] = set()
    for signal in getattr(evaluation, "warning_signals", []) or []:
        evidence = " ".join(str(signal.get("evidence", "")).split())
        if not evidence:
            continue
        key = evidence.lower()
        code = str(signal.get("code", ""))
        if code == "incomplete_description" or _is_completeness_metadata(evidence):
            if key not in seen_info:
                info_items.append(
                    "Job description is not available in the LinkedIn email"
                    if "linkedin" in key or "неполн" in key or "incomplete" in key
                    else evidence
                )
                seen_info.add(key)
            continue
        if key in seen_warn:
            continue
        seen_warn.add(key)
        warnings.append(evidence)
    return warnings, info_items


def format_telegram_card_html(card: TelegramVacancyCard) -> str:
    strength = _match_strength_label(card.decision)
    lines = [f"<b>{_escape(strength)}</b>", "", _escape(card.title)]

    if card.company:
        lines.append(_escape(card.company))
    if card.location:
        lines.append(_escape(card.location))

    why = _clean_limited([card.decision_reason or ""], limit=1)
    if why:
        lines.append("")
        lines.append("<b>Why:</b>")
        lines.append(_escape(why[0]))

    info_items = _clean_limited(_normalize_info_items(card.info_items), limit=5)
    if info_items:
        lines.append("")
        lines.append("<b>Info:</b>")
        for info in info_items:
            lines.append(_format_info_item(info))

    warnings = _clean_limited(card.warnings or _legacy_warning_nuances(card.nuances), limit=3)
    # Incomplete description belongs in Info, never as a match warning.
    warnings = [item for item in warnings if not _is_completeness_metadata(item)]
    if warnings:
        lines.append("")
        lines.append("<b>Warnings:</b>")
        for warning in warnings:
            lines.append(f"⚠️ {_escape(warning)}")

    gaps = _clean_limited(card.gaps, limit=2)
    if gaps:
        lines.append("")
        for gap in gaps:
            lines.append(f"— {_escape(gap)}")

    if card.match_percentage is not None:
        lines.append("")
        lines.append(f"Стек: {_format_percentage(card.match_percentage)}")

    lines.append("")
    lines.append(f"Резюме: {_escape(card.recommended_resume)}")

    rendered = "\n".join(lines).strip()
    if len(rendered) <= MAX_MESSAGE_LEN:
        return rendered
    return rendered[: MAX_MESSAGE_LEN - 1].rstrip() + "…"


def _match_strength_label(decision: str) -> str:
    normalized = (decision or "").strip().upper()
    if normalized in {"STRONG_MATCH", "STRONG"}:
        return "🟢 STRONG"
    if normalized in {"POTENTIAL_MATCH", "POTENTIAL"}:
        return "🟡 POTENTIAL"
    if normalized in {"IGNORE"}:
        return "⚪ IGNORE"
    return decision


def _normalize_info_items(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = " ".join(item.strip().split())
        if not text:
            continue
        if text.lower().startswith("info:"):
            text = text.split(":", 1)[1].strip()
        result.append(text)
    return result


def _is_completeness_metadata(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "неполн",
            "incomplete",
            "not available in the linkedin email",
            "нет полного описания",
            "job description is not available",
        )
    )


def _legacy_warning_nuances(nuances: list[str]) -> list[str]:
    """Fallback when older callers only populate nuances."""
    result: list[str] = []
    for nuance in nuances:
        lowered = nuance.lower()
        if any(token in lowered for token in ("hybrid", "on-site", "onsite", "work mode:", "salary:")):
            continue
        result.append(nuance)
    return result


def _format_info_item(item: str) -> str:
    # Plain completeness notes stay on one line.
    if ":" not in item or item.lower().startswith("job description"):
        return _escape(item)
    label, value = item.split(":", 1)
    label = label.strip()
    value = value.strip()
    if not label or not value:
        return _escape(item)
    if label.lower() in {"work mode", "constraints", "salary"}:
        return f"{_escape(label)}: {_escape(value)}"
    return f"{_escape(label)}:\n{_escape(value)}"


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


def format_preparation_interrupted_html(*, title: str, company: str | None, auto_retry: bool) -> str:
    lines = [
        "⚠️ Preparation was interrupted.",
        "Retrying automatically..." if auto_retry else 'Tap "Prepare application" to retry.',
        "",
        _escape(title),
    ]
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
