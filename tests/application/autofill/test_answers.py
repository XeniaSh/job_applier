from __future__ import annotations

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.answers import ApplicationAnswerGenerator, professional_context_from_profile
from app.application.autofill.questions import QuestionKind, is_llm_eligible_question, map_question
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "Berlin, Germany",
            },
            "employment": {
                "current_title": "Backend Engineer",
                "years_of_experience": 7,
                "professional_tech_stack": ["Java", "Kotlin"],
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


class _RecordingLLM:
    def __init__(self, answer: str = "I build backend services in Java.", confident: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self.answer = answer
        self.confident = confident

    def create_short_application_answer(self, **kwargs: object) -> tuple[str, bool]:
        self.calls.append(kwargs)
        return self.answer, self.confident


def test_unknown_professional_free_text_is_llm_eligible() -> None:
    field = DiscoveredField(label="Briefly describe your technical background", field_type="textarea", required=True)
    mapped = map_question(field, _profile())
    assert mapped.kind is QuestionKind.PROFESSIONAL_FREE_TEXT
    assert mapped.fillable is False
    assert is_llm_eligible_question(field, mapped) is True


def test_llm_generates_short_answer_for_why_company() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    answer = generator.generate(field, _profile())
    assert answer is not None
    assert "Java" in answer or "backend" in answer.lower()
    assert llm.calls
    assert "Why do you want to work here?" in str(llm.calls[0]["question"])


def test_unsafe_legal_question_is_not_sent_to_llm() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="I consent to the processing of my personal data for recruiting",
        field_type="checkbox",
        required=True,
    )
    assert generator.generate(field, _profile()) is None
    assert llm.calls == []

    salary = DiscoveredField(label="Expected salary", required=True)
    assert generator.generate(salary, _profile()) is None
    relationship = DiscoveredField(
        label="Do you have a personal relationship with a current Agoda employee?",
        field_type="radio",
        options=["Yes", "No"],
    )
    assert generator.generate(relationship, _profile()) is None
    visa = DiscoveredField(label="Will you require visa sponsorship?")
    assert generator.generate(visa, _profile()) is None
    deloitte = DiscoveredField(
        label="Please confirm if you are currently or formerly associated with Deloitte or any of its subsidiary entities",
        field_type="select",
        options=["Yes, I am", "No, I am not a current/an ex-employee of Deloitte"],
    )
    assert generator.generate(deloitte, _profile()) is None
    source = DiscoveredField(
        label="How did you hear about this job?",
        field_type="select",
        options=["Company Website", "LinkedIn"],
    )
    assert generator.generate(source, _profile()) is None
    blog = DiscoveredField(
        label="Have you read our engineering blog?",
        field_type="select",
        options=["Yes", "No"],
    )
    assert generator.generate(blog, _profile()) is None
    gender = DiscoveredField(
        label="Gender*",
        field_type="select",
        options=["Prefer not to disclose", "Female", "Male"],
        required=True,
    )
    assert generator.generate(gender, _profile()) is None
    sms = DiscoveredField(
        label="Do you allow us to provide you TEXT/SMS updates of your interview process?",
        field_type="select",
        options=["Yes", "No"],
    )
    assert generator.generate(sms, _profile()) is None
    located = DiscoveredField(
        label="Are you located in the UK or Poland?",
        field_type="select",
        options=["Yes", "No"],
        required=True,
    )
    assert generator.generate(located, _profile()) is None
    restrictions = DiscoveredField(
        label="Are you subject to any employment agreements and/or post-employment restrictions?",
        field_type="select",
        options=["Yes", "No"],
        required=True,
    )
    assert generator.generate(restrictions, _profile()) is None
    consulted = DiscoveredField(
        label="Have you previously worked at or consulted for GitLab?",
        field_type="select",
        options=["Yes", "No"],
        required=True,
    )
    assert generator.generate(consulted, _profile()) is None
    primary = DiscoveredField(
        label="What is your primary programming language and/or framework?",
        field_type="text",
        required=True,
    )
    assert generator.generate(primary, _profile()) is None
    oss = DiscoveredField(
        label="Please share links of any open source projects you own or have made contributions to",
        field_type="textarea",
        required=True,
    )
    assert generator.generate(oss, _profile()) is None
    gitlab = DiscoveredField(label="What is your GitLab username?", field_type="text")
    assert generator.generate(gitlab, _profile()) is None
    current_visa = DiscoveredField(
        label="Will you now or in the future require sponsorship for a visa to remain in your current location?",
        field_type="select",
        options=["Yes, Netherlands Highly Skilled Migrant Visa", "No"],
        required=True,
    )
    assert generator.generate(current_visa, _profile()) is None
    assert llm.calls == []


def test_llm_select_answer_must_match_available_options() -> None:
    llm = _RecordingLLM(answer="Quantum computing")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="What interests you about this role?",
        field_type="select",
        options=["Backend services", "Mobile apps"],
    )
    assert generator.generate(field, _profile()) is None
    llm.answer = "Backend services"
    assert generator.generate(field, _profile()) == "Backend services"


