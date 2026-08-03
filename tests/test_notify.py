import io
import json
import urllib.request
from typing import Any

import pytest

from intern_radar.models import Posting
from intern_radar.notify import (
    DISCORD_MESSAGE_LIMIT,
    _discord_chunks,
    format_issue_body,
    format_lines,
    notify_discord,
    notify_github_issue,
)


def posting(company: str, title: str, n: int = 0) -> Posting:
    return Posting(
        key=f"t:{company}:{n}", source="greenhouse", company=company, title=title,
        url=f"https://x.example/{company.lower()}/{n}",
        locations=("New York, NY",), posted_at="2026-08-03",
    )


def test_format_lines_sorted_and_checkboxed() -> None:
    lines = format_lines([posting("Zebra", "SWE Intern"), posting("Acme", "Data Intern")])
    assert lines[0].startswith("- [ ] **Acme**")
    assert "[Data Intern](https://x.example/acme/0)" in lines[0]
    assert "New York, NY" in lines[0]
    assert lines[1].startswith("- [ ] **Zebra**")


def test_issue_body_truncates() -> None:
    many = [posting("C", "Intern " + "x" * 200, n=i) for i in range(600)]
    body = format_issue_body(many)
    assert len(body) < 61_000
    assert "truncated" in body


def test_discord_chunks_respect_limit() -> None:
    lines = [f"line {i} " + "y" * 150 for i in range(50)]
    chunks = _discord_chunks(lines)
    assert all(len(c) <= DISCORD_MESSAGE_LIMIT for c in chunks)
    assert "\n".join(chunks).count("line ") == 50  # nothing dropped


class FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def test_github_issue_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        captured.append(request)
        return FakeResponse(b"{}")

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "KernSharma/intern-radar")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    notify_github_issue([posting("Acme", "SWE Intern")])

    assert len(captured) == 1
    req = captured[0]
    assert req.full_url == "https://api.github.com/repos/KernSharma/intern-radar/issues"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.data is not None
    payload = json.loads(req.data.decode("utf-8"))
    assert "1 new internship posting(s)" in payload["title"]
    assert "**Acme**" in payload["body"]


def test_github_issue_skips_without_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    calls: list[object] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    notify_github_issue([posting("Acme", "SWE Intern")])
    assert not calls
    assert "skipping" in capsys.readouterr().out


def test_discord_noop_without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    calls: list[object] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    notify_discord([posting("Acme", "SWE Intern")])
    assert not calls
