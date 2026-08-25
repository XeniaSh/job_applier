from pathlib import Path


DEFAULT_PROMPT_PATH = Path("prompts/analyze_vacancy.md")
DEFAULT_COVER_LETTER_PROMPT_PATH = Path("prompts/create_cover_letter.md")
DEFAULT_RESUME_SELECTOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts/select_resume_profile.txt"
)


class PromptLoadError(Exception):
    """Raised when analysis prompt cannot be loaded."""


def load_analysis_prompt(path: Path = DEFAULT_PROMPT_PATH) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(f"Cannot read analysis prompt: {path}") from exc

    if not content.strip():
        raise PromptLoadError(f"Analysis prompt is empty: {path}")
    return content


def load_cover_letter_prompt(path: Path = DEFAULT_COVER_LETTER_PROMPT_PATH) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(f"Cannot read cover letter prompt: {path}") from exc

    if not content.strip():
        raise PromptLoadError(f"Cover letter prompt is empty: {path}")
    return content


def load_resume_selector_prompt(path: Path = DEFAULT_RESUME_SELECTOR_PROMPT_PATH) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(f"Cannot read resume selector prompt: {path}") from exc

    if not content.strip():
        raise PromptLoadError(f"Resume selector prompt is empty: {path}")
    return content
