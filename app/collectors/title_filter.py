from __future__ import annotations

import re
from dataclasses import dataclass


ACCEPT_KEYWORDS = ("java", "kotlin", "jvm", "backend", "spring")
ABOVE_SENIORITY_MARKERS = ("staff", "principal", "distinguished", "fellow")
EDUCATION_ROLE_MARKERS = (
    "teacher",
    "trainer",
    "instructor",
    "lecturer",
    "professor",
    "tutor",
    "учитель",
    "преподаватель",
    "тренер",
    "инструктор",
    "лектор",
    "профессор",
)
AI_ONLY_MARKERS = (
    "ai engineer",
    "ai developer",
    "ai researcher",
    "ai specialist",
    "ai scientist",
    "machine learning engineer",
    "ml engineer",
    "llm engineer",
    "prompt engineer",
    "generative ai",
    "genai engineer",
    "gen ai",
)
REJECT_KEYWORDS = (
    "frontend",
    "front-end",
    "react",
    "angular",
    "qa",
    "tester",
    "analyst",
    "support",
    "devops",
    "python",
    "c#",
    ".net",
    "php",
    "mobile",
    "data scientist",
    "ml engineer",
    "teacher",
    "trainer",
    "instructor",
    "ai engineer",
)


@dataclass(frozen=True)
class TitleFilterDecision:
    accepted: bool
    reason: str
    normalized_title: str
    positive_rules: list[str]
    negative_rules: list[str]
    decision: str


def should_accept_title(title: str) -> bool:
    return evaluate_title(title).accepted


def evaluate_title(title: str) -> TitleFilterDecision:
    normalized = _normalize_title(title)
    seniority_hits = _matched_whole_words(normalized, ABOVE_SENIORITY_MARKERS)
    if seniority_hits:
        return TitleFilterDecision(
            accepted=False,
            reason="Above target seniority",
            normalized_title=normalized,
            positive_rules=[],
            negative_rules=seniority_hits,
            decision="REJECT",
        )

    education_hits = _matched_whole_words(normalized, EDUCATION_ROLE_MARKERS)
    if education_hits:
        return TitleFilterDecision(
            accepted=False,
            reason="Education/teaching role",
            normalized_title=normalized,
            positive_rules=[],
            negative_rules=education_hits,
            decision="REJECT",
        )

    positive_rules = [keyword for keyword in ("java", "jvm", "spring", "spring boot", "kotlin") if keyword in normalized]
    if positive_rules:
        return TitleFilterDecision(
            accepted=True,
            reason="Explicit Java/JVM signal in title",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=[],
            decision="PASS",
        )

    ai_hits = [marker for marker in AI_ONLY_MARKERS if marker in normalized]
    if not ai_hits and (" ai " in f" {normalized} " or normalized.startswith("ai ") or "ai/" in normalized):
        ai_hits = ["ai"]
    if ai_hits and not any(token in normalized for token in ("backend", "back-end", "java", "kotlin", "spring", "jvm")):
        return TitleFilterDecision(
            accepted=False,
            reason="AI-only role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=ai_hits,
            decision="REJECT",
        )

    negative_rules: list[str] = []
    if any(keyword in normalized for keyword in ("python", "node", "node.js", "golang", "go ", ".net", "dotnet", "php", "ruby")):
        negative_rules.extend([k for k in ("python", "node", "node.js", "golang", ".net", "dotnet", "php", "ruby") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="Title targets a non-Java specialization",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if any(keyword in normalized for keyword in ("frontend", "front-end", "react", "angular")):
        negative_rules.extend([k for k in ("frontend", "front-end", "react", "angular") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="Frontend role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if any(keyword in normalized for keyword in ("qa", "tester", "test automation")):
        negative_rules.extend([k for k in ("qa", "tester", "test automation") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="QA/test role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if any(keyword in normalized for keyword in ("mobile", "ios", "android")):
        negative_rules.extend([k for k in ("mobile", "ios", "android") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="Mobile role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if any(keyword in normalized for keyword in ("data scientist", "machine learning", "ml engineer", "ml ", " ai ", "ai/")):
        negative_rules.extend([k for k in ("data scientist", "machine learning", "ml engineer") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="Data/ML role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if any(keyword in normalized for keyword in ("devops", "sre", "site reliability")):
        negative_rules.extend([k for k in ("devops", "sre", "site reliability") if k in normalized])
        return TitleFilterDecision(
            accepted=False,
            reason="DevOps/SRE role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if "support" in normalized:
        negative_rules.append("support")
        return TitleFilterDecision(
            accepted=False,
            reason="Support role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    if "embedded" in normalized:
        negative_rules.append("embedded")
        return TitleFilterDecision(
            accepted=False,
            reason="Embedded role",
            normalized_title=normalized,
            positive_rules=positive_rules,
            negative_rules=negative_rules,
            decision="REJECT",
        )
    return TitleFilterDecision(
        accepted=True,
        reason="No incompatible title signal",
        normalized_title=normalized,
        positive_rules=positive_rules,
        negative_rules=negative_rules,
        decision="PASS",
    )


def _matched_whole_words(normalized_title: str, markers: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for marker in markers:
        if re.search(rf"\b{re.escape(marker)}\b", normalized_title, flags=re.IGNORECASE):
            hits.append(marker)
    return hits


def _normalize_title(title: str) -> str:
    normalized = title.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized
