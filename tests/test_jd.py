"""JD fetcher: parsers against real captured per-job payloads, plus URL routing."""

import json
from pathlib import Path
from typing import Any

import pytest

import intern_radar.jd as jd_mod
import intern_radar.main as main_mod
from intern_radar.http import FetchError
from intern_radar.jd import (
    JDError,
    JobDescription,
    fetch_jd,
    html_to_text,
    parse_ashby_jd,
    parse_greenhouse_jd,
    parse_icims_jd,
    parse_lever_jd,
    parse_oracle_jd,
    parse_smartrecruiters_jd,
    parse_workday_jd,
)

WD_URL = (
    "https://synchronyfinancial.wd5.myworkdayjobs.com/University/job/"
    "Stamford-Hub/BLP-Intern---Technology_2601695-1"
)
ICIMS_URL = "https://careers-sig.icims.com/jobs/11169/job?mobile=true&needsRedirect=false"
ICIMS_API = "https://careers-sig.icims.com/jobs/11169/job?mobile=true&needsRedirect=false"
ORACLE_URL = (
    "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26011990"
)
ORACLE_API = (
    "https://egug.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/"
    'recruitingCEJobRequisitionDetails?expand=all&finder=ById;Id="26011990",siteNumber="CX_1"'
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


def test_oracle_parses_real_payload(load_fixture: Any) -> None:
    jd = parse_oracle_jd(ORACLE_URL, "egug", load_fixture("jd_oracle_job.json"))
    assert jd.source == "oracle"
    assert jd.company == "egug"  # Oracle ships no company name; tenant stands in
    assert jd.title == (
        "Campus Undergraduate Summer Internship Program - 2027 Strategy & Analytics, "
        "Credit & Fraud Risk - Phoenix, AZ"
    )
    assert jd.location == "Phoenix, AZ, United States"
    # This tenant splits the posting across three fields — all must land.
    assert "Credit and Fraud Risk" in jd.text  # ExternalDescriptionStr
    assert "Qualifications" in jd.text or "Bachelor" in jd.text  # Qualifications field
    assert "<" not in jd.text


def test_oracle_concatenates_split_body_fields(load_fixture: Any) -> None:
    payload = load_fixture("jd_oracle_job.json")
    item = payload["items"][0]
    item["ExternalDescriptionStr"] = "<p>DESC_MARKER</p>"
    item["ExternalResponsibilitiesStr"] = "<p>RESP_MARKER</p>"
    item["ExternalQualificationsStr"] = "<p>QUAL_MARKER</p>"
    text = parse_oracle_jd(ORACLE_URL, "egug", payload).text
    assert "DESC_MARKER" in text and "RESP_MARKER" in text and "QUAL_MARKER" in text
    assert text.index("DESC_MARKER") < text.index("RESP_MARKER") < text.index("QUAL_MARKER")


def test_oracle_excludes_boilerplate_when_body_present(load_fixture: Any) -> None:
    payload = load_fixture("jd_oracle_job.json")
    payload["items"][0]["CorporateDescriptionStr"] = "<p>MARKETING_FLUFF</p>"
    assert "MARKETING_FLUFF" not in parse_oracle_jd(ORACLE_URL, "egug", payload).text


def test_oracle_falls_back_to_boilerplate_when_body_empty(load_fixture: Any) -> None:
    # A boilerplate-only JD beats raising on a tenant that files the posting there.
    payload = load_fixture("jd_oracle_job.json")
    for field in (
        "ShortDescriptionStr",
        "ExternalDescriptionStr",
        "ExternalResponsibilitiesStr",
        "ExternalQualificationsStr",
    ):
        payload["items"][0][field] = ""
    payload["items"][0]["CorporateDescriptionStr"] = "<p>THE WHOLE POSTING</p>"
    assert "THE WHOLE POSTING" in parse_oracle_jd(ORACLE_URL, "egug", payload).text


def test_oracle_empty_items_raises_not_silent(load_fixture: Any) -> None:
    # A retired requisition is HTTP 200 with items: [] — never a header-only file.
    payload = load_fixture("jd_oracle_job.json")
    payload["items"] = []
    with pytest.raises(JDError, match="no requisition matched"):
        parse_oracle_jd(ORACLE_URL, "egug", payload)


def test_oracle_all_empty_fields_raises(load_fixture: Any) -> None:
    payload = load_fixture("jd_oracle_job.json")
    payload["items"][0] = {"Title": "Intern", "PrimaryLocation": "NY"}
    with pytest.raises(JDError, match="empty job description"):
        parse_oracle_jd(ORACLE_URL, "egug", payload)


def test_oracle_dedupes_and_merges_locations(load_fixture: Any) -> None:
    payload = load_fixture("jd_oracle_job.json")
    item = payload["items"][0]
    item["PrimaryLocation"] = "Chicago, IL"
    item["secondaryLocations"] = [{"Name": "New York, NY"}, {"Name": "Chicago, IL"}]
    item["otherWorkLocations"] = [{"Name": "Houston, TX"}]
    assert parse_oracle_jd(ORACLE_URL, "egug", payload).location == (
        "Chicago, IL; New York, NY; Houston, TX"
    )


def test_icims_parses_real_page(load_text_fixture: Any) -> None:
    jd = parse_icims_jd(ICIMS_URL, "sig", load_text_fixture("jd_icims_job.html"))
    assert jd.source == "icims"
    assert jd.company == "Susquehanna International Group, LLP"
    assert jd.title == "Trading System Engineering Internship: Summer 2027"
    # Real payload: addressRegion is the literal "UNAVAILABLE" and must be dropped.
    assert jd.location == "Hong Kong, HK"
    assert "Susquehanna" in jd.text
    assert "<" not in jd.text  # ld+json ships raw HTML in `description`


def test_icims_drops_unavailable_address_parts() -> None:
    # iCIMS writes the literal "UNAVAILABLE" into address fields it has no value for.
    page = _icims_page(
        jobLocation=[
            {
                "@type": "Place",
                "address": {
                    "addressLocality": "UNAVAILABLE",
                    "addressRegion": "PA",
                    "addressCountry": "US",
                    "postalCode": "UNAVAILABLE",
                },
            }
        ]
    )
    assert parse_icims_jd(ICIMS_URL, "sig", page).location == "PA, US"


def test_icims_accepts_single_joblocation_object() -> None:
    page = _icims_page(
        jobLocation={
            "@type": "Place",
            "address": {"addressLocality": "Austin", "addressRegion": "TX"},
        }
    )
    assert parse_icims_jd(ICIMS_URL, "sig", page).location == "Austin, TX"


def test_icims_page_without_jobposting_raises() -> None:
    # The redirect shim answers HTTP 200 with no ld+json — must not look like success.
    with pytest.raises(JDError, match="no JobPosting metadata"):
        parse_icims_jd(ICIMS_URL, "sig", "<html><body>redirecting…</body></html>")


def test_icims_skips_non_jobposting_ld_blocks() -> None:
    page = (
        '<script type="application/ld+json">{"@type": "BreadcrumbList"}</script>'
        '<script type="application/ld+json">not json at all</script>'
        + _icims_page()
    )
    assert parse_icims_jd(ICIMS_URL, "sig", page).title == "Intern"


def test_icims_empty_description_raises() -> None:
    with pytest.raises(JDError, match="empty job description"):
        parse_icims_jd(ICIMS_URL, "sig", _icims_page(description=""))


def test_icims_falls_back_to_tenant_when_org_missing() -> None:
    page = _icims_page(hiringOrganization=None)
    assert parse_icims_jd(ICIMS_URL, "sig", page).company == "sig"


def _icims_page(**overrides: Any) -> str:
    posting: dict[str, Any] = {
        "@context": "http://schema.org",
        "@type": "JobPosting",
        "title": "Intern",
        "description": "<p>Body text</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": [],
    }
    posting.update(overrides)
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(posting)
        + "</script></head><body></body></html>"
    )


