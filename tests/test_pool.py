from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from augpool.pool import load_pool, update_account


def test_update_account_persists_fields_and_clears_disabled_active(
    home: Path, two_account_pool
):
    two_account_pool.active_email = "alice@acme.com"

    account = update_account(
        two_account_pool,
        "alice@acme.com",
        enabled=False,
        weight=2.5,
        root=home,
    )

    assert account.enabled is False
    assert account.weight == 2.5
    assert two_account_pool.active_email is None

    reloaded = load_pool(home)
    assert reloaded.get("alice@acme.com").enabled is False
    assert reloaded.get("alice@acme.com").weight == 2.5
    assert reloaded.active_email is None

    raw = json.loads((home / "pool.json").read_text(encoding="utf-8"))
    assert raw["active_email"] is None


@pytest.mark.parametrize("weight", [0, -1, math.inf, -math.inf, math.nan])
def test_update_account_rejects_non_positive_or_non_finite_weight(
    home: Path, two_account_pool, weight: float
):
    with pytest.raises(ValueError, match="finite number > 0"):
        update_account(two_account_pool, "alice@acme.com", weight=weight, root=home)


def test_update_account_requires_a_change(home: Path, two_account_pool):
    with pytest.raises(ValueError, match="enabled or weight"):
        update_account(two_account_pool, "alice@acme.com", root=home)
