from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from intern_radar.config import FilterConfig
from intern_radar.models import Posting


@lru_cache(maxsize=256)
def _keyword_re(keyword: str) -> re.Pattern[str]:
    """Match `keyword` as a whole word, tolerating the plural/`-ship` endings.

    The gate that admits untermed ATS postings is a bare `"intern" in title`,
    and substrings betray it in both directions:
      * "INTERNational Strategy Lead" and "Interne Kommunikation" match "intern"
      * "COOPerative AI" matches "coop"
    Adding notable boards on 2026-08-07 turned that from a trickle into five
    junk Waymo titles and five OpenAI ones in a single sweep.

    The boundary is on LETTERS, not `\\b`. `\\b` counts an underscore as a word
    character, which would drop the real "EHS Intern_Shenzhen" posting; and it
    would still admit "International", since a boundary exists before it.
    """
    return re.compile(
        r"(?<![a-z])" + re.escape(keyword) + r"(?:s|ship|ships)?(?![a-z])",
        re.IGNORECASE,
    )


def _matches_keyword(text: str, keywords: Sequence[str]) -> bool:
    return any(_keyword_re(k).search(text) for k in keywords if k)


def matches(posting: Posting, filters: FilterConfig) -> bool:
    title_lower = posting.title.lower()

    # Company gate runs first and applies to every source: these postings can
    # never become an application, so they should not reach the inbox at all.
    company_lower = posting.company.lower()
    if any(kw.lower() in company_lower for kw in filters.company_exclude):
        return False

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
    if _keyword_re("intern").search(posting.employment_type):
        return True
    return not filters.title_require_any or _matches_keyword(
        posting.title, filters.title_require_any
    )


def apply_filters(postings: list[Posting], filters: FilterConfig) -> list[Posting]:
    return [p for p in postings if matches(p, filters)]
