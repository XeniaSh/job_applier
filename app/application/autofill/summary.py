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
        lines.extend(["", "Warnings:"])
        for item in result.warnings:
            if "\n" in item:
                lines.append(item)
            else:
                lines.append(f"- {item}")
    return "\n".join(lines)


def format_privacy_acknowledgement_report(trace: dict[str, object]) -> str:
    discovered_text = "yes" if trace.get("discovered") else "no"
    readback = trace.get("readback_checked")
    if readback is True:
        readback_text = "true"
    elif readback is False:
        readback_text = "false"
    else:
        readback_text = "unknown"
    return "\n".join(
        [
            "privacy acknowledgement:",
            f"  discovered: {discovered_text}",
            f"  classified: {trace.get('classified') or 'none'}",
            f"  control_type: {trace.get('control_type') or 'unknown'}",
            f"  interaction_attempted: {trace.get('interaction_attempted') or 'none'}",
            f"  readback_checked: {readback_text}",
            f"  failure_reason: {trace.get('failure_reason') or 'none'}",
        ]
    )


def _label_lines(fields: list[AutofillFieldResult]) -> list[str]:
    labels = [item.label.strip() for item in fields if item.label.strip()]
    if not labels:
        return ["- None"]
    return [f"- {label}" for label in labels]