def test_unconfident_llm_answer_is_discarded() -> None:
    llm = _RecordingLLM(answer="Maybe this.", confident=False)
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    assert generator.generate(field, _profile()) is None


def test_markdown_profile_narrative_facts_reach_llm_payload() -> None:
    """The richer narrative `candidate_profile.md` text (passed through as
    `markdown_profile`) must actually reach the LLM's `candidate_profile`
    argument, not just the compact `professional_context_from_profile`
    summary -- this is the seam `AutofillService` uses (see
    `_load_markdown_profile` in service.py) to give free-text answer
    generation the same richer factual context cover-letter generation
    already has.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Briefly describe your backend experience.", field_type="textarea")
    marker = "UNIQUE_NARRATIVE_MARKER: confirmed OpenShift and production incident investigation experience."
    answer = generator.generate(field, _profile(), markdown_profile=f"## Section\n\n{marker}")
    assert answer is not None
    assert llm.calls
    assert marker in str(llm.calls[0]["candidate_profile"])


def test_tracked_narrative_n8n_automation_facts_reach_llm_payload_for_ai_question() -> None:
    """The tracked `candidate_profile.md` (loaded for real, not a fake
    marker) must carry the verified internal n8n/OpenRouter automation
    facts -- and the boundaries/preference around them -- all the way into
    the LLM's `candidate_profile` argument for an AI-integration/automation
    free-text question, alongside the preserved "Job Applier" personal
    project facts. Exercises the same `markdown_profile` seam `AutofillService`
    uses in production, but against the real tracked file.
    """
    from app.profile_loader import load_candidate_profile

    narrative = load_candidate_profile()
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Describe an internal automation or AI integration you've built in your current role.",
        field_type="textarea",
    )
    mapped = map_question(field, _profile())
    assert mapped.kind is QuestionKind.PROFESSIONAL_FREE_TEXT
    assert is_llm_eligible_question(field, mapped) is True

    answer = generator.generate(field, _profile(), markdown_profile=narrative)
    assert answer is not None
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    # Markdown source-wraps long lines, so whitespace is normalized before
    # checking multi-word phrases that may span a line break in the file.
    normalized = " ".join(candidate_profile.split())

    # Verified n8n work-project facts.
    assert "n8n" in candidate_profile
    assert "Kubernetes" in candidate_profile
    assert "Jira" in candidate_profile
    assert "OpenRouter" in candidate_profile
    assert "Slack" in candidate_profile
    assert "requires further analysis" in normalized
    assert "ready for development" in normalized
    assert "requirements unclear" in normalized
    assert "internal work project, not a commercial AI product" in normalized
    assert "prefer this n8n project as the concrete example" in normalized

    # Preserved, distinct "Job Applier" personal project example.
    assert "Job Applier" in candidate_profile
    assert "personal automation project" in normalized

    # Preserved boundaries elsewhere in the profile are untouched.
    assert "an ML engineer" in normalized
    assert "production OpenAI or Claude API integrations" in normalized


def test_absent_markdown_profile_does_not_add_extra_context() -> None:
    """When no narrative profile text is available (e.g. the file failed to
    load), the candidate_profile payload must be exactly the compact
    summary -- no blank trailing section.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Briefly describe your backend experience.", field_type="textarea")
    generator.generate(field, _profile(), markdown_profile=None)
    assert llm.calls
    assert str(llm.calls[0]["candidate_profile"]) == professional_context_from_profile(_profile())

    llm.calls.clear()
    generator.generate(field, _profile(), markdown_profile="   ")
    assert llm.calls
    assert str(llm.calls[0]["candidate_profile"]) == professional_context_from_profile(_profile())


