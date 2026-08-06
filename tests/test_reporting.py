from __future__ import annotations

import json
from pathlib import Path

from augpool.analytics import save_usage_cache
from augpool.pool import load_pool, save_pool
from augpool.reporting import collect_stats
from augpool.state import load_state, save_state


def test_collect_stats_returns_ranked_safe_snapshot(home: Path, two_account_pool):
    pool = load_pool(home)
    pool.active_email = "alice@acme.com"
    pool.accounts[0].label = "Alice"
    pool.accounts[0].notes = "never expose this note"
    pool.accounts[1].enabled = False
    save_pool(pool, home)

    state = load_state(home)
    alice_state = state.for_account("alice@acme.com")
    alice_state.local_uses = 3
    alice_state.last_selected_at = 1_800.0
    alice_state.cooldown_until = 2_500.0
    save_state(state, home)

    save_usage_cache(
        {
            "fetched_at": 1_900.0,
            "start_date": "2026-07-01",
            "end_date": "2026-07-30",
            "by_id": {"alice@acme.com": 120.0, "bob@acme.com": 50.0},
            "errors": ["cached partial warning"],
            "fetches_ok": 1,
            "tenants_queried": 2,
        },
        home,
    )

    snapshot = collect_stats(home, now=2_000.0)

    assert snapshot["schema_version"] == 1
    assert snapshot["generated_at"] == "1970-01-01T00:33:20Z"
    assert snapshot["home"] == str(home.resolve())
    assert snapshot["active_email"] == "alice@acme.com"
    assert snapshot["strategy"] == "least_used"
    assert snapshot["usage"] == {
        "fetched_at": 1_900.0,
        "age_seconds": 100,
        "ttl_seconds": 300,
        "stale": False,
        "start_date": "2026-07-01",
        "end_date": "2026-07-30",
        "refresh_attempted": False,
        "refresh_succeeded": False,
        "errors": ["cached partial warning"],
        "fetches_ok": 1,
        "tenants_queried": 2,
    }

    assert snapshot["accounts"][0] == {
        "email": "alice@acme.com",
        "label": "Alice",
        "enabled": True,
        "weight": 1.0,
        "active": True,
        "credits_consumed": 120.0,
        "score": 120.0,
        "local_uses": 3,
        "source": "analytics",
        "last_selected_at": 1_800.0,
        "in_cooldown": True,
        "cooldown_until": 2_500.0,
    }
    assert snapshot["accounts"][1] == {
        "email": "bob@acme.com",
        "label": "bob@acme.com",
        "enabled": False,
        "weight": 1.0,
        "active": False,
        "credits_consumed": None,
        "score": None,
        "local_uses": None,
        "source": None,
        "last_selected_at": None,
        "in_cooldown": False,
        "cooldown_until": None,
    }

    encoded = json.dumps(snapshot)
    for forbidden in (
        "session_path",
        "tenant_url",
        "notes",
        "tok-alice",
        "never expose this note",
    ):
        assert forbidden not in encoded
