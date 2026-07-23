from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models import Decision

logger = logging.getLogger(__name__)

ABOVE_SENIORITY_MARKERS = ("staff", "principal", "distinguished", "fellow")

EDUCATION_ROLE_MARKERS = (
    "teacher",
    "trainer",
    "instructor",
    "lecturer",
    "professor",
    "tutor",
    "curriculum",
    "учитель",
    "преподаватель",
    "тренер",
    "инструктор",
    "лектор",
    "профессор",
)

NON_ENGINEERING_IGNORE_MARKERS = (
    "recruiter",
    "recruiting",
    "sales",
    "content writer",
    "technical writer",
    "copywriter",
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

QA_ROLE_MARKERS = (
    "qa",
    "tester",
    "test automation",
    "sdet",
    "quality assurance",
)

ANDROID_ROLE_MARKERS = (
    "android",
)

JVM_MULTIWORD_MARKERS = (
    ("spring boot", r"\bspring\s+boot\b"),
)

JVM_WORD_MARKERS = (
    ("java", r"\bjava\b"),
    ("kotlin", r"\bkotlin\b"),
    ("jvm", r"\bjvm\b"),
    ("spring", r"\bspring\b"),
)

BACKEND_ROLE_PATTERNS = (
    ("backend", r"\bbackend\b"),
    ("back-end", r"\bback-end\b"),
    ("back end", r"\bback\s+end\b"),
    ("software engineer", r"\bsoftware\s+engineer\b"),
    ("software developer", r"\bsoftware\s+developer\b"),
    ("developer", r"\bdeveloper\b"),
    ("engineer", r"\bengineer\b"),
)

RULE_EXPLICIT_JVM_BACKEND = "EXPLICIT_JVM_BACKEND_TITLE"
RULE_INCOMPATIBLE_EDUCATION = "INCOMPATIBLE_EDUCATION_ROLE"
RULE_INCOMPATIBLE_NON_ENGINEERING = "INCOMPATIBLE_NON_ENGINEERING_ROLE"
RULE_JAVA_QA_DOWNGRADE = "JAVA_QA_DOWNGRADE"
RULE_JAVA_ANDROID_DOWNGRADE = "JAVA_ANDROID_DOWNGRADE"
RULE_LLM_FALLBACK = "LLM_FALLBACK"


@dataclass(frozen=True)
class TitleMatchClassification:
    """Deterministic match strength from title alone (before LLM)."""

    match_strength: Decision | None
    rule: str
    reason: str
    llm_skipped: bool
    jvm_hits: tuple[str, ...] = ()
    role_hits: tuple[str, ...] = ()
    negative_hits: tuple[str, ...] = ()

    @property
    def is_deterministic(self) -> bool:
        return self.match_strength is not None


def normalize_title(title: str) -> str:
    normalized = title.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def matched_whole_words(normalized_title: str, markers: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for marker in markers:
        if re.search(rf"\b{re.escape(marker)}\b", normalized_title, flags=re.IGNORECASE):
            hits.append(marker)
    return hits


def matched_phrase(normalized_title: str, markers: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for marker in markers:
        if " " in marker:
            if marker in normalized_title:
                hits.append(marker)
        elif re.search(rf"\b{re.escape(marker)}\b", normalized_title, flags=re.IGNORECASE):
            hits.append(marker)
    return hits


def jvm_title_hits(normalized_title: str) -> list[str]:
    """True JVM signals with word boundaries (java ≠ javascript)."""
    hits: list[str] = []
    for label, pattern in JVM_MULTIWORD_MARKERS:
        if re.search(pattern, normalized_title, flags=re.IGNORECASE):
            hits.append(label)
    for label, pattern in JVM_WORD_MARKERS:
        if label == "spring" and "spring boot" in hits:
            continue
        if re.search(pattern, normalized_title, flags=re.IGNORECASE):
            hits.append(label)
    return hits


def backend_role_hits(normalized_title: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in BACKEND_ROLE_PATTERNS:
        if re.search(pattern, normalized_title, flags=re.IGNORECASE):
            hits.append(label)
    return hits


def classify_title_match(title: str) -> TitleMatchClassification:
    """
    Deterministic title classification order:
    1. hard negative / incompatible role → IGNORE
    2. special-case downgrade (QA/Android + Java) → POTENTIAL
    3. explicit Java/JVM backend → STRONG
    4. otherwise → LLM fallback (match_strength=None)
    """
    normalized = normalize_title(title)
    jvm_hits = tuple(jvm_title_hits(normalized))
    role_hits = tuple(backend_role_hits(normalized))

    education_hits = matched_whole_words(normalized, EDUCATION_ROLE_MARKERS)
    if education_hits:
        return TitleMatchClassification(
            match_strength=Decision.IGNORE,
            rule=RULE_INCOMPATIBLE_EDUCATION,
            reason="Incompatible education/teaching role in title.",
            llm_skipped=True,
            jvm_hits=jvm_hits,
            role_hits=role_hits,
            negative_hits=tuple(education_hits),
        )

    non_eng_hits = matched_phrase(normalized, NON_ENGINEERING_IGNORE_MARKERS)
    if non_eng_hits:
        return TitleMatchClassification(
            match_strength=Decision.IGNORE,
            rule=RULE_INCOMPATIBLE_NON_ENGINEERING,
            reason="Incompatible non-engineering role in title.",
            llm_skipped=True,
            jvm_hits=jvm_hits,
            role_hits=role_hits,
            negative_hits=tuple(non_eng_hits),
        )

    qa_hits = matched_phrase(normalized, QA_ROLE_MARKERS)
    if jvm_hits and qa_hits:
        return TitleMatchClassification(
            match_strength=Decision.POTENTIAL_MATCH,
            rule=RULE_JAVA_QA_DOWNGRADE,
            reason="Java mentioned with QA/test role; stack fit is uncertain.",
            llm_skipped=True,
            jvm_hits=jvm_hits,
            role_hits=role_hits,
            negative_hits=tuple(qa_hits),
        )

    android_hits = matched_whole_words(normalized, ANDROID_ROLE_MARKERS)
    if jvm_hits and android_hits:
        return TitleMatchClassification(
            match_strength=Decision.POTENTIAL_MATCH,
            rule=RULE_JAVA_ANDROID_DOWNGRADE,
            reason="Java mentioned with Android role; not a clear backend match.",
            llm_skipped=True,
            jvm_hits=jvm_hits,
            role_hits=role_hits,
            negative_hits=tuple(android_hits),
        )

    if jvm_hits and role_hits:
        return TitleMatchClassification(
            match_strength=Decision.STRONG_MATCH,
            rule=RULE_EXPLICIT_JVM_BACKEND,
            reason="Explicit Java + backend signals in title",
            llm_skipped=True,
            jvm_hits=jvm_hits,
            role_hits=role_hits,
        )

    return TitleMatchClassification(
        match_strength=None,
        rule=RULE_LLM_FALLBACK,
        reason="No deterministic title rule matched.",
        llm_skipped=False,
        jvm_hits=jvm_hits,
        role_hits=role_hits,
    )


def log_classification_rule(
    *,
    title: str,
    classification: TitleMatchClassification,
    content_completeness: str | None = None,
) -> None:
    parts = [
        f'CLASSIFICATION_RULE title="{title}"',
        f"rule={classification.rule}",
    ]
    if classification.match_strength is not None:
        strength = {
            Decision.STRONG_MATCH: "STRONG",
            Decision.POTENTIAL_MATCH: "POTENTIAL",
            Decision.IGNORE: "IGNORE",
        }.get(classification.match_strength, classification.match_strength.value)
        parts.append(f"match_strength={strength}")
    if content_completeness:
        parts.append(f"content_completeness={content_completeness.upper()}")
    parts.append(f"llm_skipped={classification.llm_skipped}")
    logger.info(" ".join(parts))


def incomplete_description_info(content_completeness: str) -> str | None:
    completeness = content_completeness.upper().strip()
    if completeness in {"PARTIAL", "MINIMAL"}:
        return "Job description is not available in the LinkedIn email"
    return None
