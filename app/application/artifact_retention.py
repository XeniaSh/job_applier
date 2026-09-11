from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENERATED_ARTIFACT_NAMES = frozenset({"cover_letter.txt", "cover_letter.pdf"})
DEFAULT_MAX_AGE_DAYS = 14


class ArtifactRetentionError(Exception):
    """Raised when generated-artifact cleanup is refused for safety."""


@dataclass(frozen=True)
class ArtifactCleanupReport:
    deleted_files: list[Path]
    skipped_files: list[Path]


def cleanup_generated_artifacts(
    artifacts_dir: Path,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> ArtifactCleanupReport:
    """Delete rebuildable cover-letter files older than max_age_days.

    Never deletes `resumes/` originals. Only `cover_letter.txt` / `cover_letter.pdf`
    under `artifacts_dir` are eligible.
    """
    root = artifacts_dir.expanduser().resolve()
    if _is_protected_resume_dir(root):
        raise ArtifactRetentionError(f"Refusing to clean resume directory: {root}")
    if max_age_days < 1:
        raise ArtifactRetentionError("max_age_days must be at least 1.")
    if not root.exists():
        return ArtifactCleanupReport(deleted_files=[], skipped_files=[])

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    deleted: list[Path] = []
    skipped: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name not in GENERATED_ARTIFACT_NAMES:
            skipped.append(path)
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime <= cutoff:
            path.unlink()
            deleted.append(path)
        else:
            skipped.append(path)
    _remove_empty_dirs(root)
    return ArtifactCleanupReport(deleted_files=deleted, skipped_files=skipped)


def _is_protected_resume_dir(root: Path) -> bool:
    parts = {part.lower() for part in root.parts}
    if "resumes" in parts and "prepared" not in parts:
        return True
    return False


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                continue
