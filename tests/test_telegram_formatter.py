from app.telegram.formatter import format_prepared_application_html, format_telegram_card_html
from app.telegram.models import TelegramVacancyCard


def test_formatter_escapes_html_and_limits_lists() -> None:
    card = TelegramVacancyCard(
        source="li",
        external_id="123",
        decision="POTENTIAL_MATCH",
        title='Java <Lead> & "Kotlin"',
        company="ACME <Corp>",
        location="Taguig & Metro",
        url="https://www.linkedin.com/jobs/view/123/",
        match_percentage=87.5,
        gaps=["redis", "webflux", "extra"],
        nuances=["Нужно <проверить>", "Удаленка & география", "Третья", "Четвертая"],
        recommended_resume="java-backend",
        content_completeness="PARTIAL",
    )

    rendered = format_telegram_card_html(card)
    assert "<b>POTENTIAL_MATCH · Java &lt;Lead&gt; &amp; &quot;Kotlin&quot;</b>" in rendered
    assert "ACME &lt;Corp&gt;" in rendered
    assert "Стек: 87.5%" in rendered
    assert "— redis" in rendered
    assert "— webflux" in rendered
    assert "— extra" not in rendered
    assert "⚠️ Нужно &lt;проверить&gt;" in rendered
    assert "⚠️ Четвертая" not in rendered


def test_formatter_caps_message_length() -> None:
    very_long = "x" * 5000
    card = TelegramVacancyCard(
        source="li",
        external_id="123",
        decision="STRONG_MATCH",
        title=very_long,
        company=None,
        location=None,
        url="https://www.linkedin.com/jobs/view/123/",
        match_percentage=None,
        gaps=[],
        nuances=[],
        recommended_resume="java-backend",
        content_completeness="MINIMAL",
    )
    rendered = format_telegram_card_html(card)
    assert len(rendered) <= 3500


def test_prepared_layout_human_resume_and_plain_cover_letter_en() -> None:
    rendered = format_prepared_application_html(
        title="Backend Lead (Java/Kotlin)",
        company="Salmon Group Ltd",
        language="en",
        recommended_resume="java-backend",
        cover_letter="I have around seven years of backend experience.",
        warnings=[
            "Описание вакансии неполное — требуется открыть LinkedIn",
            "Локация требует дополнительного уточнения",
            "Роль уровня Lead — стоит проверить ожидания",
        ],
    )
    assert "✅ Application Ready" in rendered
    assert "<b>📄 Resume</b>" in rendered
    assert "Java Backend" in rendered
    assert "<b>📄 Cover Letter (EN)</b>" in rendered
    assert "<pre>" not in rendered
    assert "<b>⚠️ Notes</b>" in rendered
    assert "• ⚠️ Incomplete description" in rendered
    assert "• ⚠️ Verify location" in rendered
    assert "• ⚠️ Verify Lead responsibilities" in rendered


def test_prepared_layout_russian_cover_letter_header_and_resume_mapping() -> None:
    rendered = format_prepared_application_html(
        title="Java Backend Engineer",
        company=None,
        language="ru",
        recommended_resume="fintech-backend",
        cover_letter="Здравствуйте! У меня около семи лет опыта.",
        warnings=["Локация требует дополнительного уточнения"],
    )
    assert "<b>📄 Сопроводительное письмо</b>" in rendered
    assert "FinTech Backend" in rendered
    assert "• ⚠️ Уточнить локацию" in rendered