def test_ai_tool_brand_usage_question_never_reaches_llm() -> None:
    """Verified Everbridge posting ff6eacff-741a-4f60-aff4-a45e89b003a9 asks
    "Which AI coding or engineering tools have you used regularly in the
    past 6 months?" -- no specific tool brand/regular-usage fact is
    confirmed anywhere in the profile, and an LLM asked this directly is
    prone to inventing a plausible-sounding brand name, so this must never
    reach the LLM at all (fails closed unconditionally, independent of any
    confidence signal the LLM might otherwise return).
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Which AI coding or engineering tools have you used regularly in the past 6 months?",
        field_type="textarea",
        required=True,
    )
    assert generator.generate(field, _profile()) is None
    assert llm.calls == []


def test_recent_reapplication_question_never_reaches_llm() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Have you applied to this company in the past 6 months?",
        field_type="select",
        options=["Yes", "No"],
        required=True,
    )
    assert generator.generate(field, _profile()) is None
    assert llm.calls == []


def test_unrelated_recent_months_question_still_reaches_llm() -> None:
    """Regression guard: a professional free-text question merely sharing
    the same recency window as the re-application question (e.g. the
    Everbridge AI-task question) must not be swept into that unrelated
    forbidden category.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Describe one real software engineering task where you used AI meaningfully in the past 6 months.",
        field_type="textarea",
        required=True,
    )
    assert generator.generate(field, _profile()) is not None
    assert llm.calls


def test_profile_meta_reference_answer_is_rejected() -> None:
    """Verified Everbridge posting ff6eacff-741a-4f60-aff4-a45e89b003a9: the
    LLM confidently answered a frontend-experience question with "no
    frontend experience mentioned in my profile" -- a meta-reference to the
    profile document rather than natural first-person prose. The prompt now
    discourages this, but a confident LLM answer must still be rejected
    (fails closed to manual review) even if it ignores that instruction.
    """
    llm = _RecordingLLM(answer="No frontend experience mentioned in my profile.", confident=True)
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Describe your frontend experience.", field_type="textarea")
    assert generator.generate(field, _profile()) is None


def test_natural_backend_focused_answer_is_accepted() -> None:
    """Control for `test_profile_meta_reference_answer_is_rejected`: a
    natural first-person answer leaning on confirmed backend experience,
    with no profile-referential meta phrasing, must still be accepted.
    """
    llm = _RecordingLLM(
        answer=(
            "I haven't worked on frontend specifically, but I bring strong backend "
            "engineering experience with Java and Kotlin."
        ),
        confident=True,
    )
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Describe your frontend experience.", field_type="textarea")
    answer = generator.generate(field, _profile())
    assert answer is not None
    assert "backend" in answer.lower()


def test_professional_context_never_volunteers_academic_level() -> None:
    profile = _profile()
    profile.employment.highest_academic_level = "Master's"
    text = professional_context_from_profile(profile).lower()
    assert "academic" not in text
    assert "master" not in text
    assert "7 years" in text or "seven years" in text


def test_irrelevant_masters_mention_is_rejected_for_free_text_answer() -> None:
    llm = _RecordingLLM(answer="I also hold a master's degree, which helped me grow professionally.")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    assert generator.generate(field, _profile()) is None


def test_degree_mention_rejected_when_question_asks_about_qualifications() -> None:
    llm = _RecordingLLM(answer="I hold a bachelor's degree relevant to this role.")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Tell us about your qualifications for this role", field_type="textarea")
    assert generator.generate(field, _profile()) is None


def test_degree_mention_rejected_even_when_preferred_fields_of_interest_names_a_matching_degree() -> None:
    llm = _RecordingLLM(answer="I have a master's degree in Computer Science.")
    generator = ApplicationAnswerGenerator(llm)
    profile = _profile()
    profile.employment.preferred_fields_of_interest = ["Bachelor's degree in Computer Science"]
    field = DiscoveredField(label="Tell us about your qualifications for this role", field_type="textarea")
    assert generator.generate(field, profile) is None


def test_experience_wording_is_not_blocked_by_education_guard() -> None:
    llm = _RecordingLLM(answer="I bring around seven years of Java and Kotlin backend experience.")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    answer = generator.generate(field, _profile())
    assert answer == "I bring around seven years of Java and Kotlin backend experience."


