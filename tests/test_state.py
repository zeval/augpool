from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from augpool.state import (
    AccountState,
    State,
    load_state,
    record_selection,
    save_state,
    state_from_dict,
)


def _utc_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_legacy_state_migrates_without_inventing_dated_sessions():
    state = state_from_dict(
        {
            "version": 1,
            "accounts": {
                "alice@example.com": {
                    "local_uses": 17,
                    "last_selected_at": 1_000.0,
                }
            },
        }
    )

    account = state.for_account("alice@example.com")
    assert state.version == 2
    assert account.local_uses == 17
    assert account.sessions_by_day == {}


def test_record_selection_buckets_sessions_by_utc_day():
    state = State()

    record_selection(state, "alice@example.com", now=_utc_timestamp("2026-08-05T23:59:00"))
    record_selection(state, "alice@example.com", now=_utc_timestamp("2026-08-05T23:59:30"))
    record_selection(state, "alice@example.com", now=_utc_timestamp("2026-08-06T00:00:00"))

    account = state.for_account("alice@example.com")
    assert account.local_uses == 3
    assert account.sessions_by_day == {
        "2026-08-05": 2,
        "2026-08-06": 1,
    }


def test_record_selection_retains_latest_90_utc_days():
    current = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
    oldest_kept = (current.date() - timedelta(days=89)).isoformat()
    expired = (current.date() - timedelta(days=90)).isoformat()
    state = State(
        accounts={
            "alice@example.com": AccountState(
                sessions_by_day={oldest_kept: 2, expired: 4}
            )
        }
    )

    record_selection(state, "alice@example.com", now=current.timestamp())

    history = state.for_account("alice@example.com").sessions_by_day
    assert history[oldest_kept] == 2
    assert history[current.date().isoformat()] == 1
    assert expired not in history


def test_session_history_survives_state_file_round_trip(home: Path):
    state = State(
        accounts={
            "alice@example.com": AccountState(
                local_uses=3,
                sessions_by_day={"2026-08-05": 2, "2026-08-06": 1},
            )
        }
    )

    save_state(state, home)
    loaded = load_state(home)

    assert loaded.version == 2
    assert loaded.for_account("alice@example.com").sessions_by_day == {
        "2026-08-05": 2,
        "2026-08-06": 1,
    }


def test_state_loader_ignores_invalid_daily_history_entries():
    state = state_from_dict(
        {
            "version": 2,
            "accounts": {
                "alice@example.com": {
                    "sessions_by_day": {
                        "2026-08-06": "2",
                        "not-a-date": 4,
                        "2026-08-05": -1,
                        "2026-08-04": "many",
                    }
                }
            },
        }
    )

    assert state.for_account("alice@example.com").sessions_by_day == {
        "2026-08-06": 2
    }
