from __future__ import annotations

import re
from dataclasses import dataclass

SENIORITY_INTERN = "INTERN"
SENIORITY_JUNIOR = "JUNIOR"
SENIORITY_MID = "MID"
SENIORITY_SENIOR = "SENIOR"
SENIORITY_STAFF_PLUS = "STAFF_PLUS"
SENIORITY_LEAD_MANAGER = "LEAD_MANAGER"
SENIORITY_UNKNOWN = "UNKNOWN"
SENIORITY_LABELS = (
    SENIORITY_INTERN,
    SENIORITY_JUNIOR,
    SENIORITY_MID,
    SENIORITY_SENIOR,
    SENIORITY_STAFF_PLUS,
    SENIORITY_LEAD_MANAGER,
    SENIORITY_UNKNOWN,
)

# First matching rule wins. Lead/manager is checked before senior so
# "Senior Engineering Manager" is LEAD_MANAGER, not SENIOR.
# Software Engineer I is JUNIOR: Roman numeral I is the entry band, not a
# mid/senior IC target.
_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bintern(?:ship)?s?\b"),
        SENIORITY_INTERN,
        "title contains intern",
    ),
    (
        re.compile(
            r"\b(?:engineering manager|eng(?:ineering)? manager|team lead|tech lead|"
            r"technical lead|head of|directors?|managers?|leads?)\b"
        ),
        SENIORITY_LEAD_MANAGER,
        "lead/manager role is not target IC backend role",
    ),
    (
        re.compile(r"\b(?:staff|principal|distinguished|fellows?)\b"),
        SENIORITY_STAFF_PLUS,
        "title contains staff/principal-level signal",
    ),
    (
        re.compile(r"\b(?:junior|graduate|entry[ -]?level|associates?)\b"),
        SENIORITY_JUNIOR,
        "title contains junior/entry-level signal",
    ),
    (
        re.compile(r"\b(?:software\s+)?engineers?\s+i\b"),
        SENIORITY_JUNIOR,
        "Software Engineer I is treated as junior/entry band",
    ),
    (
        re.compile(r"\bseniors?\b"),
        SENIORITY_SENIOR,
        "title contains senior",
    ),
    (
        re.compile(r"\b(?:software\s+)?engineers?\s+iii\b"),
        SENIORITY_SENIOR,
        "Engineer III is treated as senior band",
    ),
    (
        re.compile(r"\b(?:software\s+)?engineers?\s+ii\b"),
        SENIORITY_MID,
        "Engineer II is treated as mid band",
    ),
    (
        re.compile(r"\bmid[ -]level\b"),
        SENIORITY_MID,
        "title contains mid-level",
    ),
)


@dataclass(frozen=True)
class SeniorityClassification:
    label: str
    reasons: list[str]


def classify_seniority(title: str) -> SeniorityClassification:
    normalized = " ".join(title.strip().lower().split())
    if not normalized:
        return SeniorityClassification(label=SENIORITY_UNKNOWN, reasons=["title has no explicit seniority"])
    for pattern, label, reason in _RULES:
        if pattern.search(normalized):
            return SeniorityClassification(label=label, reasons=[reason])
    return SeniorityClassification(label=SENIORITY_UNKNOWN, reasons=["title has no explicit seniority"])