def _nz_vacancy() -> ResolvedVacancy:
    return ResolvedVacancy(
        source="target_company:greenhouse:acme",
        external_id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://example.test/jobs/1",
        application_url="https://example.test/jobs/1",
        vacancy=NormalizedVacancy(
            source="target_company:greenhouse:acme",
            external_id="1",
            title="Backend Engineer",
            company="Acme",
            location="Auckland, New Zealand",
            employment=None,
            description="",
            url="https://example.test/jobs/1",
            published_at=None,
        ),
    )


def _non_nz_vacancy() -> ResolvedVacancy:
    return ResolvedVacancy(
        source="target_company:greenhouse:acme",
        external_id="2",
        title="Backend Engineer",
        company="Acme",
        url="https://example.test/jobs/2",
        application_url="https://example.test/jobs/2",
        vacancy=NormalizedVacancy(
            source="target_company:greenhouse:acme",
            external_id="2",
            title="Backend Engineer",
            company="Acme",
            location="Berlin, Germany",
            employment=None,
            description="",
            url="https://example.test/jobs/2",
            published_at=None,
        ),
    )


def test_nz_relevant_free_text_question_gets_relocation_facts() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="What draws you to working in New Zealand?",
        field_type="textarea",
    )
    generator.generate(field, _profile(), _nz_vacancy())
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    assert "planning a visit to New Zealand in 2026" in candidate_profile
    assert "no confirmed arrival date" in candidate_profile
    assert "willing to meet in person" in candidate_profile
    assert "employer-supported work visa" in candidate_profile
    # Never claims current NZ residence/authorization or a specific date.
    assert "mid-December" not in candidate_profile
    assert "valid" not in candidate_profile.lower()


def test_nz_unrelated_free_text_question_does_not_get_relocation_facts() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    generator.generate(field, _profile(), _nz_vacancy())
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    assert "New Zealand" not in candidate_profile
    assert "employer-supported work visa" not in candidate_profile


def test_non_nz_vacancy_free_text_question_mentioning_new_zealand_does_not_get_facts() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Please describe what draws you to working in New Zealand.",
        field_type="textarea",
    )
    generator.generate(field, _profile(), _non_nz_vacancy())
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    assert "employer-supported work visa" not in candidate_profile


def test_nz_vacancy_visa_sponsorship_question_still_excluded_from_llm() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Will you require visa sponsorship?")
    assert generator.generate(field, _profile(), _nz_vacancy()) is None
    assert llm.calls == []


def test_unrelated_label_with_new_zealand_in_context_does_not_get_facts() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="Why do you want to work here?",
        field_type="textarea",
        context="What draws you to working in New Zealand?",
    )
    generator.generate(field, _profile(), _nz_vacancy())
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    assert "New Zealand" not in candidate_profile
    assert "employer-supported work visa" not in candidate_profile


# Halter (Ashby posting 8eafdca9-3003-4730-b2ae-d1ddf9b7b7ce, Auckland) shows a
# `why_company` textarea, a generic catch-all `professional_free_text`
# textarea ("Anything else you want to share?"), and a separate cover letter
# file control. The catch-all is the only one of those that should carry the
# bounded relocation facts, built deterministically (never from the LLM, so
# an LLM-invented claim about visa/residence/relocation can never reach the
# form) unless another field already covers the same ground.


def test_nz_catchall_free_text_answer_is_deterministic_first_person_and_ignores_llm() -> None:
    """The catch-all answer never goes through the LLM at all, so an LLM
    returning an unsafe claim (falsely claiming current NZ work
    authorization) can never influence -- let alone appear in -- the final
    answer actually typed into the form.
    """
    llm = _RecordingLLM(answer="I already have a New Zealand work visa and have moved there.")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(field, _profile(), _nz_vacancy())
    assert answer is not None
    assert llm.calls == []
    assert "already have" not in answer
    assert "moved there" not in answer
    assert answer.startswith("I ")
    assert "roughly seven years of backend engineering experience" in answer
    assert "my professional stack includes Java and Kotlin" in answer
    assert "don't currently live in New Zealand" in answer
    assert "open to relocating" in answer.lower()
    assert "meet in person" in answer
    assert len([s for s in answer.split(". ") if s]) <= 3
    # Never claims current NZ residence/authorization or a specific date.
    assert "mid-December" not in answer
    assert "valid" not in answer.lower()


