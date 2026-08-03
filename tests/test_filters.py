from typing import Any

from intern_radar.config import FilterConfig
from intern_radar.filters import apply_filters, matches
from intern_radar.models import Posting
from intern_radar.sources.simplify import parse_simplify

DEFAULT = FilterConfig(
    terms=("Summer 2027",),
    categories=("Software", "Software Engineering", "AI/ML/Data"),
    degrees_any=("Bachelor's",),
    title_require_any=("intern", "co-op", "coop"),
    title_exclude=("senior", "staff", "phd"),
)


def ats(title: str, locations: tuple[str, ...] = ()) -> Posting:
    return Posting(
        key=f"t:{title}", source="greenhouse", company="X", title=title,
        url="https://x.example/1", locations=locations,
    )


def test_simplify_fixture_end_to_end(load_fixture: Any) -> None:
    postings = parse_simplify(load_fixture("simplify_fixture.json"))
    matched = apply_filters(postings, DEFAULT)
    # Palantir (multi-term incl. Summer 2027, degrees=[] passes as unspecified)
    # and Marmon (Summer 2027, Bachelor's). Etched.ai and Base Power are
    # active+visible but wrong term.
    assert sorted(p.company for p in matched) == ["Marmon Holdings", "Palantir"]


def test_direct_ats_requires_intern_in_title() -> None:
    assert matches(ats("Software Engineer Internship, Android"), DEFAULT)
    assert matches(ats("Hardware Co-op (Fall)"), DEFAULT)
    assert not matches(ats("Software Engineer, Backend"), DEFAULT)


def test_title_exclusions_beat_inclusions() -> None:
    assert not matches(ats("Senior Intern Program Manager"), DEFAULT)
    assert not matches(ats("PhD Research Intern"), DEFAULT)


def test_location_exclude_requires_all_locations_excluded() -> None:
    f = FilterConfig(
        title_require_any=("intern",), location_exclude=("london",),
    )
    both = ats("SWE Intern", locations=("London, UK", "New York, NY"))
    only_excluded = ats("SWE Intern", locations=("London, UK",))
    no_locations = ats("SWE Intern")
    assert matches(both, f)  # one acceptable site remains
    assert not matches(only_excluded, f)
    assert matches(no_locations, f)  # unknown location is not grounds for rejection


def test_empty_filter_lists_mean_any() -> None:
    f = FilterConfig(terms=(), categories=(), degrees_any=(), title_require_any=("intern",))
    simplify_posting = Posting(
        key="simplify:1", source="simplify", company="X", title="Anything",
        url="https://x.example/2", terms=("Fall 2099",), category="Underwater Basketweaving",
    )
    assert matches(simplify_posting, f)
