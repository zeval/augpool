"""Filesystem layout under ~/.augpool (or AUGPOOL_HOME)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    override = os.environ.get("AUGPOOL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".augpool").resolve()


def pool_path(root: Path | None = None) -> Path:
    return (root or home()) / "pool.json"


def state_path(root: Path | None = None) -> Path:
    return (root or home()) / "state.json"


def cache_dir(root: Path | None = None) -> Path:
    return (root or home()) / "cache"


def usage_cache_path(root: Path | None = None) -> Path:
    return cache_dir(root) / "usage.json"


def creds_dir(root: Path | None = None) -> Path:
    return (root or home()) / "creds"


def backups_dir(root: Path | None = None) -> Path:
    return (root or home()) / "backups"


def session_backup_path(root: Path | None = None) -> Path:
    return backups_dir(root) / "session.json.bak"


def ensure_layout(root: Path | None = None) -> Path:
    root = root or home()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    for d in (cache_dir(root), creds_dir(root), backups_dir(root)):
        d.mkdir(parents=True, mode=0o700, exist_ok=True)
    return root


def expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
