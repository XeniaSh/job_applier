from app.models import VacancyEvaluation


DECISION_RU = {
    "STRONG_MATCH": "Сильное совпадение",
    "POTENTIAL_MATCH": "Потенциально подходящая вакансия",
    "IGNORE": "Скорее пропустить",
}


def _clean_list(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(item.strip().split()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _format_list(title: str, items: list[str], *, empty_text: str | None = None) -> str:
    if not items and empty_text is not None:
        return empty_text
    if not items:
        return ""
    lines = [f"{title}:"]
    lines.extend([f"- {item}" for item in items])
    return "\n".join(lines)


def format_evaluation_ru(result: VacancyEvaluation) -> str:
    matched_points = _clean_list(result.matched_points)[:5]
    gaps = _clean_list(result.gaps)[:3]
    nuances = _clean_list(result.nuances)[:3]
    sections = [
        f"Решение: {DECISION_RU[result.decision.value]} ({result.decision.value})",
        f"Кратко: {' '.join(result.summary.strip().split())}",
        _format_match_line(result.match_percentage),
        _format_list("Совпадения", matched_points, empty_text="Совпадения: нет"),
        f"Рекомендуемое резюме: {result.recommended_resume.value}",
        f"Шаблон сопроводительного: {result.recommended_cover_template.value}",
    ]
    if gaps:
        sections.insert(3, _format_list("Пробелы", gaps))
    if nuances:
        insert_at = 4 if gaps else 3
        sections.insert(insert_at, _format_list("Нюансы", nuances))
    return "\n\n".join(sections)


def _format_match_line(match_percentage: float | None) -> str:
    if match_percentage is None:
        return "Совпадение по стеку: n/a"
    return f"Совпадение по стеку: {match_percentage:.1f}%"
