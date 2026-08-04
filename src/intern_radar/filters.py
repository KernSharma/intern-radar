from __future__ import annotations

from intern_radar.config import FilterConfig
from intern_radar.models import Posting


def matches(posting: Posting, filters: FilterConfig) -> bool:
    title_lower = posting.title.lower()

    if any(kw.lower() in title_lower for kw in filters.title_exclude):
        return False

    # Reject only when every listed location is excluded; a multi-location
    # posting with one acceptable site is still worth seeing.
    if filters.location_exclude and posting.locations:
        def excluded(loc: str) -> bool:
            loc_lower = loc.lower()
            return any(kw.lower() in loc_lower for kw in filters.location_exclude)

        if all(excluded(loc) for loc in posting.locations):
            return False

    if posting.terms:
        # Aggregator listing with term metadata (Simplify).
        if filters.terms and not set(posting.terms) & set(filters.terms):
            return False
        if filters.categories and posting.category and posting.category not in filters.categories:
            return False
        return (
            not filters.degrees_any
            or not posting.degrees
            or bool(set(posting.degrees) & set(filters.degrees_any))
        )

    # Direct ATS posting: no term metadata, so dated-cycle noise ("Fall 2026")
    # is excludable only by title.
    if any(kw.lower() in title_lower for kw in filters.untermed_title_exclude):
        return False
    # The source's own employment-type label counts first (Ashby: "Intern",
    # Lever: "Internship", SmartRecruiters: "Intern"), then the title gate.
    if "intern" in posting.employment_type.lower():
        return True
    return not filters.title_require_any or any(
        kw.lower() in title_lower for kw in filters.title_require_any
    )


def apply_filters(postings: list[Posting], filters: FilterConfig) -> list[Posting]:
    return [p for p in postings if matches(p, filters)]
