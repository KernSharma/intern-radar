"""JD fetcher: parsers against real captured per-job payloads, plus URL routing."""

from pathlib import Path
from typing import Any

import pytest

import intern_radar.jd as jd_mod
import intern_radar.main as main_mod
from intern_radar.jd import (
    JDError,
    JobDescription,
    fetch_jd,
    html_to_text,
    parse_ashby_jd,
    parse_greenhouse_jd,
    parse_lever_jd,
    parse_smartrecruiters_jd,
    parse_workday_jd,
)

WD_URL = (
    "https://synchronyfinancial.wd5.myworkdayjobs.com/University/job/"
    "Stamford-Hub/BLP-Intern---Technology_2601695-1"
)


def test_html_to_text_bullets_blocks_and_entities() -> None:
    fragment = (
        "<p>Intro &amp; scope</p><script>var x = 1;</script>"
        "<ul><li>First</li><li>Second&nbsp;item</li></ul><div>Tail</div>"
    )
    text = html_to_text(fragment)
    assert text.splitlines() == ["Intro & scope", "- First", "- Second item", "", "Tail"]
    assert "var x" not in text


def test_html_to_text_rejoins_block_wrapped_bullets() -> None:
    # Workday ships <li><p>text</p></li> — the "- " marker must stay glued to its text.
    html = "<ul><li><p>Alpha</p></li><li><p>Beta gamma</p></li></ul>"
    assert html_to_text(html) == "- Alpha\n- Beta gamma"


def test_html_to_text_separates_table_cells() -> None:
    html = "<table><tr><td>Pay range</td><td>$50/hr</td></tr></table>"
    assert html_to_text(html) == "Pay range $50/hr"


def test_empty_description_raises_not_silent(load_fixture: Any) -> None:
    payload = load_fixture("jd_workday_job.json")
    payload["jobPostingInfo"]["jobDescription"] = ""
    with pytest.raises(JDError, match="empty job description"):
        parse_workday_jd(WD_URL, payload)


def test_greenhouse_null_content_raises(load_fixture: Any) -> None:
    payload = load_fixture("jd_greenhouse_job.json")
    payload["content"] = None
    with pytest.raises(JDError, match="empty job description"):
        parse_greenhouse_jd("u", "stripe", payload)


def test_html_to_text_collapses_blank_runs() -> None:
    assert html_to_text("<div></div><div></div><div>a</div><br><br><div>b</div>") == "a\n\nb"


def test_workday_parses_real_payload(load_fixture: Any) -> None:
    jd = parse_workday_jd(WD_URL, load_fixture("jd_workday_job.json"))
    assert jd.source == "workday"
    assert jd.company == "Synchrony Bank"  # hiringOrganization, not the tenant slug
    assert jd.title == "BLP Intern – Technology"  # noqa: RUF001 — live data uses an en dash
    assert jd.location == "Stamford Hub"
    assert jd.url == WD_URL
    assert "Business Leadership Program" in jd.text
    assert "<" not in jd.text  # HTML fully flattened


def test_greenhouse_parses_entity_escaped_content(load_fixture: Any) -> None:
    url = "https://stripe.com/jobs/search?gh_jid=8026689"
    jd = parse_greenhouse_jd(url, "stripe", load_fixture("jd_greenhouse_job.json"))
    assert jd.company == "Stripe"
    assert jd.title == "Internal Audit Data Analytics Lead"
    assert jd.location == "Toronto, New York, San Francisco"
    # `content` arrives entity-escaped (&lt;h2&gt;...) — it must flatten to prose.
    assert "&lt;" not in jd.text and "<" not in jd.text
    assert len(jd.text) > 1000


def test_lever_parses_real_payload(load_fixture: Any) -> None:
    url = "https://jobs.lever.co/palantir/ac978161-6f46-4f6b-ad9e-a258e642751c"
    jd = parse_lever_jd(url, "palantir", load_fixture("jd_lever_posting.json"))
    assert jd.company == "palantir"
    assert jd.title == "Administrative Business Partner"
    assert jd.location == "London, United Kingdom"
    assert jd.text.startswith("A World-Changing Company")  # descriptionPlain
    assert "What We Value" in jd.text  # list-section heading
    assert "What We Require" in jd.text


def test_ashby_finds_job_on_board(load_fixture: Any) -> None:
    job_id = "34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    url = f"https://jobs.ashbyhq.com/ramp/{job_id}"
    jd = parse_ashby_jd(url, "ramp", job_id, load_fixture("jd_ashby_board.json"))
    assert jd.title == "Security Engineer, Cloud"  # live title ships with a leading space
    assert jd.location == "New York, NY (HQ); Remote (Canada); Remote (US); Miami, FL"
    assert "About Ramp" in jd.text


def test_ashby_missing_job_raises(load_fixture: Any) -> None:
    with pytest.raises(JDError, match="not on the ramp board"):
        parse_ashby_jd("u", "ramp", "0000", load_fixture("jd_ashby_board.json"))


