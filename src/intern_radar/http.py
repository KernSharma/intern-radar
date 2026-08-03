from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "intern-radar/0.1 (+https://github.com/KernSharma/intern-radar)"


class FetchError(Exception):
    pass


def get_json(url: str, *, timeout: float = 30.0, retries: int = 2) -> Any:
    """GET a URL and parse JSON, retrying transient failures with backoff."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"GET {url} -> HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    raise FetchError(f"GET {url} failed after {retries + 1} attempts: {last_error}") from last_error
