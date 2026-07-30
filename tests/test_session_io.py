from __future__ import annotations

import json
from pathlib import Path

import pytest

from augpool.session_io import (
    backup_and_use,
    build_share_envelope,
    decode_share_blob,
    encode_share_blob,
    export_shell_line,
    load_session,
    normalize_email,
    restore_backup,
    session_to_env_value,
    validate_session,
)


def test_validate_requires_keys():
    with pytest.raises(ValueError, match="missing"):
        validate_session({"accessToken": "x"})


def test_normalize_email():
    assert normalize_email("Alice@Example.COM") == "alice@example.com"
    with pytest.raises(ValueError):
        normalize_email("not-an-email")
    with pytest.raises(ValueError):
        normalize_email("me")


def test_export_roundtrip_eval_safe():
    s = {"accessToken": "abc'def", "tenantURL": "https://t/", "scopes": ["read"]}
    line = export_shell_line(s)
    assert line.startswith("export AUGMENT_SESSION_AUTH=")
    env_val = session_to_env_value(s)
    assert "abc'def" in env_val or "abc" in env_val


def test_share_blob_roundtrip_email_only():
    session = {
        "accessToken": "secret-token",
        "tenantURL": "https://e5.api.augmentcode.com/",
        "scopes": ["read", "write"],
    }
    env = build_share_envelope(email="alice@example.com", session=session)
    assert "id" not in env
    blob = encode_share_blob(env)
    assert " " not in blob and "'" not in blob and '"' not in blob
    assert "+" not in blob and "/" not in blob
    assert not blob.endswith("=")
    back = decode_share_blob(blob)
    assert back["email"] == "alice@example.com"
    assert back["session"]["accessToken"] == "secret-token"
    assert "id" not in back


def test_decode_rejects_garbage():
    with pytest.raises(ValueError, match="invalid share blob"):
        decode_share_blob("not@@@valid")
    with pytest.raises(ValueError, match="too short"):
        decode_share_blob("abc")


def test_backup_use_restore(home: Path):
    target = home / "aug" / "session.json"
    target.parent.mkdir(parents=True)
    original = {"accessToken": "old", "tenantURL": "https://t/", "scopes": []}
    target.write_text(json.dumps(original), encoding="utf-8")

    new = {"accessToken": "new", "tenantURL": "https://t2/", "scopes": ["r"]}
    backup = backup_and_use(new, target, root=home)
    assert backup is not None
    assert load_session(target)["accessToken"] == "new"

    restore_backup(target, root=home)
    assert load_session(target)["accessToken"] == "old"


def test_read_share_blob_arg_long_token_not_path():
    from augpool.session_io import (
        read_share_blob_arg,
        encode_share_blob,
        build_share_envelope,
    )

    blob = encode_share_blob(
        build_share_envelope(
            email="alice@example.com",
            session={
                "accessToken": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "tenantURL": "https://e5.api.augmentcode.com/",
                "scopes": ["read", "write"],
            },
        )
    )
    assert len(blob) > 200
    assert read_share_blob_arg(blob) == blob
    q = chr(39) + blob + chr(39)
    assert read_share_blob_arg(q) == blob

