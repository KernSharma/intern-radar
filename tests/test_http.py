import io
import json
import urllib.request
from typing import Any

import pytest

from intern_radar.http import post_json


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
