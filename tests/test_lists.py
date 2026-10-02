from datetime import UTC, date, datetime
from pathlib import Path

from intern_radar.sources.lists import parse_speedyapply, parse_vansh


def ts(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


def vansh_row(i: int, **kw: object) -> dict:
    base = {"id": f"id{i}", "active": True, "is_visible": True, "company_name": "Acme",
            "title": "Software Engineer Intern", "url": f"https://jobs.lever.co/acme/{i}",
            "locations": ["NYC"], "season": "Summer", "date_posted": ts(2026, 7, 1),
            "date_updated": ts(2026, 8, 23)}
    base.update(kw)
    return base


def test_vansh_summer_term_rules() -> None:
    rows = [
        vansh_row(1),
        vansh_row(2, date_posted=ts(2026, 4, 10)),
        vansh_row(3, title="Summer 2026 Engineer Intern"),
        vansh_row(4, title="Summer 2027 SWE Intern"),
        vansh_row(5, season="Fall"),
        vansh_row(6, active=False),
    ]
    by_id = {p.key: p for p in parse_vansh(rows)}
    assert by_id["vanshb03:id1"].terms == ("Summer 2027",)
    assert by_id["vanshb03:id2"].terms == ()
    assert by_id["vanshb03:id3"].terms == ()
    assert by_id["vanshb03:id4"].terms == ("Summer 2027",)
    assert by_id["vanshb03:id5"].terms == ()
    assert "vanshb03:id6" not in by_id
    assert by_id["vanshb03:id1"].source == "vanshb03"
    assert by_id["vanshb03:id1"].posted_at == "2026-07-01"


README = (Path(__file__).parent / "fixtures" / "speedyapply_readme.md").read_text(
    encoding="utf-8")


def test_speedyapply_both_layouts() -> None:
    posts = parse_speedyapply(README, date(2026, 9, 30))
    assert [(p.company, p.title, p.posted_at) for p in posts] == [
        ("Microsoft", "Software Engineer: Intern", "2026-09-26"),
        ("AT&T", "Data Intern", "2026-09-30"),
        ("Zeta", "ML Intern", ""),
    ]
    assert posts[1].url == "https://att.wd1.myworkdayjobs.com/ATTCollege/job/x_1"
    assert posts[0].source == "speedyapply" and posts[0].terms == ()
