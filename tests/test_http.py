import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from intern_radar.http import FetchError, get_json, get_text, post_json


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def test_post_json_sends_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        captured.append(request)
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = post_json("https://x.example/jobs", {"limit": 20, "searchText": "intern"})
    assert result == {"ok": True}
    req = captured[0]
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert req.data is not None
    assert json.loads(req.data.decode("utf-8")) == {"limit": 20, "searchText": "intern"}


def test_get_text_returns_body_and_asks_for_html(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        captured.append(request)
        return FakeResponse(b"<html>job page</html>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert get_text("https://x.example/jobs/1/job") == "<html>job page</html>"
    assert captured[0].get_header("Accept") == "text/html"


def test_get_text_survives_undecodable_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    # One bad byte in a job page must not lose the whole posting.
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda r, timeout=0: FakeResponse(b"caf\xe9 job")
    )
    assert "job" in get_text("https://x.example/j")


def test_fetch_error_carries_the_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    # jd.py keys off this to tell a closed iCIMS req (410) from a real outage.
    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        raise urllib.error.HTTPError("https://x.example/j", 410, "Gone", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(FetchError) as excinfo:
        get_text("https://x.example/j")
    assert excinfo.value.status == 410


def test_malformed_json_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # A truncated body is as transient as a dropped connection: retry it.
    bodies = [b"{not json", b"{still not json", b'{"ok": true}']

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        return FakeResponse(bodies.pop(0))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("intern_radar.http.time.sleep", lambda s: None)
    assert get_json("https://x.example/j") == {"ok": True}
    assert bodies == []
