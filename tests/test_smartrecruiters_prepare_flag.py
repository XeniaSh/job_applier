"""Offline rollout guards: disabled Prepare never affects Wise discovery."""

import pytest

import app.cli as cli
from app.application.autofill.ashby import AshbyAdapter
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillFailureReason, AutofillStatus
from app.application.autofill.resolver import DefaultVacancyResolver, SmartRecruitersTargetVacancyResolver, VacancyResolveError
from app.application.autofill.review_session import default_review_registry
from app.application.autofill.service import AutofillService, default_adapter_for_source
from app.application.autofill.smartrecruiters import SmartRecruitersAdapter, SmartRecruitersFormError
from app.config import Settings, smartrecruiters_prepare_enabled
from app.telegram.application_prepare import run_explicit_application_prepare
from app.telegram.client import APPLICATION_PREPARE_BUTTON_TEXT, build_action_buttons, source_supports_application_prepare
from tests.application.autofill.test_smartrecruiters_resolver import ID, POSTING, SOURCE
from tests.test_smartrecruiters_prepare import seed, update
from tests.test_telegram_target_company_prepare import _FakeClient

FLAG = "SMARTRECRUITERS_PREPARE_ENABLED"


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(FLAG, raising=False)


def forbidden(*args, **kwargs):
    raise AssertionError("Disabled Prepare must stop before accessing downstream infrastructure.")


@pytest.mark.parametrize("value,expected", [(None, False), ("false", False), ("0", False),
                                         ("true", True), ("TRUE", True), ("1", True),
                                         ("invalid-SYNTHETIC-PRIVATE", False)])
def test_flag_defaults_off_and_parses_without_llm_configuration(monkeypatch, value, expected, caplog):
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    assert smartrecruiters_prepare_enabled() is expected
    assert source_supports_application_prepare(SOURCE) is expected
    assert "SYNTHETIC-PRIVATE" not in caplog.text


def test_settings_inherit_flag_and_dotenv_environment_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(FLAG + "=true\nUNRELATED_SETTING=synthetic\n")
    settings = Settings(llm_api_url="https://llm.invalid", llm_api_key="synthetic", llm_model="synthetic")
    assert settings.smartrecruiters_prepare_enabled is True
    assert smartrecruiters_prepare_enabled() is True
    monkeypatch.setenv(FLAG, "false")
    assert smartrecruiters_prepare_enabled() is False
    assert Settings(llm_api_url="https://llm.invalid", llm_api_key="synthetic", llm_model="synthetic").smartrecruiters_prepare_enabled is False


def test_unreadable_flag_configuration_fails_closed(monkeypatch):
    import app.config as config

    def unavailable():
        raise OSError("synthetic unavailable configuration")

    monkeypatch.setattr(config, "PrepareFeatureSettings", unavailable)
    assert smartrecruiters_prepare_enabled() is False


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("source", [SOURCE, "tcs.wise"])
def test_buttons_are_controlled_by_current_flag(monkeypatch, enabled, source):
    monkeypatch.setenv(FLAG, str(enabled))
    labels = [b.text for row in build_action_buttons(source, ID, POSTING) for b in row]
    assert (APPLICATION_PREPARE_BUTTON_TEXT in labels) is enabled
    assert labels[-3:] == ["✅ Applied", "⏭ Skip", "🔗 Open vacancy"]
    assert not source_supports_application_prepare("target_company:smartrecruiters:canva")


def test_previously_generated_prepare_callback_is_inert_after_flag_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "true")
    callback = build_action_buttons(SOURCE, ID, POSTING)[0][0].callback_data
    storage, cache = seed(tmp_path)
    cache_before = cache.path.read_bytes()
    monkeypatch.setenv(FLAG, "false")
    monkeypatch.setattr(cli, "default_review_registry", forbidden)
    client = _FakeClient()
    cli._process_callback_update(update=update(callback), client=client, storage=storage,
        configured_chat_id="222", application_prepare_cache=cache,
        application_prepare_sync=False)
    assert "not available" in client.answers[0][1]
    assert client.texts == []
    assert storage.get_delivery(SOURCE, ID).status == "SENT"
    assert storage.get_history_status(SOURCE, ID) != "APPLIED"
    assert cache.path.read_bytes() == cache_before


def test_direct_telegram_orchestration_is_blocked_before_lookup_or_prepare():
    calls = []

    class Downstream:
        def get_by_identity(self, *args):
            calls.append("lookup")
            forbidden()

        def prepare(self, *args, **kwargs):
            calls.append("prepare")
            forbidden()

    result = run_explicit_application_prepare(source=SOURCE, external_id=ID,
        prepare_service=Downstream(), recommendation_lookup=Downstream())
    assert result.recommendation == "UNKNOWN"
    assert calls == []


def test_disabled_registered_routes_stop_before_network_profile_or_browser(monkeypatch):
    import app.application.autofill.resolver as resolver

    monkeypatch.setattr(resolver.httpx, "Client", forbidden)
    routed = DefaultVacancyResolver(smartrecruiters_resolver=type("Resolver", (), {"resolve": forbidden})())
    with pytest.raises(VacancyResolveError, match="disabled"):
        routed.resolve(SOURCE, ID)
    with pytest.raises(VacancyResolveError, match="disabled"):
        SmartRecruitersTargetVacancyResolver().resolve(SOURCE, ID)
    with pytest.raises(SmartRecruitersFormError, match="disabled"):
        default_adapter_for_source(SOURCE)
    result = AutofillService(resolver=DefaultVacancyResolver(), profile_loader=forbidden,
        browser_factory=forbidden).run(SOURCE, ID, keep_open=False)
    assert result.status is AutofillStatus.FAILED
    assert result.failure_reason is AutofillFailureReason.VACANCY_RESOLVE_FAILED
    assert not result.submit_performed and not result.filled_fields


@pytest.mark.parametrize("enabled", [False, True])
def test_other_provider_routing_is_unchanged(monkeypatch, enabled):
    monkeypatch.setenv(FLAG, str(enabled))
    for name, expected in (("greenhouse", GreenhouseAdapter), ("lever", LeverAdapter), ("ashby", AshbyAdapter)):
        source = f"target_company:{name}:example"
        assert source_supports_application_prepare(source)
        assert type(default_adapter_for_source(source)) is expected
    if enabled:
        adapter = default_adapter_for_source(SOURCE)
        assert isinstance(adapter, SmartRecruitersAdapter)
        assert adapter._prepare_screening and adapter._require_posting_identity
        assert adapter.allow_auto_submit is False


def test_disabling_new_prepares_does_not_block_existing_done_callback(monkeypatch, tmp_path):
    registry = default_review_registry()
    session_id = registry.register(source=SOURCE, external_id=ID)
    try:
        storage, _ = seed(tmp_path)
        client = _FakeClient()
        cli._process_callback_update(update=update(f"revdone:{session_id}"), client=client,
            storage=storage, configured_chat_id="222")
        assert registry.mark_done(session_id).value == "already_closed"
        registry.wait_for_done(session_id)
        assert client.edits[-1]["buttons"] == []
        assert storage.get_history_status(SOURCE, ID) != "APPLIED"
    finally:
        registry.discard(session_id)
