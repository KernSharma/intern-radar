from datetime import date
from typing import Any

import pytest

from intern_radar.sources import workday


def test_posted_date_variants() -> None:
    today = date(2026, 9, 30)
    assert workday.posted_date("Posted Today", today) == "2026-09-30"
    assert workday.posted_date("Posted Yesterday", today) == "2026-09-29"
    assert workday.posted_date("Posted 3 Days Ago", today) == "2026-09-27"
    assert workday.posted_date("Posted 30+ Days Ago", today) == "2026-08-31"
    assert workday.posted_date("Something else", today) == ""


def test_fetch_info_respects_depth_and_returns_total(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload["offset"])
        assert payload["limit"] == 20
        base = payload["offset"]
        jobs = [{"title": f"Intern {base + i}", "externalPath": f"/job/{base + i}",
                 "postedOn": "Posted Today"} for i in range(20)]
        return {"total": 747, "jobPostings": jobs}

    monkeypatch.setattr(workday, "post_json", fake_post)
    posts, total = workday.fetch_workday_info("acme.wd5/Ext", workday.DISCOVERED_MAX_RESULTS,
                                              date(2026, 9, 30))
    assert total == 747 and calls == [0, 20, 40, 60, 80] and len(posts) == 100
    assert posts[0].posted_at == "2026-09-30"
