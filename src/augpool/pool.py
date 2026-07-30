"""Pool registry load/save and account CRUD (email is the only identity)."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from augpool import paths
from augpool.session_io import load_session, normalize_email, validate_session, write_json_atomic


DEFAULT_ANALYTICS_BASE = "https://api.augmentcode.com"
DEFAULT_SESSION_PATH = "~/.augment/session.json"
DEFAULT_TTL = 300  # 5 minutes

# Dropped fields still present in older pool.json files
_LEGACY_ACCOUNT_KEYS = frozenset(
    {"analytics_token_env", "analytics_token_path", "id"}
)
_LEGACY_POOL_KEYS = frozenset(
    {"analytics_token_env", "analytics_token_path", "active_id"}
)


def _filter_kwargs(cls: type, raw: dict[str, Any], drop: frozenset[str]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in allowed and k not in drop}


def creds_filename(email: str) -> str:
    """
    Stable filesystem-safe name for an email.

    alice@example.com → alice_at_example.com.json
    Falls back to sha1 if the result is empty/odd.
    """
    email = normalize_email(email)
    local, _, domain = email.partition("@")
    base = f"{local}_at_{domain}"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    if not safe or len(safe) > 180:
        digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:16]
        safe = f"acct_{digest}"
    return f"{safe}.json"


@dataclass
class Account:
    email: str
    session_path: str
    label: str = ""
    tenant_url: str = ""
    enabled: bool = True
    weight: float = 1.0
    notes: str = ""

    def __post_init__(self) -> None:
        self.email = normalize_email(self.email)
        if not self.label:
            self.label = self.email
        if self.weight <= 0:
            raise ValueError(f"account {self.email}: weight must be > 0")

    # Back-compat alias so older call sites using .id still work during transition
    @property
    def id(self) -> str:
        return self.email


@dataclass
class Pool:
    version: int = 2
    active_email: str | None = None
    accounts: list[Account] = field(default_factory=list)
    strategy: str = "least_used"
    usage_cache_ttl_seconds: int = DEFAULT_TTL
    analytics_base_url: str = DEFAULT_ANALYTICS_BASE
    augment_session_path: str = DEFAULT_SESSION_PATH

    def get(self, email: str) -> Account:
        return find_account(self, email)

    def enabled_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.enabled]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_pool() -> Pool:
    return Pool()


def load_pool(root: Path | None = None) -> Pool:
    root = paths.ensure_layout(root)
    p = paths.pool_path(root)
    if not p.exists():
        pool = empty_pool()
        save_pool(pool, root)
        return pool
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"pool.json must be a JSON object: {p}")
    return pool_from_dict(raw)


def _account_from_raw(raw: dict[str, Any]) -> Account:
    data = dict(raw)
    # Migrate legacy id-based entries: email is required; if missing, fail clearly
    email = data.get("email") or data.get("id")
    if not email or "@" not in str(email):
        raise ValueError(
            f"pool account missing valid email (got {raw!r}). "
            "Re-add with: augpool import --self --email you@company.com"
        )
    data["email"] = normalize_email(str(email))
    # Drop legacy id so Account() doesn't reject unknown field after filter
    data.pop("id", None)
    kwargs = _filter_kwargs(Account, data, _LEGACY_ACCOUNT_KEYS)
    # Ensure session_path present
    if not kwargs.get("session_path"):
        kwargs["session_path"] = f"creds/{creds_filename(kwargs['email'])}"
    return Account(**kwargs)


def pool_from_dict(raw: dict[str, Any]) -> Pool:
    accounts = [_account_from_raw(a) for a in raw.get("accounts", [])]
    # migrate active_id → active_email
    active = raw.get("active_email")
    if not active and raw.get("active_id"):
        legacy = str(raw["active_id"]).strip().lower()
        # try match by old id field stored... we only have emails now; match email or local
        for a in accounts:
            if a.email == legacy or a.email.split("@")[0] == legacy:
                active = a.email
                break
            # common legacy: id was "me" — leave unset
        if legacy == "me":
            active = None
    pool_kwargs = _filter_kwargs(Pool, raw, _LEGACY_POOL_KEYS)
    pool_kwargs["accounts"] = accounts
    pool_kwargs["active_email"] = normalize_email(active) if active else None
    pool_kwargs.setdefault("version", 2)
    pool_kwargs.setdefault("strategy", "least_used")
    pool_kwargs.setdefault("usage_cache_ttl_seconds", DEFAULT_TTL)
    pool_kwargs.setdefault("analytics_base_url", DEFAULT_ANALYTICS_BASE)
    pool_kwargs.setdefault("augment_session_path", DEFAULT_SESSION_PATH)
    pool_kwargs["usage_cache_ttl_seconds"] = int(pool_kwargs["usage_cache_ttl_seconds"])
    pool_kwargs["version"] = int(pool_kwargs.get("version") or 2)
    # de-dupe by email (last wins)
    seen: dict[str, Account] = {}
    for a in accounts:
        seen[a.email] = a
    pool_kwargs["accounts"] = list(seen.values())
    return Pool(**pool_kwargs)


def save_pool(pool: Pool, root: Path | None = None) -> None:
    root = paths.ensure_layout(root)
    write_json_atomic(paths.pool_path(root), pool.to_dict(), mode=0o600)


def resolve_session_file(account: Account, root: Path | None = None) -> Path:
    root = root or paths.home()
    p = Path(account.session_path)
    if not p.is_absolute():
        p = root / p
    return p.expanduser().resolve()


def find_account(pool: Pool, who: str) -> Account:
    """Resolve by full email (preferred) or unique local-part."""
    key = (who or "").strip().lower()
    if not key:
        raise KeyError("empty account reference")
    # exact email
    for a in pool.accounts:
        if a.email == key:
            return a
    # unique local-part
    if "@" not in key:
        matches = [a for a in pool.accounts if a.email.split("@", 1)[0] == key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            emails = ", ".join(m.email for m in matches)
            raise KeyError(
                f"ambiguous local-part {key!r} matches: {emails}. Use full email."
            )
    known = ", ".join(a.email for a in pool.accounts) or "(pool empty)"
    raise KeyError(f"unknown account {who!r}. known: {known}")


def add_account(
    pool: Pool,
    *,
    email: str,
    session: dict[str, Any] | None = None,
    session_source: str | Path | None = None,
    root: Path | None = None,
    label: str = "",
    weight: float = 1.0,
    notes: str = "",
    force: bool = False,
) -> Account:
    """Add or replace (force=True) an account keyed only by email."""
    root = paths.ensure_layout(root)
    try:
        email = normalize_email(email)
    except ValueError as e:
        raise ValueError(f"cannot add account: {e}") from e

    existing = next((a for a in pool.accounts if a.email == email), None)
    if existing is not None and not force:
        raise ValueError(
            f"account already exists: {email} (use --force or import --self to replace)"
        )

    if session is not None:
        raw = validate_session(session)
    elif session_source is not None:
        if str(session_source) == "-":
            import sys

            try:
                payload = json.load(sys.stdin)
            except json.JSONDecodeError as e:
                raise ValueError(f"stdin is not valid JSON: {e}") from e
            raw = validate_session(payload)
        else:
            src = paths.expand(session_source)
            if not src.is_file():
                raise FileNotFoundError(f"session file not found: {src}")
            raw = load_session(src)
    else:
        raise ValueError("session or session_source required")

    dest_rel = f"creds/{creds_filename(email)}"
    dest = root / dest_rel
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    write_json_atomic(dest, raw, mode=0o600)

    # remove old creds file if path changed on replace
    if existing is not None:
        old = resolve_session_file(existing, root)
        pool.accounts = [a for a in pool.accounts if a.email != email]

    account = Account(
        email=email,
        session_path=dest_rel,
        label=label.strip() or email,
        tenant_url=str(raw.get("tenantURL") or raw.get("tenant_url") or ""),
        weight=weight,
        notes=notes,
    )
    pool.accounts.append(account)
    save_pool(pool, root)

    if existing is not None:
        try:
            new_path = resolve_session_file(account, root)
            if old.exists() and old != new_path and old.is_relative_to(paths.creds_dir(root)):
                old.unlink(missing_ok=True)
        except OSError:
            pass
    return account


def remove_account(pool: Pool, who: str, root: Path | None = None) -> Account:
    root = paths.ensure_layout(root)
    account = find_account(pool, who)
    session_file = resolve_session_file(account, root)
    pool.accounts = [a for a in pool.accounts if a.email != account.email]
    if pool.active_email == account.email:
        pool.active_email = None
    save_pool(pool, root)
    if session_file.exists() and session_file.is_relative_to(paths.creds_dir(root)):
        session_file.unlink(missing_ok=True)
    return account


def set_active(pool: Pool, email: str | None, root: Path | None = None) -> None:
    if email is not None:
        email = find_account(pool, email).email
    pool.active_email = email
    save_pool(pool, root)


def resolve_access_token(account: Account, root: Path | None = None) -> str | None:
    """Bearer token for Analytics = session accessToken for this account."""
    try:
        session = load_session(resolve_session_file(account, root))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    token = session.get("accessToken")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def clone_pool(pool: Pool) -> Pool:
    return pool_from_dict(deepcopy(pool.to_dict()))
