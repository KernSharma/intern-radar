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


def test_prune_drops_only_stale(tmp_path: Path) -> None:
    store = SeenStore.load(tmp_path / "seen.json")
    store.mark(posting("old:1", "https://x.example/old"), "2024-01-01")
    store.mark(posting("new:1", "https://x.example/new"), "2026-08-01")
    dropped = store.prune("2026-08-03")
    assert dropped == 2  # old key + old url entry
    assert "new:1" not in [k for k in store.seen if k.startswith("old")]
    assert store.seen.keys() == {"new:1", posting("new:1", "https://x.example/new").url_key}


def test_mark_keeps_first_seen_date(tmp_path: Path) -> None:
    store = SeenStore.load(tmp_path / "seen.json")
    p = posting("a:1", "https://x.example/1")
    store.mark(p, "2026-08-01")
    store.mark(p, "2026-08-03")
    assert store.seen["a:1"] == "2026-08-01"
