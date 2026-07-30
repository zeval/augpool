"""Read/write Augment session.json, env helpers, and portable share blobs."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from augpool import paths


REQUIRED_KEYS = ("accessToken", "tenantURL")
BLOB_VERSION = 2
# Unpadded base64url — shell-safe single token, no quotes needed
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Practical email check (not full RFC)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not e or not _EMAIL_RE.match(e):
        raise ValueError(
            f"invalid email {email!r} (need name@domain.tld)"
        )
    return e


def validate_session(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("session must be a JSON object")
    # Accept tenant_url alias but normalize to tenantURL
    if "tenantURL" not in data and "tenant_url" in data:
        data = {**data, "tenantURL": data["tenant_url"]}
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise ValueError(f"session missing required keys: {', '.join(missing)}")
    token = data.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("session.accessToken must be a non-empty string")
    tenant = data.get("tenantURL")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ValueError("session.tenantURL must be a non-empty string")
    if not tenant.startswith("http://") and not tenant.startswith("https://"):
        raise ValueError(f"session.tenantURL must be an http(s) URL, got {tenant!r}")
    # normalize whitespace on token
    data = {**data, "accessToken": token.strip(), "tenantURL": tenant.strip()}
    return data


def load_session(path: str | Path) -> dict[str, Any]:
    p = paths.expand(path)
    if not p.is_file():
        raise FileNotFoundError(f"session file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"session file is not valid JSON ({p}): {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"session file is not a JSON object: {p}")
    return validate_session(data)


def write_json_atomic(path: Path, data: Any, mode: int = 0o600) -> None:
    path = paths.expand(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_session(path: str | Path, data: dict[str, Any]) -> None:
    write_json_atomic(paths.expand(path), validate_session(data), mode=0o600)


def backup_and_use(
    session: dict[str, Any],
    target: str | Path,
    *,
    root: Path | None = None,
) -> Path | None:
    """Write session to target after backing up existing file. Returns backup path or None."""
    root = paths.ensure_layout(root)
    target_path = paths.expand(target)
    backup_path = paths.session_backup_path(root)
    had_existing = target_path.is_file()
    if had_existing:
        backup_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copy2(target_path, backup_path)
        os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)
    write_session(target_path, session)
    return backup_path if had_existing else None


def restore_backup(
    target: str | Path,
    *,
    root: Path | None = None,
) -> Path:
    root = paths.ensure_layout(root)
    backup_path = paths.session_backup_path(root)
    if not backup_path.is_file():
        raise FileNotFoundError(f"no backup at {backup_path}")
    target_path = paths.expand(target)
    data = load_session(backup_path)
    write_session(target_path, data)
    return target_path


def session_to_env_value(session: dict[str, Any]) -> str:
    return json.dumps(validate_session(session), separators=(",", ":"), sort_keys=True)


def export_shell_line(session: dict[str, Any]) -> str:
    """Single line safe for: eval "$(augpool export --env)" """
    value = session_to_env_value(session)
    escaped = value.replace("'", "'\"'\"'")
    return f"export AUGMENT_SESSION_AUTH='{escaped}'"


def apply_env(session: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, str]:
    out = dict(os.environ if env is None else env)
    out["AUGMENT_SESSION_AUTH"] = session_to_env_value(session)
    return out


def build_share_envelope(
    *,
    email: str,
    session: dict[str, Any],
    label: str = "",
) -> dict[str, Any]:
    email = normalize_email(email)
    return {
        "v": BLOB_VERSION,
        "email": email,
        "label": (label or email).strip(),
        "session": validate_session(session),
    }


def encode_share_blob(envelope: dict[str, Any]) -> str:
    """Compact JSON → unpadded base64url (one shell token, no quotes)."""
    ver = int(envelope.get("v", 0))
    if ver not in (1, 2):
        raise ValueError(f"unsupported share blob version: {envelope.get('v')}")
    # always re-encode as v2 email-only
    env = build_share_envelope(
        email=str(envelope.get("email") or ""),
        session=envelope.get("session") or {},
        label=str(envelope.get("label") or ""),
    )
    raw = json.dumps(env, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_share_blob(blob: str) -> dict[str, Any]:
    """Decode unpadded base64url share blob → envelope dict (email + session)."""
    s = (blob or "").strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        s = s[1:-1].strip()
    # strip accidental "augpool import " paste
    if s.lower().startswith("augpool "):
        raise ValueError(
            "looks like a full command was pasted; pass only the base64 token"
        )
    if s.upper().startswith("AUGPOOL1.") or s.upper().startswith("AUGPOOL2."):
        s = s.split(".", 1)[1]
    if not s or not _B64URL_RE.match(s):
        preview = (s[:40] + "…") if len(s) > 40 else s
        raise ValueError(
            f"invalid share blob (expected unpadded base64url token), got {preview!r}"
        )
    if len(s) < 20:
        raise ValueError("share blob too short to be valid")
    pad = "=" * (-len(s) % 4)
    try:
        raw = base64.urlsafe_b64decode(s + pad)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"invalid share blob (decode failed): {e}") from e
    if not isinstance(data, dict):
        raise ValueError("share blob JSON must be an object")
    ver = int(data.get("v", 0))
    if ver not in (1, 2):
        raise ValueError(f"unsupported share blob version: {ver}")

    # v1 had id+email; v2 is email-only. Prefer email always.
    email_raw = data.get("email") or ""
    if not email_raw and data.get("id") and "@" in str(data.get("id")):
        email_raw = data["id"]
    try:
        email = normalize_email(str(email_raw))
    except ValueError as e:
        # v1 used local-part ids — require email field
        raise ValueError(
            f"share blob missing valid email ({e}). "
            "Re-export with a newer augpool (export embeds full email)."
        ) from e

    session = validate_session(data.get("session") or {})
    label = str(data.get("label") or email)
    return {
        "v": BLOB_VERSION,
        "email": email,
        "label": label,
        "session": session,
    }


def read_share_blob_arg(arg: str) -> str:
    """Resolve import source: stdin, file path, or raw blob string.

    Never call Path.is_file() on a long base64url token — Linux returns
    ENAMETOOLONG which broke: augpool import eyJ...
    """
    if not arg or not str(arg).strip():
        raise ValueError("empty blob argument")
    s = str(arg).strip()
    if s == "-":
        import sys

        text = sys.stdin.read()
        if not text.strip():
            raise ValueError("stdin is empty (expected share blob)")
        for line in text.splitlines():
            if line.strip():
                return line.strip()
        raise ValueError("stdin has no non-empty line")

    # Fast path: looks like a share blob (unpadded base64url). Never touch Path.
    candidate = s
    if (candidate.startswith("'") and candidate.endswith("'")) or (
        candidate.startswith('"') and candidate.endswith('"')
    ):
        candidate = candidate[1:-1].strip()
    upper = candidate.upper()
    if upper.startswith("AUGPOOL1.") or upper.startswith("AUGPOOL2."):
        candidate = candidate.split(".", 1)[1]
    if (
        _B64URL_RE.match(candidate)
        and len(candidate) >= 20
        and "/" not in s
        and not s.startswith(".")
    ):
        return candidate

    # Path path: only if path-like AND short enough for the FS (NAME_MAX ~255).
    if len(s) > 240:
        raise ValueError(
            "argument looks too long to be a file path and is not valid base64url; "
            "pass the export token as-is (no quotes)"
        )
    looks_like_path = (
        "/" in s
        or s.startswith(".")
        or s.startswith("~")
        or s.endswith(".txt")
        or s.endswith(".b64")
        or s.endswith(".augpool")
    )
    if not looks_like_path:
        return s

    try:
        p = Path(s).expanduser()
        is_file = p.is_file()
    except OSError as e:
        raise ValueError(
            f"cannot read blob path {s!r}: {e}. "
            "If this was an export token, it must be pure base64url."
        ) from e
    if not is_file:
        raise FileNotFoundError(f"blob file not found: {p}")
    lines = [
        ln.strip()
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    if not lines:
        raise ValueError(f"blob file is empty: {p}")
    return lines[0]
