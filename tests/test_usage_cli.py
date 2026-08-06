from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from augpool.analytics import save_usage_cache
from augpool.cli import _normalize_argv, main
from augpool.state import load_state, save_state


def _seed_usage(home: Path) -> None:
    now = time.time()
    today = datetime.now(timezone.utc).date()
    save_usage_cache(
        {
            "fetched_at": now,
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "by_id": {"alice@acme.com": 4_200.0, "bob@acme.com": 1_800.0},
            "errors": [],
            "fetches_ok": 1,
        },
        home,
    )
    state = load_state(home)
    state.for_account("alice@acme.com").local_uses = 5
    state.for_account("bob@acme.com").local_uses = 2
    save_state(state, home)


def test_usage_command_prints_account_dashboard(two_account_pool, home: Path, capsys):
    _seed_usage(home)

    assert main(["--home", str(home), "usage", "--no-color"]) == 0

    output = capsys.readouterr().out
    assert "augpool usage" in output
    assert "2 accounts · 6k credits · 7 sessions" in output
    assert "alice@acme.com" in output
    assert "bob@acme.com" in output
    assert "\x1b[" not in output


def test_usage_command_prints_structured_json(two_account_pool, home: Path, capsys):
    _seed_usage(home)

    assert main(["--home", str(home), "usage", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["credits_consumed"] == 6_000.0
    assert payload["totals"]["local_sessions"] == 7
    assert payload["accounts"][0]["email"] == "bob@acme.com"


def test_usage_is_recognized_as_augpool_subcommand():
    assert _normalize_argv(["usage"]) == ["usage"]


def test_usage_help_explains_report_options(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["usage", "--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "emit structured JSON" in output
    assert "refresh Analytics before rendering" in output
    assert "disable ANSI color" in output
