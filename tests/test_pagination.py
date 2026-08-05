"""Pagination loops for the POST/paged sources — the exact spot where a cap
or off-by-one silently hides postings (found the hard way in review)."""

from typing import Any

import pytest

import intern_radar.sources.smartrecruiters as sr_mod
import intern_radar.sources.workday as wd_mod


def wd_page(total: int, offset: int, size: int) -> dict[str, Any]:
    count = max(0, min(size, total - offset))
    return {
        "total": total,
        "jobPostings": [
            {"title": f"Intern {offset + i}", "externalPath": f"/job/X/p{offset + i}",
             "locationsText": "Boston"}
            for i in range(count)
        ],
    }


def test_workday_paginates_to_full_total(monkeypatch: pytest.MonkeyPatch) -> None:
    offsets: list[int] = []

    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        offsets.append(payload["offset"])
        assert payload["searchText"] == "intern"
        return wd_page(total=45, offset=payload["offset"], size=payload["limit"])

    monkeypatch.setattr(wd_mod, "post_json", fake_post)
    postings = wd_mod.fetch_workday("acme.wd5/Careers")
    assert offsets == [0, 20, 40]
    assert len(postings) == 45
    assert postings[0].url == "https://acme.wd5.myworkdayjobs.com/Careers/job/X/p0"


def test_workday_exact_page_multiple_makes_no_extra_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offsets: list[int] = []

    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        offsets.append(payload["offset"])
        return wd_page(total=40, offset=payload["offset"], size=payload["limit"])

    monkeypatch.setattr(wd_mod, "post_json", fake_post)
    assert len(wd_mod.fetch_workday("acme.wd5/Careers")) == 40
    assert offsets == [0, 20]


def test_workday_max_results_is_a_runaway_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload["offset"])
        return wd_page(total=100_000, offset=payload["offset"], size=payload["limit"])

    monkeypatch.setattr(wd_mod, "post_json", fake_post)
    postings = wd_mod.fetch_workday("acme.wd5/Careers")
    assert len(postings) == wd_mod.MAX_RESULTS
    assert len(calls) == wd_mod.MAX_RESULTS // wd_mod.PAGE_SIZE


def test_workday_stops_on_empty_raw_page_even_if_total_lies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"total": 500, "jobPostings": []}

    monkeypatch.setattr(wd_mod, "post_json", fake_post)
    assert wd_mod.fetch_workday("acme.wd5/Careers") == []


def test_workday_null_total_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        page = wd_page(total=3, offset=payload["offset"], size=payload["limit"])
        page["total"] = None
        return page

    monkeypatch.setattr(wd_mod, "post_json", fake_post)
    assert len(wd_mod.fetch_workday("acme.wd5/Careers")) == 3


@pytest.mark.parametrize("bad", ["noslash", "a/b/c", "/site", "tenant/"])
def test_workday_malformed_board_fails_loudly(bad: str) -> None:
    with pytest.raises(ValueError, match=r"tenant\.instance/site"):
        wd_mod.fetch_workday(bad)


def test_smartrecruiters_paginates_to_full_total(monkeypatch: pytest.MonkeyPatch) -> None:
    offsets: list[int] = []

    def fake_get(url: str) -> dict[str, Any]:
        offset = int(url.split("offset=")[1])
        offsets.append(offset)
        count = max(0, min(100, 250 - offset))
        return {
            "totalFound": 250,
            "content": [
                {"id": f"{offset + i}", "name": f"Intern {offset + i}",
                 "releasedDate": "2026-08-03T00:00:00Z"}
                for i in range(count)
            ],
        }

    monkeypatch.setattr(sr_mod, "get_json", fake_get)
    postings = sr_mod.fetch_smartrecruiters("Acme")
    assert offsets == [0, 100, 200]
    assert len(postings) == 250


def test_smartrecruiters_country_filter_in_query(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_get(url: str) -> dict[str, Any]:
        urls.append(url)
        return {"totalFound": 0, "content": []}

    monkeypatch.setattr(sr_mod, "get_json", fake_get)
    sr_mod.fetch_smartrecruiters("Acme", "us")
    assert urls[0].endswith("&country=us")
    urls.clear()
    sr_mod.fetch_smartrecruiters("Acme")  # default stays worldwide
    assert "country" not in urls[0]