def test_fetch_jd_routes_icims_and_forces_mobile_params(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_get_text(api_url: str, **kwargs: Any) -> str:
        called.append(api_url)
        return _icims_page()

    monkeypatch.setattr(jd_mod, "get_text", fake_get_text)
    # Slug in the path and a query that lacks the params: both must normalize.
    jd = fetch_jd("https://careers-sig.icims.com/jobs/11169/trading-system-eng/job")
    assert called == [ICIMS_API]
    assert jd.title == "Intern"


def test_fetch_jd_icims_410_reports_closed_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_text(api_url: str, **kwargs: Any) -> str:
        raise FetchError("GET ... -> HTTP 410", status=410)

    monkeypatch.setattr(jd_mod, "get_text", fake_get_text)
    with pytest.raises(JDError, match="is closed"):
        fetch_jd(ICIMS_URL)


def test_fetch_jd_icims_other_http_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_text(api_url: str, **kwargs: Any) -> str:
        raise FetchError("GET ... -> HTTP 503", status=503)

    monkeypatch.setattr(jd_mod, "get_text", fake_get_text)
    with pytest.raises(FetchError):
        fetch_jd(ICIMS_URL)


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
    (ORACLE_URL, "", ORACLE_API, "jd_oracle_job.json"),
    (
        # Apply-page copy: the id is found by scanning for the "job" segment,
        # so trailing segments and a different locale must not shift it.
        "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en-US/"
        "sites/CX_1/job/26011990/apply",
        "",
        ORACLE_API,
        "jd_oracle_job.json",
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
        ("https://careers-sig.icims.com/search?q=intern", "unrecognized icims"),
        ("https://careers-sig.icims.com/jobs/not-a-number/job", "unrecognized icims"),
        (
            "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
            "unrecognized oracle",
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
