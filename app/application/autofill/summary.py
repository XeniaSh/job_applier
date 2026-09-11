from __future__ import annotations

from app.application.autofill.models import AutofillFieldResult, AutofillResult, AutofillStatus


def render_autofill_summary(result: AutofillResult) -> str:
    heading = {
        AutofillStatus.READY_FOR_REVIEW: "Autofill completed.",
        AutofillStatus.NEEDS_MANUAL_INTERVENTION: "Autofill needs manual intervention.",
        AutofillStatus.FAILED: "Autofill failed.",
    }[result.status]
    lines = [
        heading,
        "",
        "Filled:",
        *_label_lines(result.filled_fields),
        "",
        "Needs review:",
        *_label_lines(result.unresolved_required_fields + result.unresolved_optional_fields),
        "",
        "Unsupported:",
        *_label_lines(result.unsupported_fields),
        "",
        "Generated (needs review):",
        *_label_lines(result.generated_fields),
        "",
        "Sensitive (not filled):",
        *_label_lines(result.sensitive_fields),
        "",
        "Resume:",
        "Uploaded successfully" if result.resume_uploaded else "Not uploaded",
        "",
        "Cover letter:",
        "Filled" if result.cover_letter_filled else "Not filled",
        "",
        "Submit:",
        "NOT PERFORMED",
    ]
    if result.warnings:
        lines.extend(["", "Warnings:", *[f"- {item}" for item in result.warnings]])
    return "\n".join(lines)


def _label_lines(fields: list[AutofillFieldResult]) -> list[str]:
    labels = [item.label.strip() for item in fields if item.label.strip()]
    if not labels:
        return ["- None"]
    return [f"- {label}" for label in labels]
