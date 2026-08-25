from __future__ import annotations

import json
import logging
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.resume_profiles import ResumeProfile, ResumeProfiles


logger = logging.getLogger(__name__)


class ResumeSelectionLLM(Protocol):
    def select_resume_profile(self, *, prompt: str) -> str: ...


class _ModelSelection(BaseModel):
    selected_profile: str
    reason: str


class ResumeSelection(BaseModel):
    selected_profile: str
    reason: str


class ResumeSelector:
    def __init__(
        self,
        *,
        llm_client: ResumeSelectionLLM,
        profiles: ResumeProfiles,
        prompt_template: str,
    ) -> None:
        self._llm_client = llm_client
        self._profiles = profiles
        self._prompt_template = prompt_template

    @property
    def profiles(self) -> ResumeProfiles:
        return self._profiles

    def select(self, job_description: str) -> ResumeSelection:
        prompt = self.build_prompt(job_description)
        raw_response = self._llm_client.select_resume_profile(prompt=prompt)
        try:
            model_selection = _ModelSelection.model_validate(json.loads(raw_response))
        except (json.JSONDecodeError, ValidationError, TypeError):
            selection = self._fallback("Model returned an invalid resume selection response.")
        else:
            if self._profiles.get(model_selection.selected_profile) is None:
                selection = self._fallback(
                    f"Model selected unknown profile '{model_selection.selected_profile}'. "
                    f"Model reason: {model_selection.reason.strip()}"
                )
            else:
                selection = ResumeSelection(
                    selected_profile=model_selection.selected_profile,
                    reason=model_selection.reason.strip(),
                )

        logger.info(
            "Selected resume profile: %s; reason: %s",
            selection.selected_profile,
            selection.reason,
        )
        return selection

    def selected_profile(self, selection: ResumeSelection) -> ResumeProfile:
        return self._profiles.get(selection.selected_profile) or self._profiles.default

    def build_prompt(self, job_description: str) -> str:
        return (
            self._prompt_template.replace(
                "{{ resume_profiles }}",
                self._profiles.format_for_prompt(),
            ).replace(
                "{{ job_description }}",
                job_description.strip(),
            )
        )

    def _fallback(self, reason: str) -> ResumeSelection:
        return ResumeSelection(
            selected_profile=self._profiles.default.id,
            reason=f"{reason} Falling back to the default profile.",
        )
