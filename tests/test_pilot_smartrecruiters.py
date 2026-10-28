from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pilot_smartrecruiters as pilot  # noqa: E402

from app.company_watch.watchers.smartrecruiters import (  # noqa: E402
    smartrecruiters_posting_endpoint,
    smartrecruiters_postings_endpoint,
)


@pytest.fixture(autouse=True)
def _allowed_pilot_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Confine the allowed output root to this test's tmp_path, so no test in
    # this module can resolve against (or accidentally prove safe against)
    # the real repo's data/smartrecruiters-pilot directory.
    monkeypatch.setattr(pilot, "DEFAULT_OUTPUT_DIR", tmp_path / "smartrecruiters-pilot")


# Synthetic sentinel values (never real data) injected into the mocked
# detail body below, so privacy assertions can prove these specific values
# are not serialized -- rather than asserting a bare substring like
# "creator" is absent, which would also flag LIMITATIONS' own honest
# explanatory text about creator/contact fields never being read.
_SENTINEL_CREATOR_EMAIL = "sentinel-creator-9f3c@example.invalid"
_SENTINEL_AVATAR_MARKER = "sentinel-avatar-9f3c"
_SENTINEL_REFERRAL_MARKER = "sentinel-referral-9f3c"
_SENTINEL_APPLY_MARKER = "sentinel-apply-9f3c"


def _mock_wise_board() -> None:
    respx.get(smartrecruiters_postings_endpoint("Wise")).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "id": "744000156787429",
                        "name": "Senior Backend Engineer - Rules Platform",
                        "uuid": "07a50ce2-80e6-4b07-9752-fb86c89717ae",
                        "location": {"city": "Tallinn", "country": "ee", "fullLocation": "Tallinn, , Estonia"},
                    },
                    {
                        "id": "9",
                        "name": "Senior Recruiter",
                        "uuid": "uuid-9",
                        "location": {"city": "London", "country": "gb"},
                    },
                ],
                "totalFound": 2,
            },
        )
    )
    respx.get(smartrecruiters_posting_endpoint("Wise", "744000156787429")).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "744000156787429",
                "uuid": "07a50ce2-80e6-4b07-9752-fb86c89717ae",
                "active": True,
                "name": "Senior Backend Engineer - Rules Platform",
                "location": {"fullLocation": "Tallinn, , Estonia", "hybrid": True},
                "postingUrl": "https://jobs.smartrecruiters.com/Wise/744000156787429-senior-backend-engineer-rules-platform",
                "applyUrl": f"https://jobs.smartrecruiters.com/Wise/744000156787429?oga={_SENTINEL_APPLY_MARKER}",
                "avatarUrl": f"https://cdn.example.invalid/{_SENTINEL_AVATAR_MARKER}.png",
                "referralUrl": f"https://jobs.smartrecruiters.com/Wise/744000156787429?ref={_SENTINEL_REFERRAL_MARKER}",
                "creator": {"name": "Hiring Manager", "email": _SENTINEL_CREATOR_EMAIL},
                "jobAd": {
                    "sections": {
                        "jobDescription": {"text": "<p>Java/Kotlin backend, Kafka, SQL. Tallinn relocation/visa support.</p>"},
                    }
                },
            },
        )
    )


@respx.mock
def test_pilot_writes_isolated_report_with_only_public_fields(tmp_path: Path) -> None:
    _mock_wise_board()
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 0
    assert output_path.exists()
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["employer"] == "Wise"
    assert report["counters"]["raw_fetched"] == 2
    assert report["counters"]["title_matched_count"] == 1
    assert report["counters"]["vacancy_count"] == 1
    assert report["limitations"]

    vacancy = report["vacancies"][0]
    assert vacancy["external_id"] == "744000156787429"
    assert vacancy["url"] == "https://jobs.smartrecruiters.com/Wise/744000156787429-senior-backend-engineer-rules-platform"
    assert "Java/Kotlin backend" in vacancy["description"]
    allowed_keys = {
        "source",
        "external_id",
        "title",
        "company",
        "location",
        "employment",
        "url",
        "published_at",
        "content_completeness",
        "description",
    }
    assert set(vacancy.keys()) == allowed_keys
    # Prove the injected synthetic sentinel values from the mocked detail
    # body (creator/avatarUrl/applyUrl/referralUrl) never reach the output,
    # without asserting on the bare word "creator" -- LIMITATIONS itself
    # legitimately mentions "creator/contact fields" in its explanatory text.
    serialized = json.dumps(report)
    assert _SENTINEL_CREATOR_EMAIL not in serialized
    assert _SENTINEL_AVATAR_MARKER not in serialized
    assert _SENTINEL_APPLY_MARKER not in serialized
    assert _SENTINEL_REFERRAL_MARKER not in serialized


