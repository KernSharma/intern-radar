from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from intern_radar.models import Posting, normalize_url

STATUSES = ("applied", "oa", "interview", "offer", "rejected", "withdrawn")
# Dashboard ordering: closest-to-offer first.
DISPLAY_ORDER = ("offer", "interview", "oa", "applied", "rejected", "withdrawn")


class TrackerError(Exception):
    pass


@dataclass
class Application:
    url: str
    company: str
    title: str
    status: str
    history: dict[str, str] = field(default_factory=dict)  # status -> ISO date
    notes: list[str] = field(default_factory=list)


def write_postings_cache(path: Path, postings: list[Posting]) -> None:
    """Snapshot matched postings so `track add <url>` can resolve metadata.

    Merges into the existing cache rather than replacing it: a partial-failure
    run must not evict a healthy source's metadata, and a posting you applied
    to stays resolvable after it closes.
    """
    payload: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        if isinstance(existing, dict):
            payload = existing
    for p in postings:
        payload[p.url_key] = {
            "company": p.company,
            "title": p.title,
            "url": p.url,
            "locations": list(p.locations),
            "posted_at": p.posted_at,
            "source": p.source,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(sorted(payload.items())), f, indent=1, ensure_ascii=False)
        f.write("\n")


@dataclass
class Tracker:
    path: Path
    apps: dict[str, Application] = field(default_factory=dict)  # url_key -> app

    @classmethod
    def load(cls, path: Path) -> Tracker:
        if not path.exists():
            return cls(path=path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        apps: dict[str, Application] = {}
        for key, raw in (data.get("applications", {}) if isinstance(data, dict) else {}).items():
            apps[str(key)] = Application(
                url=str(raw.get("url", "")),
                company=str(raw.get("company", "")),
                title=str(raw.get("title", "")),
                status=str(raw.get("status", "applied")),
                history={str(k): str(v) for k, v in raw.get("history", {}).items()},
                notes=[str(n) for n in raw.get("notes", [])],
            )
        return cls(path=path, apps=apps)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "applications": {k: asdict(a) for k, a in sorted(self.apps.items())},
        }
        with self.path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
            f.write("\n")

    def add(
        self,
        url: str,
        status: str,
        today: str,
        postings_cache: Path,
        *,
        company: str = "",
        title: str = "",
        note: str = "",
    ) -> Application:
        key = "url:" + normalize_url(url)
        if key in self.apps:
            raise TrackerError(f"already tracked ({self.apps[key].status}); use `track set`")
        if not (company and title):
            cached = _cache_lookup(postings_cache, key)
            if cached is not None:
                company = company or cached["company"]
                title = title or cached["title"]
        if not (company and title):
            raise TrackerError(
                "posting not in data/postings.json (not seen by the watcher yet?) — "
                "pass --company and --title explicitly"
            )
        app = Application(
            url=url, company=company, title=title, status=status, history={status: today},
            notes=[note] if note else [],
        )
        self.apps[key] = app
        return app

    def set_status(self, url: str, status: str, today: str, *, note: str = "") -> Application:
        key = "url:" + normalize_url(url)
        if key not in self.apps:
            raise TrackerError("not tracked yet; use `track add` first")
        app = self.apps[key]
        app.status = status
        app.history[status] = today
        if note:
            app.notes.append(note)
        return app


def _cache_lookup(postings_cache: Path, key: str) -> dict[str, str] | None:
    if not postings_cache.exists():
        return None
    with postings_cache.open("r", encoding="utf-8") as f:
        cache = json.load(f)
    entry = cache.get(key) if isinstance(cache, dict) else None
    if not isinstance(entry, dict):
        return None
    return {"company": str(entry.get("company", "")), "title": str(entry.get("title", ""))}


def _cell(text: str) -> str:
    """Titles and notes carry arbitrary text; keep the markdown table intact."""
    return " ".join(text.split()).replace("|", "\\|")


def render_dashboard(tracker: Tracker) -> str:
    apps = list(tracker.apps.values())
    counts = {s: sum(1 for a in apps if a.status == s) for s in DISPLAY_ORDER}
    funnel = " · ".join(f"{s}: {n}" for s, n in counts.items() if n)
    lines = [
        "# Applications",
        "",
        f"**{len(apps)} tracked** — {funnel or 'nothing yet'}",
        "",
    ]
    for status in DISPLAY_ORDER:
        group = sorted(
            (a for a in apps if a.status == status),
            key=lambda a: a.history.get(a.status, ""),
            reverse=True,
        )
        if not group:
            continue
        lines += [f"## {status} ({len(group)})", ""]
        lines += ["| Company | Role | Since | Notes |", "|---|---|---|---|"]
        for a in group:
            notes = _cell("; ".join(a.notes))
            lines.append(
                f"| {_cell(a.company)} | [{_cell(a.title)}]({a.url}) "
                f"| {a.history.get(a.status, '')} | {notes} |"
            )
        lines.append("")
    lines.append("_Managed by `python -m intern_radar track` — do not edit by hand._")
    return "\n".join(lines) + "\n"
