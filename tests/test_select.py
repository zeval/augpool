from __future__ import annotations

import time
from pathlib import Path

import pytest

from augpool.pool import load_pool
from augpool.select import pick, rank_accounts
from augpool.state import empty_state, record_selection, set_cooldown


def test_least_used_by_analytics(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    usage = {"alice@acme.com": 4200.0, "bob@acme.com": 1850.0}
    ranked = rank_accounts(pool, state, usage)
    assert ranked[0].account.email == "bob@acme.com"
    assert ranked[1].account.email == "alice@acme.com"
    assert pick(pool, state, usage).account.email == "bob@acme.com"


def test_local_fallback_and_tiebreak(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    record_selection(state, "bob@acme.com", now=100.0)
    record_selection(state, "bob@acme.com", now=101.0)
    assert pick(pool, state, None).account.email == "alice@acme.com"


def test_cooldown_skipped(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    usage = {"alice@acme.com": 100.0, "bob@acme.com": 200.0}
    now = time.time()
    set_cooldown(state, "alice@acme.com", 600, now=now)
    assert pick(pool, state, usage, now=now).account.email == "bob@acme.com"


def test_weight_prefers_heavier_capacity(two_account_pool, home: Path):
    pool = load_pool(home)
    pool.get("alice@acme.com").weight = 10.0
    pool.get("bob@acme.com").weight = 1.0
    state = empty_state()
    usage = {"alice@acme.com": 900.0, "bob@acme.com": 100.0}
    assert pick(pool, state, usage).account.email == "alice@acme.com"


def test_all_in_cooldown_raises(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    now = 1_000_000.0
    set_cooldown(state, "alice@acme.com", 60, now=now)
    set_cooldown(state, "bob@acme.com", 60, now=now)
    with pytest.raises(RuntimeError, match="no available"):
        pick(pool, state, {}, now=now)
