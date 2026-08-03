from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime

from intern_radar.http import USER_AGENT
from intern_radar.models import Posting

DISCORD_MESSAGE_LIMIT = 2000
GITHUB_ISSUE_BODY_LIMIT = 60_000


class NotifyError(Exception):
    pass


def format_lines(postings: list[Posting]) -> list[str]:
    lines: list[str] = []
    for p in sorted(postings, key=lambda p: (p.company.lower(), p.title.lower())):
        locations = ", ".join(p.locations[:3]) + (" …" if len(p.locations) > 3 else "")
        extras = " · ".join(x for x in (locations, ", ".join(p.terms), p.posted_at) if x)
        suffix = f" ({extras})" if extras else ""
        lines.append(f"- [ ] **{p.company}** — [{p.title}]({p.url}){suffix}")
    return lines


def format_issue_body(postings: list[Posting], failures: tuple[str, ...] = ()) -> str:
    lines = format_lines(postings)
    body = "\n".join(lines)
    if len(body) > GITHUB_ISSUE_BODY_LIMIT:
        body = body[:GITHUB_ISSUE_BODY_LIMIT] + "\n\n… truncated."
    if failures:
        body += "\n\n**⚠ Source failures this run** (these boards went unwatched):\n" + "\n".join(
            f"- {f}" for f in failures
        )
    return body + "\n\n_Found by intern-radar. Check a box once you've applied._"


def notify_console(postings: list[Posting]) -> None:
    for line in format_lines(postings):
        print(line)


def notify_github_issue(postings: list[Posting], failures: tuple[str, ...] = ()) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        # Failing loudly matters: silently skipping would mark these postings
        # seen without any notification ever going out.
        raise NotifyError(
            "github_issues is enabled but GITHUB_TOKEN/GITHUB_REPOSITORY are not set "
            "(disable notify.github_issues in config.toml for local runs)"
        )
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    payload = {
        "title": f"{len(postings)} new internship posting(s) — {now}",
        "body": format_issue_body(postings, failures),
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201):
                raise NotifyError(f"github issue creation returned HTTP {response.status}")
    except OSError as e:
        raise NotifyError(f"github issue creation failed: {e}") from e


def _discord_chunks(lines: list[str], limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = line[: limit - 1]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def notify_discord(postings: list[Posting]) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return
    header = f"**{len(postings)} new internship posting(s)**"
    plain_lines = [
        f"{p.company} — {p.title}\n<{p.url}>"
        for p in sorted(postings, key=lambda p: (p.company.lower(), p.title.lower()))
    ]
    for chunk in _discord_chunks([header, *plain_lines]):
        request = urllib.request.Request(
            webhook_url,
            data=json.dumps({"content": chunk}).encode("utf-8"),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except OSError as e:
            raise NotifyError(f"discord webhook failed: {e}") from e
