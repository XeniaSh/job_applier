from pathlib import Path

import pytest
from pydantic import ValidationError

from app.company_watch.domain_policy import DomainPolicy, excluded_domain_reason, load_domain_policy


@pytest.fixture
def policy():
    return load_domain_policy(Path(__file__).parents[2] / "config/discovery_policy.yaml")


@pytest.mark.parametrize("name", ["Exness", "binance", " COINBASE "])
def test_approved_employer_exclusions(name, policy):
    assert excluded_domain_reason(company=name, policy=policy).startswith("excluded employer/project domain:")


def test_source_identity_covers_renamed_company(policy):
    assert excluded_domain_reason(company="Unknown", source="target_company:lever:binance", policy=policy)
    assert excluded_domain_reason(company="Binance-like bank", policy=policy) is None


@pytest.mark.parametrize("text,domain", [
    ("We are a forex broker.", "speculative_trading"),
    ("Our company is a cryptocurrency exchange.", "crypto_exchange"),
    ("Our client is an online casino.", "gambling"),
    ("We are a defense contractor.", "defense"),
    ("Our product is an adult entertainment platform.", "adult_entertainment"),
    ("Мы — форекс-брокер.", "speculative_trading"),
    ("Наша компания — криптовалютная биржа.", "crypto_exchange"),
    ("Наш клиент — онлайн-казино.", "gambling"),
    ("Мы — производитель оружия.", "defense"),
    ("Мы — порнографическая платформа.", "adult_entertainment"),
    ("We are a forex broker and no relocation is required.", "speculative_trading"),
    ("We are a speculative trading company.", "speculative_trading"),
    ("We build weapons systems.", "defense"),
    ("We are an adult entertainment company.", "adult_entertainment"),
])
def test_explicit_employer_or_project_self_description(text, domain, policy):
    assert excluded_domain_reason(text=text, policy=policy) == "excluded employer/project domain: " + domain


@pytest.mark.parametrize("name,text", [
    ("Wise", "Payment systems, FX conversion and financial infrastructure."),
    ("Ordinary Bank", "We are a lending and banking platform."),
    ("Games", "We develop games and social entertainment."),
    ("Payments", "Our customers include online casinos and forex brokers."),
    ("Security", "We build defense against cyber attacks."),
    ("Acme", "We are not a crypto exchange."),
    ("Acme", "Мы — не криптобиржа."),
    ("Acme", "Experience with a crypto exchange is optional."),
    ("Broker Protocol", "We build event brokers using Kafka."),
])
def test_no_incidental_keyword_or_fintech_ban(name, text, policy):
    assert excluded_domain_reason(company=name, text=text, policy=policy) is None


def test_policy_is_configurable_and_legacy_missing_file_is_empty(tmp_path):
    assert load_domain_policy(tmp_path / "absent.yaml") == DomainPolicy()
    policy = DomainPolicy(excluded_domains=["defense"], employer_domains={"Coinbase": ["crypto_exchange"]})
    assert excluded_domain_reason(company="Coinbase", policy=policy) is None
    assert excluded_domain_reason(text="We are a defense contractor.", policy=policy)


def test_policy_typo_is_not_silently_ignored(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("excluded_domains: [fintech]\n")
    with pytest.raises(ValidationError):
        load_domain_policy(path)


def test_malformed_present_policy_is_an_error(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("excluded_domains: [\n")
    with pytest.raises(ValueError, match="Invalid discovery policy YAML"):
        load_domain_policy(path)
