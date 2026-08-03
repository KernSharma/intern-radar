"""Parsers exercised against real captured API payloads (trimmed, not synthesized)."""

from typing import Any

import pytest

from intern_radar.sources.ashby import parse_ashby
from intern_radar.sources.greenhouse import parse_greenhouse
from intern_radar.sources.lever import parse_lever
from intern_radar.sources.simplify import parse_simplify


def test_greenhouse_parses_real_payload(load_fixture: Any) -> None:
    postings = parse_greenhouse("stripe", load_fixture("greenhouse_jobs.json"))
    assert len(postings) == 3
    first = postings[0]
    assert first.key == "greenhouse:stripe:7954688"
    assert first.source == "greenhouse"
    assert first.company == "Stripe"  # from company_name, not the board slug
    assert first.title == "Account Executive, AI Sales (Grower)"
    assert first.url == "https://stripe.com/jobs/search?gh_jid=7954688"
    assert first.locations == ("San Francisco, CA",)
    assert first.posted_at == "2026-06-02"
    # Trailing whitespace in live titles must be stripped.
    assert postings[2].title == "Account Executive, Bridge"


def test_lever_parses_real_payload(load_fixture: Any) -> None:
    postings = parse_lever("palantir", load_fixture("lever_postings.json"))
    assert len(postings) == 3
    intern = postings[0]
    assert intern.key == "lever:palantir:774cf5c9-bf6a-4d77-bf60-d50ef1beb1a0"
    assert intern.title == "Deployment Strategist, Internship"
    assert intern.url == "https://jobs.lever.co/palantir/774cf5c9-bf6a-4d77-bf60-d50ef1beb1a0"
    assert intern.locations == ("Paris, France",)
    assert intern.posted_at == "2026-05-11"  # createdAt is epoch milliseconds


def test_ashby_parses_real_payload(load_fixture: Any) -> None:
    postings = parse_ashby("ramp", load_fixture("ashby_jobs.json"))
    assert len(postings) == 3
    intern = postings[0]
    assert intern.key == "ashby:ramp:67fadb77-43d8-4449-954b-d4cf2c6d3b8b"
    # Live Ashby titles carry stray whitespace ("Software Engineer Internship, Android ").
    assert intern.title == "Software Engineer Internship, Android"
    assert postings[1].title == "Security Engineer, Cloud"
    assert intern.locations == ("New York, NY (HQ)", "San Francisco, CA")
    assert intern.posted_at == "2025-08-07"


def test_ashby_skips_unlisted(load_fixture: Any) -> None:
    payload = load_fixture("ashby_jobs.json")
    payload["jobs"][0]["isListed"] = False
    postings = parse_ashby("ramp", payload)
    assert len(postings) == 2
    assert all("Internship" not in p.title for p in postings)


def test_simplify_parses_and_drops_inactive_invisible(load_fixture: Any) -> None:
    postings = parse_simplify(load_fixture("simplify_fixture.json"))
    companies = {p.company for p in postings}
    # Fixture has 6 listings: Dexcom is inactive, AstraZeneca is invisible.
    assert companies == {"Palantir", "Marmon Holdings", "Etched.ai", "Base Power"}
    palantir = next(p for p in postings if p.company == "Palantir")
    assert palantir.key == "simplify:ada5c220-536e-454a-8ba0-1f7629d949e6"
    assert palantir.terms == ("Winter 2027", "Spring 2027", "Summer 2027", "Fall 2027")
    assert palantir.category == "Software"
    assert palantir.locations == ("Honolulu, HI",)
    assert palantir.posted_at == "2025-12-12"  # date_posted is epoch seconds


@pytest.mark.parametrize(
    ("parse", "bad"),
    [
        (lambda p: parse_simplify(p), {"not": "a list"}),
        (lambda p: parse_greenhouse("x", p), ["not a dict"]),
        (lambda p: parse_lever("x", p), {"not": "a list"}),
        (lambda p: parse_ashby("x", p), ["not a dict"]),
    ],
)
def test_parsers_reject_wrong_shapes(parse: Any, bad: Any) -> None:
    with pytest.raises(ValueError):
        parse(bad)