def test_pilot_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    # No respx mock is registered: validation must reject before any
    # network call is attempted, so an unmocked request would fail loudly.
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("existing", encoding="utf-8")

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == "existing"


def test_pilot_rejects_output_path_outside_pilot_directory(tmp_path: Path) -> None:
    output_path = tmp_path / "somewhere-else" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 2
    assert not output_path.exists()


def test_pilot_rejects_output_path_not_directly_under_pilot_directory(tmp_path: Path) -> None:
    # "smartrecruiters-pilot" appears in the path but is not the immediate
    # parent directory -- a bare "is it anywhere in the path" check would
    # wrongly accept this.
    output_path = tmp_path / "smartrecruiters-pilot" / "nested" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 2
    assert not output_path.exists()


def test_pilot_rejects_lookalike_pilot_directory_outside_allowed_root(tmp_path: Path) -> None:
    # Same directory *name* as the allowed root, but a different actual
    # directory on disk -- a bare "parent.name == 'smartrecruiters-pilot'"
    # check would wrongly accept this.
    output_path = tmp_path / "elsewhere" / "smartrecruiters-pilot" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 2
    assert not output_path.exists()


def test_pilot_rejects_invalid_bounds_before_any_network_call(tmp_path: Path) -> None:
    # No respx mock is registered for any of these: bounds must be rejected
    # before the watcher ever makes a request.
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"
    for bad_args in (
        ["--max-pages", "0"],
        ["--max-pages", "-1"],
        ["--page-limit", "0"],
        ["--page-limit", "101"],
        ["--max-detail", "-1"],
        ["--max-detail", "101"],
    ):
        exit_code = pilot.main(["--output", str(output_path), *bad_args])
        assert exit_code == 2
        assert not output_path.exists()


@respx.mock
def test_pilot_does_not_touch_files_outside_its_output_path(tmp_path: Path) -> None:
    # Sentinel stand-in for "production" state the pilot must never read or
    # write, entirely within tmp_path -- no real repo paths are touched.
    sentinel_db = tmp_path / "data" / "jobs.db"
    sentinel_db.parent.mkdir(parents=True)
    sentinel_db.write_bytes(b"sentinel-production-data")

    _mock_wise_board()
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 0
    assert sentinel_db.read_bytes() == b"sentinel-production-data"
    produced_files = {path for path in tmp_path.rglob("*") if path.is_file()}
    assert produced_files == {sentinel_db, output_path}


@respx.mock
def test_pilot_report_marks_complete_true_with_no_errors_or_truncation(tmp_path: Path) -> None:
    _mock_wise_board()
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["complete"] is True


@respx.mock
def test_pilot_report_marks_complete_false_and_excludes_response_snippet_on_job_error(tmp_path: Path) -> None:
    respx.get(smartrecruiters_postings_endpoint("Wise")).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "id": "744000156787429",
                        "name": "Senior Backend Engineer - Rules Platform",
                        "location": {"city": "Tallinn", "country": "ee"},
                    }
                ],
                "totalFound": 1,
            },
        )
    )
    # Error body carries a synthetic sentinel value in a creator/contact
    # field to prove the whitelist drops response_snippet (and so this
    # sentinel) rather than serializing it.
    sentinel_error_email = "sentinel-job-error-creator-9f3c@example.invalid"
    respx.get(smartrecruiters_posting_endpoint("Wise", "744000156787429")).mock(
        return_value=httpx.Response(
            500,
            json={"creator": {"email": sentinel_error_email}, "message": "boom"},
        )
    )
    output_path = tmp_path / "smartrecruiters-pilot" / "wise.json"

    exit_code = pilot.main(["--output", str(output_path)])

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert len(report["job_errors"]) == 1
    assert "response_snippet" not in report["job_errors"][0]
    serialized = json.dumps(report)
    assert sentinel_error_email not in serialized


def test_pilot_module_imports_no_settings_db_telegram_or_llm_modules() -> None:
    forbidden_substrings = ("config", "settings", "storage", "telegram", "vacancy_analyzer", "llm")
    source = Path(pilot.__file__).read_text(encoding="utf-8")
    import_lines = [line for line in source.splitlines() if line.startswith("from app.") or line.startswith("import app.")]
    assert import_lines == [
        "from app.company_watch.models import TargetCompany  # noqa: E402",
        "from app.company_watch.watchers.smartrecruiters import SmartRecruitersTargetWatcher  # noqa: E402",
    ]
    for line in import_lines:
        lowered = line.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), line
