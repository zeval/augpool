from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from augpool.pool import Account, load_pool
from augpool.state import load_state, set_cooldown
from augpool.usage_view import (
    format_compact,
    format_relative_time,
    format_sparkline,
    render_usage_dashboard,
    usage_report_payload,
)


def test_format_compact_uses_short_readable_units():
    assert format_compact(None) == "—"
    assert format_compact(999) == "999"
    assert format_compact(1_250) == "1.2k"
    assert format_compact(2_000_000) == "2m"


def test_format_relative_time_describes_recent_activity():
    now = 10_000.0
    assert format_relative_time(None, now=now) == "never"
    assert format_relative_time(now - 20, now=now) == "just now"
    assert format_relative_time(now - 120, now=now) == "2m ago"
    assert format_relative_time(now - 7_200, now=now) == "2h ago"
    assert format_relative_time(now - 172_800, now=now) == "2d ago"


def test_format_sparkline_preserves_zero_days_and_relative_peaks():
    assert format_sparkline([0, 0, 0]) == "···"
    assert format_sparkline([0, 1, 2, 4, 8]) == "·▁▂▄█"


def test_dashboard_shows_account_credit_share_and_local_sessions(
    two_account_pool, home: Path
):
    pool = load_pool(home)
    pool.active_email = "bob@acme.com"
    state = load_state(home)
    alice = state.for_account("alice@acme.com")
    alice.local_uses = 5
    alice.last_selected_at = 9_880.0
    bob = state.for_account("bob@acme.com")
    bob.local_uses = 2
    bob.last_selected_at = 9_000.0

    output = render_usage_dashboard(
        pool,
        state,
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
        {
            "fetched_at": 9_950.0,
            "start_date": "2026-07-08",
            "end_date": "2026-08-06",
            "errors": [],
        },
        width=88,
        color=False,
        now=10_000.0,
    )

    assert "augpool usage" in output
    assert "6k credits" in output
    assert "7 sessions" in output
    assert "alice@acme.com" in output
    assert "4.2k" in output
    assert "70.0%" in output
    assert "5 sessions" in output
    assert "used 2m ago" in output
    assert "bob@acme.com" in output
    assert "active" in output
    assert "2026-07-08 → 2026-08-06" in output
    assert "updated just now" in output
    assert "Sessions are local account selections" in output


def test_dashboard_shows_30_day_session_timeline(two_account_pool, home: Path):
    now = datetime(2026, 8, 6, 12, tzinfo=timezone.utc).timestamp()
    state = load_state(home)
    state.for_account("alice@acme.com").sessions_by_day = {
        "2026-07-08": 1,
        "2026-08-05": 2,
        "2026-08-06": 1,
    }
    state.for_account("bob@acme.com").sessions_by_day = {"2026-08-05": 3}

    output = render_usage_dashboard(
        load_pool(home),
        state,
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
        None,
        width=88,
        color=False,
        now=now,
    )

    assert "Sessions over time · 30d UTC · 7 tracked" in output
    assert "07-08" in output
    assert "08-06" in output
    assert "No dated sessions yet" not in output


def test_dashboard_explains_when_dated_history_starts(two_account_pool, home: Path):
    output = render_usage_dashboard(
        load_pool(home),
        load_state(home),
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
        None,
        width=72,
        color=False,
        now=datetime(2026, 8, 6, 12, tzinfo=timezone.utc).timestamp(),
    )

    assert "Sessions over time · 30d UTC · 0 tracked" in output
    assert "No dated sessions yet · tracking starts after upgrade" in output


def test_dashboard_marks_cooldown_disabled_and_non_default_weight(
    two_account_pool, home: Path
):
    pool = load_pool(home)
    pool.accounts[0].weight = 2.0
    pool.accounts.append(
        Account(
            email="off@acme.com",
            session_path="creds/off.json",
            enabled=False,
        )
    )
    state = load_state(home)
    set_cooldown(state, "alice@acme.com", 300, now=9_900.0)
    state.for_account("off@acme.com").local_uses = 3

    output = render_usage_dashboard(
        pool,
        state,
        {"alice@acme.com": 100.0, "bob@acme.com": 200.0},
        None,
        width=88,
        color=False,
        now=10_000.0,
    )

    assert "cooldown 3m" in output
    assert "weight 2×" in output
    assert "off@acme.com" in output
    assert "disabled" in output
    assert "3 sessions" in output
    assert "—  disabled" in output
    assert "100.0%  disabled" not in output


def test_dashboard_fits_narrow_terminal(two_account_pool, home: Path):
    output = render_usage_dashboard(
        load_pool(home),
        load_state(home),
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_850.0},
        None,
        width=44,
        color=False,
        now=10_000.0,
    )

    assert all(len(line) <= 44 for line in output.splitlines())
    assert "alice@acme.com" in output
    assert "bob@acme.com" in output