def test_smartrecruiters_parses_real_payload(load_fixture: Any) -> None:
    url = "https://jobs.smartrecruiters.com/WesternDigital/744000140949875"
    jd = parse_smartrecruiters_jd(url, load_fixture("jd_smartrecruiters_posting.json"))
    assert jd.company == "Western Digital"
    assert jd.title == "Summer 2027 Intern - Hardware Engineering"
    assert jd.location == "San Jose, CA, United States"
    # All four jobAd sections must land in the flattened text.
    assert "Job Description" in jd.text and "Qualifications" in jd.text


ROUTES = [
    (
        "https://boards.greenhouse.io/stripe/jobs/8026689",
        "",
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/8026689",
        "jd_greenhouse_job.json",
    ),
    (
        "https://stripe.com/jobs/search?gh_jid=8026689",
        "stripe",
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/8026689",
        "jd_greenhouse_job.json",
    ),
    (
        "https://jobs.lever.co/palantir/ac978161-6f46-4f6b-ad9e-a258e642751c",
        "",
        "https://api.lever.co/v0/postings/palantir/ac978161-6f46-4f6b-ad9e-a258e642751c",
        "jd_lever_posting.json",
    ),
    (
        "https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245/application",
        "",
        "https://api.ashbyhq.com/posting-api/job-board/ramp",
        "jd_ashby_board.json",
    ),
    (
        WD_URL,
        "",
        "https://synchronyfinancial.wd5.myworkdayjobs.com/wday/cxs/synchronyfinancial/"
        "University/job/Stamford-Hub/BLP-Intern---Technology_2601695-1",
        "jd_workday_job.json",
    ),
    (
        # Copied from the apply page: trailing /apply must be dropped.
        WD_URL + "/apply",
        "",
        "https://synchronyfinancial.wd5.myworkdayjobs.com/wday/cxs/synchronyfinancial/"
        "University/job/Stamford-Hub/BLP-Intern---Technology_2601695-1",
        "jd_workday_job.json",
    ),
    (
        # Locale-prefixed workday URL must route identically.
        "https://synchronyfinancial.wd5.myworkdayjobs.com/en-US/University/job/"
        "Stamford-Hub/BLP-Intern---Technology_2601695-1",
        "",
        "https://synchronyfinancial.wd5.myworkdayjobs.com/wday/cxs/synchronyfinancial/"
        "University/job/Stamford-Hub/BLP-Intern---Technology_2601695-1",
        "jd_workday_job.json",
    ),
    (
        "https://jobs.smartrecruiters.com/WesternDigital/744000140949875-summer-intern",
        "",
        "https://api.smartrecruiters.com/v1/companies/WesternDigital/postings/744000140949875",
        "jd_smartrecruiters_posting.json",
    ),
]


@pytest.mark.parametrize(("url", "board", "expected_api", "fixture"), ROUTES)
def test_fetch_jd_routes_to_the_right_api(
    monkeypatch: pytest.MonkeyPatch,
    load_fixture: Any,
    url: str,
    board: str,
    expected_api: str,
    fixture: str,
) -> None:
    called: list[str] = []

    def fake_get_json(api_url: str, **kwargs: Any) -> Any:
        called.append(api_url)
        return load_fixture(fixture)

    monkeypatch.setattr(jd_mod, "get_json", fake_get_json)
    jd = fetch_jd(url, board=board)
    assert called == [expected_api]
    assert jd.title and jd.text


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://example.com/careers/123", "unsupported job board host"),
        ("https://stripe.com/jobs/search?gh_jid=8026689", "pass --board"),
        ("https://boards.greenhouse.io/stripe", "unrecognized greenhouse"),
        ("https://acme.wd1.myworkdayjobs.com/site/notjob/x/y", "unrecognized workday"),
        ("https://jobs.smartrecruiters.com/Acme/no-numeric-id", "no numeric id"),
        (
            "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/uuid",
            "use the posting link",
        ),
    ],
)
def test_fetch_jd_rejects_bad_urls(url: str, match: str) -> None:
    with pytest.raises(JDError, match=match):
        fetch_jd(url)


def test_render_format() -> None:
    jd = JobDescription(
        source="workday", company="Acme", title="Intern", location="X, Y",
        url="https://a", text="body",
    )
    assert jd.render() == "# Intern\nAcme — X, Y\nhttps://a\n\nbody\n"


def test_cli_jd_writes_out_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jd = JobDescription(
        source="workday", company="Acme", title="Intern", location="",
        url="https://a", text="body",
    )
    monkeypatch.setattr(main_mod, "fetch_jd", lambda url, board="": jd)
    out = tmp_path / "sub" / "jd.md"
    assert main_mod.main(["jd", "https://a", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == jd.render()
    assert "Acme — Intern" in capsys.readouterr().out


def test_cli_jd_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(url: str, board: str = "") -> JobDescription:
        raise JDError("nope")

    monkeypatch.setattr(main_mod, "fetch_jd", boom)
    assert main_mod.main(["jd", "https://a"]) == 2
    assert "nope" in capsys.readouterr().err
