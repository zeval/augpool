from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from augpool.analytics import (
    cache_is_fresh,
    default_date_window,
    get_fresh_usage,
    map_records_to_accounts,
    refresh_usage,
    save_usage_cache,
)
from augpool.pool import Account, load_pool, save_pool
from augpool.session_io import write_json_atomic


def test_default_window_uses_current_calendar_month():
    start, end = default_date_window(date(2026, 7, 29))
    assert end == "2026-07-29"
    assert start == "2026-07-01"


def test_default_window_resets_on_first_day_of_month():
    start, end = default_date_window(date(2026, 8, 1))
    assert start == "2026-08-01"
    assert end == "2026-08-01"


def test_map_records(two_account_pool, credit_records, home: Path):
    pool = load_pool(home)
    mapped = map_records_to_accounts(credit_records, pool.accounts)
    assert mapped["alice@acme.com"] == 4200
    assert mapped["bob@acme.com"] == 1850


def test_refresh_uses_session_access_token(two_account_pool, credit_records, home: Path):
    pool = load_pool(home)
    tokens_seen: list[str] = []

    def fake_get(url: str, headers: dict) -> dict:
        auth = headers["Authorization"]
        assert auth.startswith("Bearer ")
        tokens_seen.append(auth.removeprefix("Bearer "))
        assert "credit-usage-by-user" in url
        return {"records": credit_records, "pagination": {"has_more": False}}

    cache = refresh_usage(pool, root=home, http_get=fake_get, now=1_000.0)
    assert len(tokens_seen) == 1
    assert tokens_seen[0] in {"tok-alice", "tok-bob"}
    assert cache["fetches_ok"] == 1
    assert cache["tenants_queried"] == 1
    assert cache["by_id"]["bob@acme.com"] == 1850
    assert cache["by_id"]["alice@acme.com"] == 4200
    assert cache_is_fresh(cache, ttl_seconds=300, now=1_000.0 + 10)
    assert not cache_is_fresh(cache, ttl_seconds=300, now=1_000.0 + 301)

    on_disk = json.loads((home / "cache" / "usage.json").read_text())
    assert on_disk["by_id"]["alice@acme.com"] == 4200


def test_refresh_one_call_per_tenant(two_account_pool, credit_records, home: Path):
    from augpool.pool import creds_filename

    pool = load_pool(home)
    email = "carol@other.com"
    rel = f"creds/{creds_filename(email)}"
    write_json_atomic(
        home / rel,
        {
            "accessToken": "tok-carol",
            "tenantURL": "https://other.api.augmentcode.com/",
            "scopes": [],
        },
        mode=0o600,
    )
    pool.accounts.append(
        Account(
            email=email,
            session_path=rel,
            tenant_url="https://other.api.augmentcode.com/",
        )
    )
    save_pool(pool, home)

    calls = {"n": 0}

    def fake_get(url: str, headers: dict) -> dict:
        calls["n"] += 1
        return {
            "records": credit_records
            + [{"user_email": "carol@other.com", "credits_consumed": 50}],
            "pagination": {"has_more": False},
        }

    cache = refresh_usage(pool, root=home, http_get=fake_get, now=2_000.0)
    assert calls["n"] == 2
    assert cache["fetches_ok"] == 2
    assert cache["tenants_queried"] == 2
    assert cache["by_id"]["carol@other.com"] == 50
    assert cache["by_id"]["alice@acme.com"] == 4200


def test_refresh_missing_session_records_error(two_account_pool, home: Path):
    pool = load_pool(home)
    for a in pool.accounts:
        (home / a.session_path).unlink()
    cache = refresh_usage(pool, root=home, now=1.0)
    assert cache["errors"]
    assert "no session accessToken" in cache["errors"][0]
    assert cache["fetches_ok"] == 0


def test_get_fresh_usage_refreshes_when_expired(two_account_pool, credit_records, home: Path):
    pool = load_pool(home)
    pool.usage_cache_ttl_seconds = 300
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
            "start_date": "2026-01-01",
            "end_date": "2026-01-30",
            "by_id": {"alice@acme.com": 1.0, "bob@acme.com": 1.0},
            "errors": [],
            "fetches_ok": 1,
        },
        home,
    )
    calls = {"n": 0}

    def fake_get(url: str, headers: dict) -> dict:
        calls["n"] += 1
        return {"records": credit_records, "pagination": {"has_more": False}}

    usage = get_fresh_usage(
        pool, root=home, http_get=fake_get, now=1_000.0 + 301, refresh_if_stale=True
    )
    assert calls["n"] == 1
    assert usage["bob@acme.com"] == 1850

    usage2 = get_fresh_usage(
        pool, root=home, http_get=fake_get, now=1_000.0 + 301 + 10, refresh_if_stale=True
    )
    assert calls["n"] == 1
    assert usage2["bob@acme.com"] == 1850


