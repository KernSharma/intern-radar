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
