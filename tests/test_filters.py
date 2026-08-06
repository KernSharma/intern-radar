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


def test_employment_type_label_beats_title_gate() -> None:
    # An intern posting titled without a keyword still passes when the source
    # itself labels it an internship (Ashby "Intern", Lever "Internship").
    labeled = Posting(
        key="t:1", source="ashby", company="X", url="https://x.example/1",
        title="Software Engineer — Summer 2027 Program", employment_type="Intern",
    )
    assert matches(labeled, DEFAULT)
    assert not matches(ats("Software Engineer — Summer 2027 Program"), DEFAULT)


def test_empty_title_require_any_means_any() -> None:
    f = FilterConfig(title_require_any=())
    assert matches(ats("Software Engineer, Backend"), f)


def test_untermed_exclude_only_hits_postings_without_terms() -> None:
    f = FilterConfig(
        terms=("Summer 2027",), title_require_any=("intern",),
        untermed_title_exclude=("fall 2026",),
    )
    # Direct ATS: dated noise from another cycle is rejected...
    assert not matches(ats("Avionics Intern (Fall 2026)"), f)
    # ...but a term-bearing listing mentioning both cycles still passes.
    both_cycles = Posting(
        key="simplify:2", source="simplify", company="X",
        title="SWE Intern (Fall 2026 & Summer 2027)", url="https://x.example/3",
        terms=("Fall 2026", "Summer 2027"), category="Software",
    )
    assert matches(both_cycles, f)


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


def _at(company: str, *, terms: tuple[str, ...] = ()) -> Posting:
    """A posting from `company` that would otherwise match DEFAULT."""
    return Posting(
        key=f"c:{company}", source="simplify", company=company,
        title="Software Engineer Intern", url="https://x.example/2",
        terms=terms, category="Software", degrees=("Bachelor's",),
    )


def test_company_exclude_drops_untermed_ats_posting() -> None:
    filters = FilterConfig(**{**vars(DEFAULT), "company_exclude": ("TikTok",)})
    assert matches(_at("Stripe"), filters) is True
    assert matches(_at("TikTok"), filters) is False


def test_company_exclude_drops_termed_simplify_listing() -> None:
    """The gate runs before the term/category branch, so it applies to both."""
    filters = FilterConfig(**{**vars(DEFAULT), "company_exclude": ("ByteDance",)})
    assert matches(_at("ByteDance", terms=("Summer 2027",)), filters) is False
    assert matches(_at("Palantir", terms=("Summer 2027",)), filters) is True


def test_company_exclude_is_case_insensitive_substring() -> None:
    filters = FilterConfig(**{**vars(DEFAULT), "company_exclude": ("tiktok",)})
    assert matches(_at("TikTok Inc."), filters) is False


def test_company_exclude_empty_by_default_changes_nothing() -> None:
    assert DEFAULT.company_exclude == ()
    assert matches(_at("TikTok"), DEFAULT) is True