def test_get_fresh_usage_refreshes_fresh_cache_from_previous_month(
    two_account_pool, home: Path
):
    pool = load_pool(home)
    pool.usage_cache_ttl_seconds = 300
    today = datetime.now(timezone.utc).date()
    current_start = today.replace(day=1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
            "start_date": previous_start.isoformat(),
            "end_date": previous_end.isoformat(),
            "by_id": {"alice@acme.com": 2_200_000.0, "bob@acme.com": 800_000.0},
            "errors": [],
            "fetches_ok": 1,
        },
        home,
    )
    calls = {"n": 0}

    def fake_get(url: str, headers: dict) -> dict:
        calls["n"] += 1
        assert f"start_date={current_start.isoformat()}" in url
        assert f"end_date={today.isoformat()}" in url
        return {
            "records": [
                {"user_email": "alice@acme.com", "credits_consumed": 120_000},
                {"user_email": "bob@acme.com", "credits_consumed": 45_000},
            ],
            "pagination": {"has_more": False},
        }

    usage = get_fresh_usage(
        pool,
        root=home,
        http_get=fake_get,
        now=1_010.0,
        refresh_if_stale=True,
    )

    assert calls["n"] == 1
    assert usage == {"alice@acme.com": 120_000.0, "bob@acme.com": 45_000.0}
    cache = json.loads((home / "cache" / "usage.json").read_text())
    assert cache["start_date"] == current_start.isoformat()
    assert cache["end_date"] == today.isoformat()


def test_get_fresh_usage_keeps_stale_on_failed_refresh(two_account_pool, home: Path):
    pool = load_pool(home)
    pool.usage_cache_ttl_seconds = 300
    today = datetime.now(timezone.utc).date()
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "by_id": {"alice@acme.com": 99.0, "bob@acme.com": 11.0},
            "errors": [],
            "fetches_ok": 1,
        },
        home,
    )

    def boom(url: str, headers: dict) -> dict:
        raise RuntimeError("analytics HTTP 500: nope")

    usage = get_fresh_usage(
        pool, root=home, http_get=boom, now=1_000.0 + 999, refresh_if_stale=True
    )
    assert usage == {"alice@acme.com": 99.0, "bob@acme.com": 11.0}


def test_get_fresh_usage_rejects_previous_month_fallback_when_refresh_fails(
    two_account_pool, home: Path
):
    pool = load_pool(home)
    today = datetime.now(timezone.utc).date()
    current_start = today.replace(day=1)
    previous_end = current_start - timedelta(days=1)
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
            "start_date": previous_end.replace(day=1).isoformat(),
            "end_date": previous_end.isoformat(),
            "by_id": {"alice@acme.com": 2_200_000.0, "bob@acme.com": 800_000.0},
            "errors": [],
            "fetches_ok": 1,
        },
        home,
    )

    def boom(url: str, headers: dict) -> dict:
        raise RuntimeError("analytics HTTP 500: nope")

    usage = get_fresh_usage(
        pool,
        root=home,
        http_get=boom,
        now=1_010.0,
        refresh_if_stale=True,
    )

    assert usage is None
    cache = json.loads((home / "cache" / "usage.json").read_text())
    assert cache["start_date"] == current_start.isoformat()
    assert cache["end_date"] == today.isoformat()
    assert cache["fetches_ok"] == 0
    assert cache["errors"]


def test_get_fresh_usage_retries_fresh_cache_with_no_successful_fetches(
    two_account_pool, credit_records, home: Path
):
    pool = load_pool(home)
    today = datetime.now(timezone.utc).date()
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "by_id": {"alice@acme.com": 0.0, "bob@acme.com": 0.0},
            "errors": ["analytics HTTP 500: nope"],
            "fetches_ok": 0,
        },
        home,
    )
    calls = {"n": 0}

    def fake_get(url: str, headers: dict) -> dict:
        calls["n"] += 1
        return {"records": credit_records, "pagination": {"has_more": False}}

    usage = get_fresh_usage(
        pool,
        root=home,
        http_get=fake_get,
        now=1_010.0,
        refresh_if_stale=True,
    )

    assert calls["n"] == 1
    assert usage == {"alice@acme.com": 4_200.0, "bob@acme.com": 1_850.0}
