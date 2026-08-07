from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = "intern-radar/0.1 (+https://github.com/KernSharma/intern-radar)"


class FetchError(Exception):
    """A fetch that failed. `status` carries the HTTP code when there was one.

    Callers use `status` to tell a dead posting (iCIMS answers 410 for a
    closed req) apart from a transport failure, so they can say which it was.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _request(
    request: urllib.request.Request,
    *,
    timeout: float,
    retries: int,
    parse: Callable[[bytes], Any],
) -> Any:
    """Fetch with backoff and hand the body to `parse`.

    A body that `parse` rejects is retried alongside transport failures — a
    truncated JSON response is as transient as a dropped connection.
    """
    last_error: Exception | None = None
    url = request.full_url
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return parse(response.read())
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(
                    f"{request.get_method()} {url} -> HTTP {e.code}", status=e.code
                ) from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    status = last_error.code if isinstance(last_error, urllib.error.HTTPError) else None
    raise FetchError(
        f"{request.get_method()} {url} failed after {retries + 1} attempts: {last_error}",
        status=status,
    ) from last_error


def _request_json(request: urllib.request.Request, *, timeout: float, retries: int) -> Any:
    return _request(
        request,
        timeout=timeout,
        retries=retries,
        parse=lambda body: json.loads(body.decode("utf-8")),
    )


def get_json(url: str, *, timeout: float = 30.0, retries: int = 2) -> Any:
    """GET a URL and parse JSON, retrying transient failures with backoff."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _request_json(request, timeout=timeout, retries=retries)


def get_text(url: str, *, timeout: float = 30.0, retries: int = 2) -> str:
    """GET a URL and return its body as text (for boards with no JSON API)."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    # "replace": a stray bad byte in a 40 KB job page must not lose the posting.
    body = _request(
        request,
        timeout=timeout,
        retries=retries,
        parse=lambda raw: raw.decode("utf-8", "replace"),
    )
    return str(body)


def post_json(url: str, payload: Any, *, timeout: float = 30.0, retries: int = 2) -> Any:
    """POST a JSON body and parse the JSON response (Workday CXS style)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    return _request_json(request, timeout=timeout, retries=retries)
