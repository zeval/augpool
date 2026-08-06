"""Versioned, credential-free machine reporting for Augpool integrations."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from augpool import paths
from augpool.analytics import cache_is_fresh, get_usage_result
from augpool.pool import load_pool
from augpool.select import rank_accounts
from augpool.state import locked_state


SCHEMA_VERSION = 1


def _timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def collect_stats(
    root: Path | None = None,
    *,
    force_refresh: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Collect the versioned dashboard snapshot without exposing credentials."""
    now = time.time() if now is None else now
    root = paths.ensure_layout(root or paths.home()).expanduser().resolve()
    pool = load_pool(root)
    usage_result = get_usage_result(
        pool,
        root=root,
        force=force_refresh,
        now=now,
        refresh_if_stale=True,
    )

    with locked_state(root) as state:
        ranked = rank_accounts(pool, state, usage_result.by_id, now=now)

    accounts: list[dict[str, Any]] = []
    ranked_emails: set[str] = set()
    for item in ranked:
        account = item.account
        ranked_emails.add(account.email)
        accounts.append(
            {
                "email": account.email,
                "label": account.label,
                "enabled": account.enabled,
                "weight": account.weight,
                "active": account.email == pool.active_email,
                "credits_consumed": item.credits_consumed,
                "score": item.score,
                "local_uses": item.local_uses,
                "source": item.source,
                "last_selected_at": item.last_selected_at,
                "in_cooldown": item.in_cooldown,
                "cooldown_until": item.cooldown_until,
            }
        )

    for account in pool.accounts:
        if account.email in ranked_emails:
            continue
        accounts.append(
            {
                "email": account.email,
                "label": account.label,
                "enabled": account.enabled,
                "weight": account.weight,
                "active": account.email == pool.active_email,
                "credits_consumed": None,
                "score": None,
                "local_uses": None,
                "source": None,
                "last_selected_at": None,
                "in_cooldown": False,
                "cooldown_until": None,
            }
        )

    cache = usage_result.cache or {}
    fetched_at_raw = cache.get("fetched_at")
    fetched_at = float(fetched_at_raw) if fetched_at_raw is not None else None
    age_seconds = (
        max(0, int(now - fetched_at)) if fetched_at is not None else None
    )
    errors = (
        usage_result.errors
        if usage_result.refresh_attempted
        else [str(error) for error in (cache.get("errors") or [])]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _timestamp(now),
        "home": str(root),
        "active_email": pool.active_email,
        "strategy": pool.strategy,
        "usage": {
            "fetched_at": fetched_at,
            "age_seconds": age_seconds,
            "ttl_seconds": pool.usage_cache_ttl_seconds,
            "stale": not cache_is_fresh(
                usage_result.cache, pool.usage_cache_ttl_seconds, now=now
            ),
            "start_date": cache.get("start_date"),
            "end_date": cache.get("end_date"),
            "refresh_attempted": usage_result.refresh_attempted,
            "refresh_succeeded": usage_result.refresh_succeeded,
            "errors": errors,
            "fetches_ok": int(cache.get("fetches_ok") or 0),
            "tenants_queried": int(cache.get("tenants_queried") or 0),
        },
        "accounts": accounts,
    }
