"""Least-used account ranking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from augpool.pool import Account, Pool
from augpool.state import AccountState, State, clear_expired_cooldowns


@dataclass(frozen=True)
class RankedAccount:
    account: Account
    credits_consumed: float
    local_uses: int
    last_selected_at: float | None
    score: float
    in_cooldown: bool
    cooldown_until: float | None
    source: str  # "analytics" | "local" | "unknown"


def _usage_for(
    account: Account,
    usage: dict[str, float] | None,
    st: AccountState,
) -> tuple[float, str]:
    if usage is not None and account.email in usage:
        return float(usage[account.email]), "analytics"
    if st.local_uses:
        return float(st.local_uses), "local"
    return 0.0, "unknown"


def rank_accounts(
    pool: Pool,
    state: State,
    usage: dict[str, float] | None = None,
    *,
    now: float | None = None,
    include_cooldown: bool = True,
) -> list[RankedAccount]:
    """Return enabled accounts sorted best-first (lowest score)."""
    now = time.time() if now is None else now
    clear_expired_cooldowns(state, now=now)
    ranked: list[RankedAccount] = []
    for account in pool.enabled_accounts():
        st = state.for_account(account.email)
        credits, source = _usage_for(account, usage, st)
        weight = account.weight if account.weight > 0 else 1.0
        score = credits / weight
        in_cd = st.cooldown_until is not None and st.cooldown_until > now
        ranked.append(
            RankedAccount(
                account=account,
                credits_consumed=credits,
                local_uses=st.local_uses,
                last_selected_at=st.last_selected_at,
                score=score,
                in_cooldown=in_cd,
                cooldown_until=st.cooldown_until,
                source=source,
            )
        )

    def sort_key(r: RankedAccount) -> tuple:
        # Available accounts first, then lowest score, oldest selection, id
        return (
            1 if r.in_cooldown else 0,
            r.score,
            r.last_selected_at if r.last_selected_at is not None else 0.0,
            r.account.email,
        )

    ranked.sort(key=sort_key)
    if not include_cooldown:
        ranked = [r for r in ranked if not r.in_cooldown]
    return ranked


def pick(
    pool: Pool,
    state: State,
    usage: dict[str, float] | None = None,
    *,
    now: float | None = None,
    exclude_ids: Iterable[str] | None = None,
) -> RankedAccount:
    exclude = set(exclude_ids or ())
    ranked = rank_accounts(pool, state, usage, now=now, include_cooldown=True)
    for r in ranked:
        if r.account.email in exclude:
            continue
        if r.in_cooldown:
            continue
        return r
    raise RuntimeError("no available accounts (all disabled, excluded, or in cooldown)")


def load_usage_map(root: Path | None = None) -> dict[str, float] | None:
    """Load cached usage map {email: credits}. None if missing."""
    from augpool.analytics import load_usage_cache

    cache = load_usage_cache(root)
    if cache is None:
        return None
    return dict(cache.get("by_id") or {})
