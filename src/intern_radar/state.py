from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from intern_radar.models import Posting

PRUNE_AFTER_DAYS = 365


@dataclass
class SeenStore:
    path: Path
    seen: dict[str, str] = field(default_factory=dict)  # key -> last-seen ISO date

    @classmethod
    def load(cls, path: Path) -> SeenStore:
        if not path.exists():
            return cls(path=path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        seen_raw = data.get("seen", {}) if isinstance(data, dict) else {}
        seen = {str(k): str(v) for k, v in seen_raw.items()}
        return cls(path=path, seen=seen)

    def is_seen(self, posting: Posting) -> bool:
        return posting.key in self.seen or posting.url_key in self.seen

    def mark(self, posting: Posting, today: str) -> None:
        # Overwrite, not setdefault: entries hold the LAST date the posting was
        # observed live, so prune only drops postings that have disappeared.
        self.seen[posting.key] = today
        self.seen[posting.url_key] = today

    def prune(self, today: str) -> int:
        """Drop entries not observed live for PRUNE_AFTER_DAYS. Bounds the
        state file's growth without re-notifying long-lived postings."""
        cutoff = (
            datetime.fromisoformat(today).replace(tzinfo=UTC)
            - timedelta(days=PRUNE_AFTER_DAYS)
        ).date().isoformat()
        stale = [k for k, last_seen in self.seen.items() if last_seen < cutoff]
        for k in stale:
            del self.seen[k]
        return len(stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "seen": dict(sorted(self.seen.items()))}
        with self.path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
            f.write("\n")


def append_inbox(path: Path, postings: list[Posting], today: str) -> int:
    """Queue notified postings for the downstream auto-tailor agent.

    The inbox is a committed JSON list; the consumer removes entries it has
    processed and commits the shrunken file. Deduped by URL so a posting
    re-notified while still queued isn't doubled. Returns entries added.
    """
    entries: list[dict[str, str]] = []
    if path.exists():
        # utf-8-sig: a BOM from a Windows editor must not kill the watcher
        # mid-run (post-notify, pre-state-save => duplicate notifications).
        with path.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            entries = [
                {str(k): str(v) for k, v in e.items()} for e in raw if isinstance(e, dict)
            ]
    known = {e.get("url", "") for e in entries}
    added = 0
    for p in postings:
        if p.url in known:
            continue
        entries.append(
            {
                "url": p.url,
                "company": p.company,
                "title": p.title,
                "locations": ", ".join(p.locations),
                "source": p.source,
                "added": today,
            }
        )
        added += 1
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(entries, f, indent=1, ensure_ascii=False)
            f.write("\n")
    return added
