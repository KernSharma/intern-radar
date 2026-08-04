from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "intern-radar/0.1 (+https://github.com/KernSharma/intern-radar)"


class FetchError(Exception):
    pass


def _request_json(request: urllib.request.Request, *, timeout: float, retries: int) -> Any:
    last_error: Exception | None = None
    url = request.full_url
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"{request.get_method()} {url} -> HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    raise FetchError(
        f"{request.get_method()} {url} failed after {retries + 1} attempts: {last_error}"
    ) from last_error


def get_json(url: str, *, timeout: float = 30.0, retries: int = 2) -> Any:
    """GET a URL and parse JSON, retrying transient failures with backoff."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _request_json(request, timeout=timeout, retries=retries)


def post_json(url: str, payload: Any, *, timeout: float = 30.0, retries: int = 2) -> Any:
    """POST a JSON body and parse the JSON response (Workday CXS style)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    return _request_json(request, timeout=timeout, retries=retries)
