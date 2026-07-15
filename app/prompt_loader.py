from pathlib import Path


DEFAULT_PROMPT_PATH = Path("prompts/analyze_vacancy.md")


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
