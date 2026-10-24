from __future__ import annotations

import pytest

from app.resume_policy import (
    ResumePolicyInput,
    ResumeVariant,
    decide_resume_variant,
    force_non_ai,
    normalize_resume_variant_id,
)


def _signals(**kwargs) -> ResumePolicyInput:
    return ResumePolicyInput(**kwargs)


# --- AI dimension -----------------------------------------------------


def test_substantial_hands_on_ai_in_responsibilities_selects_ai_variant() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            responsibilities=["Build and operate a retrieval augmented generation pipeline for support search"],
            mandatory_skills=["Java", "Spring Boot"],
        )
    )
    assert decision.is_ai is True
    assert decision.variant is ResumeVariant.AI_BACKEND


def test_llm_integration_in_mandatory_skills_selects_ai_variant() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            mandatory_skills=["Java", "LLM integration experience"],
        )
    )
    assert decision.is_ai is True


def test_casual_employer_ai_marketing_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            responsibilities=["Build backend services for our AI-powered marketing platform"],
            mandatory_skills=["Java", "Spring Boot"],
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_ai_assisted_coding_tool_mention_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            responsibilities=["We use GitHub Copilot and other AI-assisted coding tools daily"],
            mandatory_skills=["Java"],
        )
    )
    assert decision.is_ai is False


def test_optional_nice_to_have_ai_skill_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            mandatory_skills=["Java", "Spring Boot"],
            optional_skills=["Nice to have: machine learning experience"],
        )
    )
    assert decision.is_ai is False


def test_negated_ai_requirement_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            responsibilities=["No machine learning experience is required for this role"],
            mandatory_skills=["Java"],
        )
    )
    assert decision.is_ai is False


def test_title_only_fallback_detects_hands_on_ai_when_extraction_is_empty() -> None:
    decision = decide_resume_variant(
        _signals(title="Generative AI / LLM Integration Engineer", description="Backend role")
    )
    assert decision.is_ai is True


def test_title_only_fallback_stays_java_without_hands_on_evidence() -> None:
    decision = decide_resume_variant(_signals(title="Retail Backend Engineer", description="Backend role"))
    assert decision.is_ai is False


def test_raw_fallback_marketing_llm_mention_stays_java() -> None:
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="We are an LLM-powered company. You will maintain Java APIs.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_nice_to_have_llm_stays_java() -> None:
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Build Spring services. Nice to have: LLM integration experience.",
        )
    )
    assert decision.is_ai is False


def test_raw_fallback_fulfillment_substring_does_not_match_llm() -> None:
    decision = decide_resume_variant(
        _signals(title="Java Backend Engineer", description="Build order fulfillment APIs.")
    )
    assert decision.is_ai is False


def test_raw_fallback_llm_coding_assistant_mention_stays_java() -> None:
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Use LLM coding assistants to develop Java services.",
        )
    )
    assert decision.is_ai is False


def test_raw_fallback_genuine_llm_integration_duty_selects_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            title="Backend Engineer",
            description="Build LLM integrations and AI automation workflows for our backend.",
        )
    )
    assert decision.is_ai is True
    assert decision.variant is ResumeVariant.AI_BACKEND


def test_raw_fallback_ai_integration_title_selects_ai() -> None:
    decision = decide_resume_variant(
        _signals(title="Software Engineer, AI Integration", description="Backend role.")
    )
    assert decision.is_ai is True


def test_raw_fallback_you_will_integrate_llm_apis_selects_ai() -> None:
    decision = decide_resume_variant(
        _signals(title="Backend Engineer", description="You will integrate LLM APIs into backend services.")
    )
    assert decision.is_ai is True
    assert decision.variant is ResumeVariant.AI_BACKEND


def test_raw_fallback_rag_pipeline_duty_selects_ai() -> None:
    decision = decide_resume_variant(
        _signals(title="Backend Engineer", description="Develop RAG pipelines for our search product.")
    )
    assert decision.is_ai is True


def test_raw_fallback_company_marketing_llm_platform_with_ordinary_duty_stays_java() -> None:
    """Employer self-description ("We build an LLM-powered platform") must
    not count as the role's substantial AI responsibility, even though it
    uses a hands-on verb -- the verb's subject is the company, not the
    candidate.
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="We build an LLM-powered platform. You will maintain our Java APIs.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_distinct_responsibility_entries_copilot_and_llm_integration_select_ai() -> None:
    """A coding-assistant disqualifier in one responsibility entry must not
    bleed into a genuinely separate entry that states real AI/LLM work.
    """
    decision = decide_resume_variant(
        _signals(
            role_type="Backend Engineer",
            responsibilities=[
                "Use GitHub Copilot coding assistants",
                "Build LLM integrations and automation workflows",
            ],
        )
    )
    assert decision.is_ai is True
    assert decision.variant is ResumeVariant.AI_BACKEND


def test_llm_coding_tool_mention_in_responsibilities_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(
            role_type="Java Backend Engineer",
            responsibilities=["Use LLM coding tools to develop Java services"],
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_bare_llm_familiarity_mandatory_skill_does_not_force_ai() -> None:
    decision = decide_resume_variant(
        _signals(role_type="Java Backend Engineer", mandatory_skills=["Java", "Familiarity with LLMs"])
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_ordinary_duty_on_llm_powered_platform_stays_java() -> None:
    """A generic backend duty ("maintain APIs") on an AI-branded product
    sentence ("for our LLM-powered platform") does not prove the candidate's
    own duty is AI integration -- the marker only names the employer's
    product, not the verb's object.
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Maintain Java APIs for our LLM-powered platform.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_ordinary_duty_on_ai_driven_product_stays_java() -> None:
    """Closes the same gap as the LLM-powered-platform case above for a
    different marker phrase ("ai-driven product").
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Write integration tests for our ai-driven product.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_for_an_llm_powered_platform_stays_java() -> None:
    """Generalizes the "for our" employer-product phrase to any preposition
    plus determiner ("for an"), not just the single literal string "for
    our" -- the candidate's actual duty ("Build Java payment APIs") must
    still be the resume signal, not the employer's product description.
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Build Java payment APIs for an LLM-powered platform.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_at_our_llm_powered_company_stays_java() -> None:
    """Closes the same gap for a different preposition ("at our") and a
    different employer noun ("company" rather than "platform").
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description="Develop Java APIs at our LLM-powered company.",
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_raw_fallback_our_company_is_building_llm_platform_stays_java() -> None:
    """"Our company is building ..." must be recognized as an employer
    self-reference subject just like "We build ...", so the hands-on verb in
    that sentence never counts as the candidate's own duty, even when the
    genuine duty ("maintain payment APIs") is in a separate sentence with no
    AI marker at all.
    """
    decision = decide_resume_variant(
        _signals(
            title="Java Backend Engineer",
            description=(
                "Our company is building an LLM-powered platform. "
                "Your role is to maintain payment APIs."
            ),
        )
    )
    assert decision.is_ai is False
    assert decision.variant is ResumeVariant.JAVA_BACKEND


def test_force_non_ai_overrides_and_recomputes_variant() -> None:
    decision = decide_resume_variant(
        _signals(role_type="AI Automation Engineer", mandatory_skills=["LLM integration"])
    )
    assert decision.is_ai is True
    forced = force_non_ai(decision, reason="override")
    assert forced.is_ai is False
    assert forced.ai_reason == "override"
    assert forced.variant is ResumeVariant.JAVA_BACKEND


# --- New Zealand dimension ---------------------------------------------


def test_auckland_location_selects_nz_variant() -> None:
    decision = decide_resume_variant(_signals(location="Auckland, New Zealand"))
    assert decision.is_nz is True
    assert decision.location_warning is None


def test_wellington_location_selects_nz_variant() -> None:
    decision = decide_resume_variant(_signals(location="Wellington"))
    assert decision.is_nz is True


def test_nz_country_label_selects_nz_variant() -> None:
    decision = decide_resume_variant(_signals(location="NZ"))
    assert decision.is_nz is True


def test_new_zealand_country_label_selects_nz_variant() -> None:
    decision = decide_resume_variant(_signals(location="New Zealand"))
    assert decision.is_nz is True


def test_worldwide_remote_does_not_select_nz_variant() -> None:
    decision = decide_resume_variant(_signals(location="Remote - Worldwide"))
    assert decision.is_nz is False
    assert decision.location_warning is None


def test_nz_specific_remote_position_qualifies() -> None:
    decision = decide_resume_variant(_signals(location="Remote (New Zealand only)"))
    assert decision.is_nz is True


def test_employer_office_mention_outside_location_field_does_not_select_nz() -> None:
    """An employer-office mention belongs in `description`, never `location`;
    the NZ decision must only ever look at the structured location field.
    """
    decision = decide_resume_variant(
        _signals(
            location="Austin, United States",
            description="Our company also has an office in Auckland, New Zealand.",
        )
    )
    assert decision.is_nz is False
    assert decision.location_warning is None


def test_conflicting_multi_country_location_falls_back_to_non_nz_with_warning() -> None:
    decision = decide_resume_variant(_signals(location="New Zealand or Australia"))
    assert decision.is_nz is False
    assert decision.location_warning is not None
    assert "New Zealand" in decision.location_warning


def test_missing_location_falls_back_to_non_nz_with_warning() -> None:
    decision = decide_resume_variant(_signals(location=None))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_empty_location_falls_back_to_non_nz_with_warning() -> None:
    decision = decide_resume_variant(_signals(location="   "))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_explicit_nz_location_restrictions_do_not_confirm_nz_when_location_is_missing() -> None:
    """`location_restrictions` text is never read by the New Zealand
    decision, even when it explicitly and unambiguously says "must reside
    in New Zealand" -- fuzzy inference over extracted/free-form restriction
    text cannot safely affirm the position's actual country, so a missing
    structured `location` always falls back to the non-New Zealand variant
    with the existing uncertainty warning.
    """
    decision = decide_resume_variant(
        _signals(location=None, location_restrictions=["must reside in new zealand"])
    )
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_non_nz_ordinary_location_has_no_warning() -> None:
    decision = decide_resume_variant(_signals(location="Berlin, Germany"))
    assert decision.is_nz is False
    assert decision.location_warning is None


def test_remote_worldwide_parenthetical_nz_is_ambiguous_not_nz() -> None:
    decision = decide_resume_variant(_signals(location="Remote Worldwide (New Zealand)"))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_nz_or_remote_worldwide_is_ambiguous_not_nz() -> None:
    decision = decide_resume_variant(_signals(location="New Zealand or Remote Worldwide"))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_nz_dash_australia_is_conflicting_not_nz() -> None:
    decision = decide_resume_variant(_signals(location="NZ - Australia"))
    assert decision.is_nz is False
    assert decision.location_warning is not None
    assert "New Zealand" in decision.location_warning


def test_negated_not_new_zealand_is_not_nz() -> None:
    decision = decide_resume_variant(_signals(location="Not New Zealand"))
    assert decision.is_nz is False


def test_bare_remote_surfaces_unknown_geography_warning() -> None:
    decision = decide_resume_variant(_signals(location="Remote"))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_ambiguous_hamilton_city_alone_is_not_nz_with_warning() -> None:
    decision = decide_resume_variant(_signals(location="Hamilton"))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_hamilton_with_new_zealand_country_confirms_nz() -> None:
    decision = decide_resume_variant(_signals(location="Hamilton, New Zealand"))
    assert decision.is_nz is True
    assert decision.location_warning is None


@pytest.mark.parametrize(
    "location_restrictions",
    [
        pytest.param(["must reside in new zealand"], id="explicit_unambiguous_nz_text"),
        pytest.param(["Our office is based in New Zealand"], id="employer_office_decoy"),
        pytest.param(["Company headquarters located in New Zealand"], id="employer_headquarters_decoy"),
        pytest.param(["No New Zealand visa required"], id="negated_visa_mention"),
        pytest.param(
            ["Must reside in Australia; employer has offices in New Zealand"],
            id="australia_restriction_with_unrelated_nz_office_semicolon",
        ),
        pytest.param(
            ["Must reside in Australia", "Employer has offices in New Zealand"],
            id="australia_restriction_with_unrelated_nz_office_separate_entries",
        ),
        pytest.param(
            ["Must reside in Australia and employer has offices in New Zealand"],
            id="australia_restriction_with_unrelated_nz_office_same_clause",
        ),
        pytest.param(["Must reside in New Zealand or Australia"], id="nz_or_australia_mixed_restriction"),
        pytest.param(
            ["Must be based in New Zealand and our headquarters are located in Germany"],
            id="genuine_nz_restriction_with_unrelated_office_country",
        ),
    ],
)
def test_location_restrictions_never_confirm_nz_when_structured_location_is_missing(
    location_restrictions: list[str],
) -> None:
    """`location_restrictions` is never read by the New Zealand decision --
    only the vacancy's own structured `location` field can affirm New
    Zealand (see `ResumePolicyInput`'s docstring and `app.resume_policy`'s
    module docstring). A missing structured location always falls back to
    the non-New Zealand variant with the existing uncertainty warning, no
    matter how explicit, ambiguous, or decoy-laden the restriction text is.
    """
    decision = decide_resume_variant(_signals(location=None, location_restrictions=location_restrictions))
    assert decision.is_nz is False
    assert decision.location_warning is not None


def test_worldwide_including_new_zealand_single_segment_is_ambiguous_not_nz() -> None:
    """A broadening word and a New Zealand mention with no separator at all
    (one location-field segment) is the same ambiguity as a separate
    "worldwide" segment -- conservatively non-NZ with a warning.
    """
    decision = decide_resume_variant(_signals(location="Remote worldwide including New Zealand"))
    assert decision.is_nz is False
    assert decision.location_warning is not None


# --- Combined four-variant canonical ids -------------------------------


def test_java_backend_variant_id() -> None:
    decision = decide_resume_variant(_signals(location="Berlin, Germany"))
    assert decision.variant.value == "java_backend"


def test_ai_backend_variant_id() -> None:
    decision = decide_resume_variant(
        _signals(mandatory_skills=["LLM integration"], location="Berlin, Germany")
    )
    assert decision.variant.value == "ai_backend"


def test_java_backend_nz_variant_id() -> None:
    decision = decide_resume_variant(_signals(location="Auckland, New Zealand"))
    assert decision.variant.value == "java_backend_nz"


def test_ai_backend_nz_variant_id() -> None:
    decision = decide_resume_variant(
        _signals(mandatory_skills=["LLM integration"], location="Auckland, New Zealand")
    )
    assert decision.variant.value == "ai_backend_nz"


# --- Legacy alias normalization ----------------------------------------


def test_normalize_resume_variant_id_accepts_canonical_ids() -> None:
    assert normalize_resume_variant_id("ai_backend") == "ai_backend"


def test_normalize_resume_variant_id_maps_legacy_aliases() -> None:
    assert normalize_resume_variant_id("java") == "java_backend"
    assert normalize_resume_variant_id("java_ai") == "ai_backend"


def test_normalize_resume_variant_id_rejects_unknown() -> None:
    assert normalize_resume_variant_id("something_else") is None
    assert normalize_resume_variant_id("") is None
