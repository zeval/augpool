"""Mutable runtime state (local counters, cooldowns) with file locking."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from augpool import paths

try:
    import fcntl
except ImportError:  # pragma: no cover - non-posix
    fcntl = None  # type: ignore


@dataclass
class AccountState:
    local_uses: int = 0
    last_selected_at: float | None = None
    cooldown_until: float | None = None
    last_error: str | None = None


@dataclass
class State:
    version: int = 1
    accounts: dict[str, AccountState] = field(default_factory=dict)

    def for_account(self, account_id: str) -> AccountState:
        if account_id not in self.accounts:
            self.accounts[account_id] = AccountState()
        return self.accounts[account_id]


def _account_from_dict(raw: dict[str, Any]) -> AccountState:
    return AccountState(
        local_uses=int(raw.get("local_uses", 0)),
        last_selected_at=raw.get("last_selected_at"),
        cooldown_until=raw.get("cooldown_until"),
        last_error=raw.get("last_error"),
    )


def state_from_dict(raw: dict[str, Any]) -> State:
    accounts = {
        k: _account_from_dict(v) for k, v in (raw.get("accounts") or {}).items()
    }
    return State(version=int(raw.get("version", 1)), accounts=accounts)


def empty_state() -> State:
    return State()


def load_state(root: Path | None = None) -> State:
    root = paths.ensure_layout(root)
    p = paths.state_path(root)
    if not p.exists():
        st = empty_state()
        save_state(st, root)
        return st
    with p.open("r", encoding="utf-8") as f:
        return state_from_dict(json.load(f))


def save_state(state: State, root: Path | None = None) -> None:
    root = paths.ensure_layout(root)
    payload = {
        "version": state.version,
        "accounts": {k: asdict(v) for k, v in state.accounts.items()},
    }
    path = paths.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


@contextmanager
def locked_state(root: Path | None = None) -> Iterator[State]:
    """Exclusive lock around load-modify-save of state.json."""
    root = paths.ensure_layout(root)
    lock_path = root / ".state.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_f:
        if fcntl is not None:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(root)
            yield state
            save_state(state, root)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def record_selection(state: State, account_id: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    ac = state.for_account(account_id)
    ac.local_uses += 1
    ac.last_selected_at = now
    ac.last_error = None


def set_cooldown(
    state: State,
    account_id: str,
    seconds: float,
    *,
    error: str | None = None,
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    ac = state.for_account(account_id)
    ac.cooldown_until = now + seconds
    if error:
        ac.last_error = error


def clear_expired_cooldowns(state: State, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for ac in state.accounts.values():
        if ac.cooldown_until is not None and ac.cooldown_until <= now:
            ac.cooldown_until = None