def test_dashboard_fits_minimum_terminal(two_account_pool, home: Path):
    now = datetime(2026, 8, 6, 12, tzinfo=timezone.utc).timestamp()
    state = load_state(home)
    set_cooldown(state, "alice@acme.com", 300, now=now - 100)
    output = render_usage_dashboard(
        load_pool(home),
        state,
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_850.0},
        None,
        width=24,
        color=False,
        now=now,
    )

    assert all(len(line) <= 24 for line in output.splitlines())
    assert "4.2k cooldown 3m" in output


def test_dashboard_only_adds_ansi_when_color_enabled(two_account_pool, home: Path):
    args = (
        load_pool(home),
        load_state(home),
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_850.0},
        None,
    )

    plain = render_usage_dashboard(*args, width=72, color=False, now=10_000.0)
    colored = render_usage_dashboard(*args, width=72, color=True, now=10_000.0)

    assert "\x1b[" not in plain
    assert "\x1b[" in colored


def test_dashboard_explains_local_fallback(two_account_pool, home: Path):
    state = load_state(home)
    state.for_account("alice@acme.com").local_uses = 4

    output = render_usage_dashboard(
        load_pool(home),
        state,
        None,
        None,
        width=72,
        color=False,
        now=10_000.0,
    )

    assert "credits unavailable" in output
    assert "Usage bars use local sessions" in output


def test_dashboard_handles_empty_pool(home: Path):
    output = render_usage_dashboard(
        load_pool(home),
        load_state(home),
        None,
        None,
        width=72,
        color=False,
        now=10_000.0,
    )

    assert "No accounts yet" in output
    assert "augpool import --self --email you@company.com" in output


def test_usage_payload_contains_totals_and_account_details(two_account_pool, home: Path):
    pool = load_pool(home)
    state = load_state(home)
    state.for_account("alice@acme.com").local_uses = 5
    state.for_account("alice@acme.com").last_selected_at = 9_900.0
    cache = {
        "fetched_at": 9_950.0,
        "start_date": "2026-07-08",
        "end_date": "2026-08-06",
        "errors": ["partial tenant failure"],
    }

    payload = usage_report_payload(
        pool,
        state,
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
        cache,
        now=10_000.0,
    )

    assert payload["totals"] == {
        "accounts": 2,
        "enabled_accounts": 2,
        "credits_consumed": 6_000.0,
        "local_sessions": 5,
    }
    assert payload["window"]["age_seconds"] == 50
    assert payload["errors"] == ["partial tenant failure"]
    assert payload["accounts"][0]["email"] == "bob@acme.com"
    alice = next(row for row in payload["accounts"] if row["email"] == "alice@acme.com")
    assert alice["credit_share"] == 0.7
    assert alice["local_sessions"] == 5
    assert alice["last_selected_at"] == 9_900.0


def test_usage_payload_contains_daily_per_account_session_history(
    two_account_pool, home: Path
):
    now = datetime(2026, 8, 6, 12, tzinfo=timezone.utc).timestamp()
    state = load_state(home)
    alice = state.for_account("alice@acme.com")
    alice.local_uses = 10
    alice.sessions_by_day = {
        "2026-07-07": 9,
        "2026-07-08": 1,
        "2026-08-05": 2,
        "2026-08-06": 1,
    }
    bob = state.for_account("bob@acme.com")
    bob.local_uses = 5
    bob.sessions_by_day = {"2026-08-05": 3}

    payload = usage_report_payload(
        load_pool(home),
        state,
        {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
        None,
        now=now,
    )

    history = payload["session_history"]
    assert history["timezone"] == "UTC"
    assert history["start_date"] == "2026-07-08"
    assert history["end_date"] == "2026-08-06"
    assert history["tracked_sessions"] == 7
    assert len(history["by_day"]) == 30
    assert history["by_day"][0] == {
        "date": "2026-07-08",
        "sessions": 1,
        "accounts": {"alice@acme.com": 1},
    }
    assert history["by_day"][-1] == {
        "date": "2026-08-06",
        "sessions": 1,
        "accounts": {"alice@acme.com": 1},
    }
    busiest = next(day for day in history["by_day"] if day["date"] == "2026-08-05")
    assert busiest == {
        "date": "2026-08-05",
        "sessions": 5,
        "accounts": {"alice@acme.com": 2, "bob@acme.com": 3},
    }
    alice_row = next(
        row for row in payload["accounts"] if row["email"] == "alice@acme.com"
    )
    assert alice_row["tracked_sessions_30d"] == 4
