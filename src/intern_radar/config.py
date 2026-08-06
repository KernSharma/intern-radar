from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FilterConfig:
    terms: tuple[str, ...] = ("Summer 2027",)
    categories: tuple[str, ...] = ()
    degrees_any: tuple[str, ...] = ()
    title_require_any: tuple[str, ...] = ("intern",)
    title_exclude: tuple[str, ...] = ()
    # Applied only to postings with no term metadata (direct ATS boards);
    # term-bearing listings are already gated by `terms`.
    untermed_title_exclude: tuple[str, ...] = ()
    location_exclude: tuple[str, ...] = ()
    # Companies to drop outright, whatever they post. For employers whose
    # postings can never become an application: an unreachable apply flow, or
    # a lifetime cap on applications you have already spent.
    company_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcesConfig:
    simplify: bool = True
    greenhouse_boards: tuple[str, ...] = ()
    lever_companies: tuple[str, ...] = ()
    ashby_orgs: tuple[str, ...] = ()
    workday_boards: tuple[str, ...] = ()
    smartrecruiters_companies: tuple[str, ...] = ()
    # ISO country code filter for SmartRecruiters queries ("" = worldwide).
    # Global boards like BoschGroup are ~90% non-US postings otherwise.
    smartrecruiters_country: str = ""


@dataclass(frozen=True)
class NotifyConfig:
    github_issues: bool = True


@dataclass(frozen=True)
class Config:
    filters: FilterConfig = field(default_factory=FilterConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def _str_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"expected a list of strings, got {raw!r}")
    return tuple(raw)


def load_config(path: Path) -> Config:
    with path.open("rb") as f:
        data = tomllib.load(f)

    f_raw = data.get("filters", {})
    filters = FilterConfig(
        terms=_str_tuple(f_raw.get("terms", ["Summer 2027"])),
        categories=_str_tuple(f_raw.get("categories", [])),
        degrees_any=_str_tuple(f_raw.get("degrees_any", [])),
        title_require_any=_str_tuple(f_raw.get("title_require_any", ["intern"])),
        title_exclude=_str_tuple(f_raw.get("title_exclude", [])),
        untermed_title_exclude=_str_tuple(f_raw.get("untermed_title_exclude", [])),
        location_exclude=_str_tuple(f_raw.get("location_exclude", [])),
        company_exclude=_str_tuple(f_raw.get("company_exclude", [])),
    )

    s_raw = data.get("sources", {})
    sources = SourcesConfig(
        simplify=bool(s_raw.get("simplify", True)),
        greenhouse_boards=_str_tuple(s_raw.get("greenhouse", {}).get("boards", [])),
        lever_companies=_str_tuple(s_raw.get("lever", {}).get("companies", [])),
        ashby_orgs=_str_tuple(s_raw.get("ashby", {}).get("orgs", [])),
        workday_boards=_str_tuple(s_raw.get("workday", {}).get("boards", [])),
        smartrecruiters_companies=_str_tuple(
            s_raw.get("smartrecruiters", {}).get("companies", [])
        ),
        smartrecruiters_country=str(s_raw.get("smartrecruiters", {}).get("country", "")),
    )

    n_raw = data.get("notify", {})
    notify = NotifyConfig(github_issues=bool(n_raw.get("github_issues", True)))

    return Config(filters=filters, sources=sources, notify=notify)