def test_nz_catchall_experience_sentence_never_implies_per_technology_years() -> None:
    """years_of_experience is a total-career figure, not a per-technology one.
    Wording it as "seven years of Python backend experience" would falsely
    claim seven years specifically in Python, so the years claim and the
    tech stack must stay in separate clauses.
    """
    llm = _RecordingLLM(answer="irrelevant -- catch-all bypasses the LLM")
    generator = ApplicationAnswerGenerator(llm)
    profile = _profile()
    profile.employment.professional_tech_stack = ["Python", "Kotlin"]
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(field, profile, _nz_vacancy())
    assert answer is not None
    assert "seven years of Python" not in answer
    assert "roughly seven years of backend engineering experience" in answer
    assert "my professional stack includes Python and Kotlin" in answer


def test_nz_why_company_field_still_requires_explicit_new_zealand_label() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    generator.generate(field, _profile(), _nz_vacancy())
    assert llm.calls
    candidate_profile = str(llm.calls[0]["candidate_profile"])
    assert "New Zealand" not in candidate_profile


def test_nz_generic_professional_free_text_question_is_not_treated_as_catchall() -> None:
    """Only a true catch-all prompt ("Anything else...") volunteers the
    facts -- an unrelated `professional_free_text` question such as a
    technical-background prompt must never get them, even on an NZ vacancy.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Briefly describe your technical background", field_type="textarea")
    mapped = map_question(field, _profile())
    assert mapped.kind is QuestionKind.PROFESSIONAL_FREE_TEXT
    answer = generator.generate(field, _profile(), _nz_vacancy())
    assert answer is not None
    assert "New Zealand" not in answer
    assert "employer-supported work visa" not in answer


def test_nz_catchall_free_text_skips_facts_when_sibling_why_company_names_new_zealand() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(
        field,
        _profile(),
        _nz_vacancy(),
        sibling_has_dedicated_new_zealand_question=True,
    )
    assert answer is not None
    assert "New Zealand" not in answer
    assert "employer-supported work visa" not in answer


def test_nz_catchall_free_text_sibling_relocation_question_suppresses_only_the_relocation_fact() -> None:
    """A dedicated sibling `relocation` question already states willingness
    to relocate, so the catch-all must not repeat it -- but the visit/
    in-person fact, which no sibling asks about, must still come through.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(
        field,
        _profile(),
        _nz_vacancy(),
        sibling_kinds=frozenset({QuestionKind.RELOCATION}),
    )
    assert answer is not None
    assert "open to relocating" not in answer.lower()
    assert "meet in person" in answer
    assert "employer-supported work visa" in answer


def test_nz_catchall_free_text_sibling_visa_question_suppresses_only_the_visa_fact() -> None:
    """A dedicated sibling `visa_sponsorship` question already covers the
    work-visa topic, so the catch-all must not repeat it -- but the visit/
    in-person fact, which no sibling asks about, must still come through.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(
        field,
        _profile(),
        _nz_vacancy(),
        sibling_kinds=frozenset({QuestionKind.VISA_SPONSORSHIP}),
    )
    assert answer is not None
    assert "employer-supported work visa" not in answer
    assert "meet in person" in answer
    assert "open to relocating" in answer.lower()


def test_remote_nz_vacancy_catchall_free_text_question_does_not_get_relocation_facts() -> None:
    """A remote-only NZ vacancy never requires relocating or visiting in
    person, so the deterministic catch-all must not fire -- the question
    falls through to the normal LLM path instead.
    """
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    remote_nz_vacancy = ResolvedVacancy(
        source="target_company:greenhouse:acme",
        external_id="3",
        title="Backend Engineer",
        company="Acme",
        url="https://example.test/jobs/3",
        application_url="https://example.test/jobs/3",
        vacancy=NormalizedVacancy(
            source="target_company:greenhouse:acme",
            external_id="3",
            title="Backend Engineer",
            company="Acme",
            location="Remote - New Zealand",
            employment=None,
            description="",
            url="https://example.test/jobs/3",
            published_at=None,
        ),
    )
    answer = generator.generate(field, _profile(), remote_nz_vacancy)
    assert llm.calls
    assert answer is not None
    assert "employer-supported work visa" not in answer
    assert "open to relocating" not in answer.lower()


def test_non_nz_vacancy_catchall_free_text_question_does_not_get_facts() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Anything else you want to share?", field_type="textarea")
    answer = generator.generate(field, _profile(), _non_nz_vacancy())
    assert answer is not None
    assert "New Zealand" not in answer
    assert "employer-supported work visa" not in answer
