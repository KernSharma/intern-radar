from pathlib import Path

from intern_radar.models import Posting, normalize_url
from intern_radar.state import SeenStore


def posting(key: str, url: str) -> Posting:
    return Posting(key=key, source="test", company="X", title="T", url=url)


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    store = SeenStore.load(path)  # missing file -> empty store
    p = posting("a:1", "https://x.example/jobs/1")
    assert not store.is_seen(p)
    store.mark(p, "2026-08-03")
    store.save()

    reloaded = SeenStore.load(path)
    assert reloaded.is_seen(p)
    assert reloaded.seen["a:1"] == "2026-08-03"


def test_seen_by_url_across_sources(tmp_path: Path) -> None:
    store = SeenStore.load(tmp_path / "seen.json")
    via_simplify = posting("simplify:abc", "https://jobs.lever.co/acme/123/apply")
    via_lever = posting("lever:acme:123", "https://jobs.lever.co/acme/123")
    store.mark(via_simplify, "2026-08-03")
    # Different key, same underlying job URL (modulo /apply) -> already seen.
    assert store.is_seen(via_lever)


def test_url_normalization() -> None:
    assert normalize_url("HTTPS://Example.com/a/?utm_source=x") == "https://example.com/a"
    assert (
        normalize_url("https://stripe.com/jobs/search?gh_jid=7954688")
        == "https://stripe.com/jobs/search?gh_jid=7954688"
    )
    assert (
        normalize_url("https://jobs.lever.co/acme/123/apply")
        == normalize_url("https://jobs.lever.co/acme/123")
    )


def test_url_normalization_ashby_aggregator_forms() -> None:
    # Simplify links Ashby jobs as /application?embed=true with its own slug
    # casing; the Ashby API returns the bare jobUrl. All three must dedupe.
    bare = normalize_url("https://jobs.ashbyhq.com/perplexity/79a07e2d-abc")
    assert normalize_url("https://jobs.ashbyhq.com/Perplexity/79a07e2d-abc/application") == bare
    assert (
        normalize_url("https://jobs.ashbyhq.com/Perplexity/79a07e2d-abc/application?embed=true")
        == bare
    )


def test_url_normalization_drops_duplicated_params() -> None:
    # Seen live: jobs.solarwinds.com/job-detail/?gh_jid=X&gh_jid=X
    assert (
        normalize_url("https://x.example/job/?gh_jid=1&gh_jid=1")
        == normalize_url("https://x.example/job?gh_jid=1")
    )


def test_prune_drops_only_stale(tmp_path: Path) -> None:
    store = SeenStore.load(tmp_path / "seen.json")
    store.mark(posting("old:1", "https://x.example/old"), "2024-01-01")
    store.mark(posting("new:1", "https://x.example/new"), "2026-08-01")
    dropped = store.prune("2026-08-03")
    assert dropped == 2  # old key + old url entry
    assert "new:1" not in [k for k in store.seen if k.startswith("old")]
    assert store.seen.keys() == {"new:1", posting("new:1", "https://x.example/new").url_key}


def test_mark_refreshes_last_seen_date(tmp_path: Path) -> None:
    # Entries hold last-seen, not first-seen: a posting that stays live past
    # the prune horizon must not be pruned and re-notified.
    store = SeenStore.load(tmp_path / "seen.json")
    p = posting("a:1", "https://x.example/1")
    store.mark(p, "2025-08-01")
    store.mark(p, "2026-08-03")
    assert store.seen["a:1"] == "2026-08-03"
    assert store.prune("2026-08-03") == 0


def test_inbox_appends_and_dedups(tmp_path: Path) -> None:
    import json

    from intern_radar.state import append_inbox

    path = tmp_path / "inbox.json"
    a = Posting(key="a:1", source="lever", company="Acme", title="SWE Intern",
                url="https://jobs.lever.co/acme/1", locations=("NYC", "Remote"))
    b = Posting(key="b:2", source="ashby", company="Bcorp", title="ML Intern",
                url="https://jobs.ashbyhq.com/bcorp/2")
    assert append_inbox(path, [a], "2026-08-04") == 1
    # Re-notifying a still-queued URL must not double it; new ones append.
    assert append_inbox(path, [a, b], "2026-08-05") == 1

    entries = json.loads(path.read_text(encoding="utf-8"))
    assert [e["url"] for e in entries] == [a.url, b.url]
    assert entries[0] == {"url": a.url, "company": "Acme", "title": "SWE Intern",
                          "locations": "NYC, Remote", "source": "lever",
                          "added": "2026-08-04"}


def test_inbox_survives_consumer_rewrite(tmp_path: Path) -> None:
    from intern_radar.state import append_inbox

    path = tmp_path / "inbox.json"
    # The consumer empties the file to [] after processing.
    path.write_text("[]", encoding="utf-8")
    p = Posting(key="a:1", source="lever", company="Acme", title="T",
                url="https://jobs.lever.co/acme/1")
    assert append_inbox(path, [p], "2026-08-04") == 1
    assert append_inbox(path, [], "2026-08-04") == 0  # no-op writes nothing new


def test_inbox_tolerates_utf8_bom(tmp_path: Path) -> None:
    # A BOM from a Windows editor crashed the watcher post-notify (duplicate
    # notifications every run) on 2026-08-05 — never again.
    from intern_radar.state import append_inbox

    path = tmp_path / "inbox.json"
    path.write_bytes(b"\xef\xbb\xbf[]")
    p = Posting(key="a:1", source="lever", company="Acme", title="T",
                url="https://jobs.lever.co/acme/1")
    assert append_inbox(path, [p], "2026-08-05") == 1
