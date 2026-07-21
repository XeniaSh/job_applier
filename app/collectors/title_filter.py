from __future__ import annotations

import re
from dataclasses import dataclass


ACCEPT_KEYWORDS = ("java", "kotlin", "jvm", "backend", "spring")
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
)


@dataclass(frozen=True)
class TitleFilterDecision:
    accepted: bool
    reason: str


def should_accept_title(title: str) -> bool:
    return evaluate_title(title).accepted


def evaluate_title(title: str) -> TitleFilterDecision:
    normalized = _normalize_title(title)
    has_accept = any(keyword in normalized for keyword in ACCEPT_KEYWORDS)
    has_jvm_language = any(keyword in normalized for keyword in ("java", "kotlin", "jvm"))
    backend_context = any(keyword in normalized for keyword in ("backend", "back-end", "spring"))
    has_reject = any(keyword in normalized for keyword in REJECT_KEYWORDS)
    if has_jvm_language and backend_context:
        return TitleFilterDecision(accepted=True, reason="JVM language with backend context overrides reject marker")
    if has_reject:
        if any(keyword in normalized for keyword in ("frontend", "front-end", "react", "angular")):
            return TitleFilterDecision(accepted=False, reason="Frontend title")
        if any(keyword in normalized for keyword in ("mobile",)):
            return TitleFilterDecision(accepted=False, reason="Mobile role")
        if any(keyword in normalized for keyword in ("data scientist", "ml engineer")):
            return TitleFilterDecision(accepted=False, reason="Data/ML role")
        if any(keyword in normalized for keyword in ("qa", "tester")):
            return TitleFilterDecision(accepted=False, reason="QA/test role")
        if any(keyword in normalized for keyword in ("devops", "support", "analyst", "python", "c#", ".net", "php")):
            return TitleFilterDecision(accepted=False, reason="Title targets a non-Java specialization")
    if not has_accept:
        return TitleFilterDecision(
            accepted=False,
            reason="Generic software title without Java/backend evidence",
        )
    if not has_reject:
        return TitleFilterDecision(accepted=True, reason="Title has Java/backend role signal")
    return TitleFilterDecision(
        accepted=False,
        reason="Title does not contain an allowed Java/backend role signal",
    )


def _normalize_title(title: str) -> str:
    normalized = title.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized
