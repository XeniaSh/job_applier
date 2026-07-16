from __future__ import annotations

import re


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


def should_accept_title(title: str) -> bool:
    normalized = _normalize_title(title)
    has_accept = any(keyword in normalized for keyword in ACCEPT_KEYWORDS)
    if not has_accept:
        return False

    has_jvm_language = any(keyword in normalized for keyword in ("java", "kotlin", "jvm"))
    backend_context = any(keyword in normalized for keyword in ("backend", "back-end", "spring"))
    has_reject = any(keyword in normalized for keyword in REJECT_KEYWORDS)
    if not has_reject:
        return True

    if has_jvm_language and backend_context:
        return True
    return False


def _normalize_title(title: str) -> str:
    normalized = title.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized
