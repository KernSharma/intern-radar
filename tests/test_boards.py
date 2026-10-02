from datetime import UTC, datetime, timedelta

import pytest

from intern_radar import boards
from intern_radar.models import canon_key

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(("url", "expected"), [
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US/Intern_JR1",
     ("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
    ("https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/x/y_R1",
     ("workday", "capitalone.wd12/Capital_One")),
    ("https://bank.wd1.myworkdayjobs.com/fr-CA/Ext/job/z", ("workday", "bank.wd1/Ext")),
    ("https://evil.wd5.myworkdayjobs.com.attacker.io/Site/job/x", None),
    ("https://a.b.wd5.myworkdayjobs.com/Site/job/x", None),
    ("https://job-boards.greenhouse.io/Stripe/jobs/123", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/anduril/jobs/5", ("greenhouse", "anduril")),
    ("https://boards.greenhouse.io/embed/job_app?for=Jumptrading&token=8027900",
     ("greenhouse", "jumptrading")),
    ("https://boards.greenhouse.io/embed/job_app?token=1", None),
    ("https://jobs.ashbyhq.com/Perplexity/abc/application?embed=true", ("ashby", "perplexity")),
    ("https://jobs.lever.co/palantir/uuid/apply", ("lever", "palantir")),
    ("https://jobs.smartrecruiters.com/BoschGroup/7439", ("smartrecruiters", "BoschGroup")),
    ("https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/9",
     ("oracle", "eofe.fa.us2.oraclecloud.com|CX_1001")),
    ("https://evil-oraclecloud.com/hcmUI/CandidateExperience/en/sites/X/job/1", None),
    ("https://oraclecloud.com.evil.io/hcmUI/CandidateExperience/en/sites/X/job/1", None),
    ("https://careers-schwab.icims.com/jobs/26869/intern/job",
     ("icims", "careers-schwab.icims.com")),
    ("https://paypal.eightfold.ai/careers/job/1", ("eightfold", "paypal.eightfold.ai")),
    ("https://www.optiver.com/job?gh_jid=8027900", ("gh_custom", "www.optiver.com")),
    ("https://www.optiver.com/job?gh_jid=abc", None),
    ("https://example.com/careers/1", None),
    ("javascript:alert(1)", None),
    ("https://bad_host.example.com/x", None),
])
def test_board_of(url: str, expected: tuple[str, str] | None) -> None:
    assert boards.board_of(url) == expected


def test_canon_key_folds_workday_locale_and_icims_variants() -> None:
    assert canon_key("https://x.wd1.myworkdayjobs.com/en-US/Site/job/a_1") == \
        canon_key("https://x.wd1.myworkdayjobs.com/Site/job/a_1")
    assert canon_key("https://careers-y.icims.com/jobs/12886/job?mobile=true&needsRedirect=false") \
        == canon_key("https://careers-y.icims.com/jobs/12886/software-intern/job?in_iframe=1")
    assert canon_key("https://job-boards.greenhouse.io/a/jobs/1") != \
        canon_key("https://job-boards.greenhouse.io/a/jobs/2")


def row(**kw: object) -> dict:
    base = boards.new_row("workday", "t.wd1/S", pinned=False, discovered_via="simplify",
                          now=(NOW - timedelta(days=100)).isoformat())
    base.update(kw)
    return base


def test_due_tiers() -> None:
    assert boards.due(row(pinned=True, last_polled=NOW.isoformat()), NOW)
    hot = row(last_match=(NOW - timedelta(days=10)).isoformat(), last_polled=NOW.isoformat())
    assert boards.due(hot, NOW)
    new = row(first_seen=(NOW - timedelta(days=3)).isoformat(), last_polled=NOW.isoformat())
    assert boards.due(new, NOW)
    warm = row(last_match=(NOW - timedelta(days=90)).isoformat(),
               last_polled=(NOW - timedelta(hours=2)).isoformat())
    assert not boards.due(warm, NOW)
    warm["last_polled"] = (NOW - timedelta(hours=6)).isoformat()
    assert boards.due(warm, NOW)
    cold = row(last_polled=(NOW - timedelta(hours=12)).isoformat())
    assert not boards.due(cold, NOW)
    cold["last_polled"] = (NOW - timedelta(hours=24)).isoformat()
    assert boards.due(cold, NOW)
    assert boards.due(row(), NOW)  # never polled
    assert not boards.due(row(disabled=True), NOW)
    assert not boards.due(row(needs_config=True), NOW)
    assert not boards.due(row(ats="oracle"), NOW)  # no reader until phase 2


def test_jitter_is_bounded_and_spread() -> None:
    keys = [f"workday:t{i}.wd1/s" for i in range(400)]
    minutes = {boards.bucket(k) % 60 for k in keys}
    assert len(minutes) > 50 and max(minutes) < 60


def test_deep_crawl_due() -> None:
    assert not boards.deep_crawl_due(row(), NOW)
    assert boards.deep_crawl_due(row(deep=True), NOW)
    assert not boards.deep_crawl_due(row(deep=True, last_deep_crawl=NOW.isoformat()), NOW)
    assert not boards.deep_crawl_due(row(deep=True, pinned=True), NOW)


def test_prune_and_evict() -> None:
    reg = boards.empty_registry()
    old = (NOW - timedelta(days=500)).isoformat()
    reg["boards"]["workday:old"] = row(first_seen=old)
    reg["boards"]["workday:pinned-old"] = row(first_seen=old, pinned=True)
    reg["boards"]["workday:fresh"] = row(first_seen=NOW.isoformat())
    assert boards.prune_and_evict(reg, NOW) == ["workday:old"]
    assert set(reg["boards"]) == {"workday:pinned-old", "workday:fresh"}


def test_evicts_oldest_beyond_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boards, "MAX_UNPINNED", 2)
    reg = boards.empty_registry()
    for i, days in enumerate((1, 5, 9)):
        reg["boards"][f"workday:b{i}"] = row(
            last_match=(NOW - timedelta(days=days)).isoformat(), first_seen=NOW.isoformat())
    assert boards.prune_and_evict(reg, NOW) == ["workday:b2"]
