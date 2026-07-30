"""Augment Analytics API client + on-disk usage cache."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from augpool import paths
from augpool.pool import Account, Pool, resolve_access_token
from augpool.session_io import write_json_atomic

HttpGetter = Callable[[str, dict[str, str]], dict[str, Any]]


def default_date_window(today: date | None = None) -> tuple[str, str]:
    """Last 30 days UTC inclusive of today."""
    today = today or date.today()
    start = today - timedelta(days=29)
    return start.isoformat(), today.isoformat()


def load_usage_cache(root: Path | None = None) -> dict[str, Any] | None:
    p = paths.usage_cache_path(root)
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_usage_cache(data: dict[str, Any], root: Path | None = None) -> None:
    root = paths.ensure_layout(root)
    write_json_atomic(paths.usage_cache_path(root), data, mode=0o600)


def cache_is_fresh(cache: dict[str, Any] | None, ttl_seconds: int, now: float | None = None) -> bool:
    if not cache:
        return False
    fetched = cache.get("fetched_at")
    if fetched is None:
        return False
    now = time.time() if now is None else now
    return (now - float(fetched)) < ttl_seconds


def http_get_json(url: str, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"analytics HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"analytics network error: {e}") from e


def fetch_credit_usage(
    *,
    base_url: str,
    token: str,
    start_date: str,
    end_date: str,
    http_get: HttpGetter | None = None,
) -> list[dict[str, Any]]:
    """Paginate GET /analytics/v0/credit-usage-by-user; return all records."""
    http_get = http_get or http_get_json
    base = base_url.rstrip("/")
    records: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {
            "start_date": start_date,
            "end_date": end_date,
            "page_size": "500",
        }
        if cursor:
            params["cursor"] = cursor
        url = f"{base}/analytics/v0/credit-usage-by-user?{urllib.parse.urlencode(params)}"
        payload = http_get(url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
        batch = payload.get("records") or []
        records.extend(batch)
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor") or ""
        if not cursor:
            break
    return records


def _identity_keys(account: Account) -> set[str]:
    return {account.email, account.email.lower()}


def map_records_to_accounts(
    records: list[dict[str, Any]],
    accounts: list[Account],
) -> dict[str, float]:
    """Sum credits_consumed per account email."""
    index: dict[str, str] = {}
    for a in accounts:
        index[a.email.lower()] = a.email

    totals: dict[str, float] = {a.email: 0.0 for a in accounts}
    for rec in records:
        email = (rec.get("user_email") or "").lower()
        sa = (rec.get("service_account_name") or "").lower()
        account_email = index.get(email) or index.get(sa)
        if not account_email:
            continue
        credits = rec.get("credits_consumed", 0)
        try:
            totals[account_email] = totals.get(account_email, 0.0) + float(credits)
        except (TypeError, ValueError):
            continue
    return totals


def _tenant_key(account: Account) -> str:
    """Normalize tenantURL for grouping; empty if unknown."""
    return (account.tenant_url or "").strip().rstrip("/").lower()


def _accounts_covered(mapped: dict[str, float], enabled: list[Account]) -> bool:
    """True when every enabled account id is present in the mapped totals."""
    return all(a.id in mapped for a in enabled)


def _fetch_one_tenant(
    *,
    base_url: str,
    start: str,
    end: str,
    tenant: str,
    token_accounts: list[tuple[str, list[Account]]],
    enabled: list[Account],
    http_get: HttpGetter | None,
) -> tuple[dict[str, float], list[str], int]:
    """
    Try tokens for one tenant until one succeeds.

    One org-level response maps credits for every pool email (0 if absent).
    Stops after the first working token — no need to hit the API 3x for one org.
    """
    errors: list[str] = []
    for token, group in token_accounts:
        try:
            records = fetch_credit_usage(
                base_url=base_url,
                token=token,
                start_date=start,
                end_date=end,
                http_get=http_get,
            )
            mapped = map_records_to_accounts(records, enabled)
            return mapped, errors, 1
        except Exception as e:  # noqa: BLE001
            ids = ",".join(a.id for a in group)
            label = tenant or "unknown-tenant"
            errors.append(f"{label}/{ids}: {e}")
    return {}, errors, 0


def refresh_usage(
    pool: Pool,
    *,
    root: Path | None = None,
    http_get: HttpGetter | None = None,
    now: float | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Pull analytics for all enabled accounts.

    Auth = session accessToken. Same-org accounts share one API call (group by
    tenantURL; first working token wins). Distinct tenants are fetched in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = time.time() if now is None else now
    start, end = default_date_window(today)
    enabled = pool.enabled_accounts()
    if not enabled:
        cache = {
            "fetched_at": now,
            "start_date": start,
            "end_date": end,
            "by_id": {},
            "errors": ["no enabled accounts"],
            "fetches_ok": 0,
        }
        save_usage_cache(cache, root)
        return cache

    # tenant -> token -> accounts
    by_tenant: dict[str, dict[str, list[Account]]] = {}
    missing: list[str] = []
    for a in enabled:
        token = resolve_access_token(a, root)
        if not token:
            missing.append(a.id)
            continue
        tenant = _tenant_key(a)
        by_tenant.setdefault(tenant, {}).setdefault(token, []).append(a)

    by_id: dict[str, float] = {a.email: 0.0 for a in enabled}
    errors: list[str] = []
    if missing:
        errors.append(f"no session accessToken for: {', '.join(missing)}")

    tenant_jobs: list[tuple[str, list[tuple[str, list[Account]]]]] = [
        (tenant, list(token_map.items())) for tenant, token_map in by_tenant.items()
    ]

    def _job(
        item: tuple[str, list[tuple[str, list[Account]]]],
    ) -> tuple[dict[str, float], list[str], int]:
        tenant, token_accounts = item
        return _fetch_one_tenant(
            base_url=pool.analytics_base_url,
            start=start,
            end=end,
            tenant=tenant,
            token_accounts=token_accounts,
            enabled=enabled,
            http_get=http_get,
        )

    fetches_ok = 0
    workers = min(8, max(1, len(tenant_jobs)))
    results: list[tuple[dict[str, float], list[str], int]] = []
    if workers <= 1:
        results = [_job(j) for j in tenant_jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_job, j) for j in tenant_jobs]
            for fut in as_completed(futs):
                results.append(fut.result())

    for mapped, job_errors, ok in results:
        errors.extend(job_errors)
        fetches_ok += ok
        for aid, credits in mapped.items():
            by_id[aid] = max(by_id.get(aid, 0.0), credits)

    cache = {
        "fetched_at": now,
        "start_date": start,
        "end_date": end,
        "by_id": by_id,
        "errors": errors,
        "fetches_ok": fetches_ok,
        "tenants_queried": len(tenant_jobs),
    }
    save_usage_cache(cache, root)
    return cache


def _cache_by_id(cache: dict[str, Any] | None) -> dict[str, float] | None:
    if not cache or cache.get("by_id") is None:
        return None
    return {str(k): float(v) for k, v in (cache.get("by_id") or {}).items()}


def _refresh_had_usable_data(cache: dict[str, Any], had_token: bool) -> bool:
    """True when a refresh should replace the previous cache as authoritative."""
    if not had_token:
        return False
    # At least one token-level fetch succeeded.
    if int(cache.get("fetches_ok") or 0) < 1:
        return False
    return cache.get("by_id") is not None


def get_fresh_usage(
    pool: Pool,
    *,
    root: Path | None = None,
    force: bool = False,
    http_get: HttpGetter | None = None,
    now: float | None = None,
    refresh_if_stale: bool = True,
) -> dict[str, float] | None:
    """
    Return usage map for ranking.

    When refresh_if_stale is True (default), an expired or missing cache triggers
    an Analytics pull. On pull failure, falls back to the previous cache if any.
    """
    now = time.time() if now is None else now
    cache = load_usage_cache(root)
    if not force and cache_is_fresh(cache, pool.usage_cache_ttl_seconds, now=now):
        return _cache_by_id(cache)

    if not refresh_if_stale and not force:
        return _cache_by_id(cache)

    # Stale/missing (or force): pull now.
    had_token = any(resolve_access_token(a, root) for a in pool.enabled_accounts())

    stale = _cache_by_id(cache)
    try:
        new_cache = refresh_usage(pool, root=root, http_get=http_get, now=now)
    except Exception:
        return stale

    if _refresh_had_usable_data(new_cache, had_token):
        return _cache_by_id(new_cache)

    # Failed/no-token pull: restore previous on-disk cache so a blip doesn't
    # zero out ranks, and ranking still sees last known credits.
    if cache is not None and stale is not None:
        save_usage_cache(cache, root)
        return stale
    return _cache_by_id(new_cache)
