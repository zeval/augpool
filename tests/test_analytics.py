from __future__ import annotations

import json
from pathlib import Path

from augpool.analytics import (
    cache_is_fresh,
    default_date_window,
    get_fresh_usage,
    get_usage_result,
    map_records_to_accounts,
    refresh_usage,
    save_usage_cache,
)
from augpool.pool import Account, load_pool, save_pool
from augpool.session_io import write_json_atomic


def test_default_window_30_days():
    from datetime import date

    start, end = default_date_window(date(2026, 7, 29))
    assert end == "2026-07-29"
    assert start == "2026-06-30"


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


def test_get_fresh_usage_keeps_stale_on_failed_refresh(two_account_pool, home: Path):
    pool = load_pool(home)
    pool.usage_cache_ttl_seconds = 300
    save_usage_cache(
        {
            "fetched_at": 1_000.0,
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


def test_usage_result_surfaces_failed_refresh_while_restoring_stale_cache(
    two_account_pool, home: Path
):
    pool = load_pool(home)
    stale_cache = {
        "fetched_at": 1_000.0,
        "start_date": "2026-01-01",
        "end_date": "2026-01-30",
        "by_id": {"alice@acme.com": 99.0, "bob@acme.com": 11.0},
        "errors": [],
        "fetches_ok": 1,
        "tenants_queried": 1,
    }
    save_usage_cache(stale_cache, home)

    def boom(url: str, headers: dict) -> dict:
        raise RuntimeError("analytics HTTP 500: nope")

    result = get_usage_result(
        pool,
        root=home,
        force=True,
        http_get=boom,
        now=2_000.0,
    )

    assert result.by_id == stale_cache["by_id"]
    assert result.cache == stale_cache
    assert result.refresh_attempted is True
    assert result.refresh_succeeded is False
    assert len(result.errors) == 2
    assert all(error.endswith("analytics HTTP 500: nope") for error in result.errors)
    assert any("alice@acme.com" in error for error in result.errors)
    assert any("bob@acme.com" in error for error in result.errors)
    assert json.loads((home / "cache" / "usage.json").read_text()) == stale_cache


def test_usage_result_keeps_warnings_from_usable_refresh(
    two_account_pool, credit_records, home: Path
):
    pool = load_pool(home)

    def one_token_fails(url: str, headers: dict) -> dict:
        if headers["Authorization"] == "Bearer tok-alice":
            raise RuntimeError("token expired")
        return {"records": credit_records, "pagination": {"has_more": False}}

    result = get_usage_result(
        pool,
        root=home,
        force=True,
        http_get=one_token_fails,
        now=2_000.0,
    )

    assert result.refresh_attempted is True
    assert result.refresh_succeeded is True
    assert result.by_id["alice@acme.com"] == 4200
    assert result.errors == [
        "https://e5.api.augmentcode.com/alice@acme.com: token expired"
    ]
