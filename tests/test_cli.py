from __future__ import annotations

import json
import re
from pathlib import Path

from augpool.cli import main
from augpool.session_io import decode_share_blob, write_json_atomic


def test_add_list_use_restore(home: Path, capsys):
    session = home / "in.json"
    write_json_atomic(
        session,
        {"accessToken": "t1", "tenantURL": "https://e5.api.augmentcode.com/", "scopes": []},
    )
    assert (
        main(
            [
                "--home", str(home), "add",
                "--email", "me@x.com",
                "--session", str(session),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--home", str(home), "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["email"] == "me@x.com"

    s2 = home / "in2.json"
    write_json_atomic(
        s2,
        {"accessToken": "t2", "tenantURL": "https://e5.api.augmentcode.com/", "scopes": []},
    )
    assert main([
        "--home", str(home), "add", "--email", "o@x.com", "--session", str(s2)
    ]) == 0

    target_parent = home / "fake-augment"
    pool_path = home / "pool.json"
    pool = json.loads(pool_path.read_text())
    pool["augment_session_path"] = str(target_parent / "session.json")
    pool_path.write_text(json.dumps(pool), encoding="utf-8")

    target_parent.mkdir(parents=True)
    (target_parent / "session.json").write_text(
        json.dumps({"accessToken": "old", "tenantURL": "https://t/", "scopes": []}),
        encoding="utf-8",
    )

    assert main(["--home", str(home), "use", "me@x.com"]) == 0
    used = json.loads((target_parent / "session.json").read_text())
    assert used["accessToken"] == "t1"

    assert main(["--home", str(home), "restore"]) == 0
    restored = json.loads((target_parent / "session.json").read_text())
    assert restored["accessToken"] == "old"


def test_export_import_blob_roundtrip(home: Path, capsys, tmp_path: Path):
    session = home / "in.json"
    write_json_atomic(
        session,
        {
            "accessToken": "tok-secret",
            "tenantURL": "https://e5.api.augmentcode.com/",
            "scopes": ["r"],
        },
    )
    main([
        "--home", str(home), "add",
        "--email", "alice@example.com",
        "--session", str(session),
    ])
    capsys.readouterr()

    assert main(["--home", str(home), "export", "alice@example.com"]) == 0
    blob = capsys.readouterr().out.strip()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", blob)
    env = decode_share_blob(blob)
    assert env["email"] == "alice@example.com"
    assert "id" not in env
    assert env["session"]["accessToken"] == "tok-secret"

    other = tmp_path / "other-home"
    other.mkdir()
    assert main(["--home", str(other), "import", blob]) == 0
    out = capsys.readouterr().out
    assert "alice@example.com" in out
    # creds file named from email
    creds = list((other / "creds").glob("*.json"))
    assert len(creds) == 1
    assert json.loads(creds[0].read_text())["accessToken"] == "tok-secret"


def test_export_env_flag(home: Path, capsys):
    session = home / "in.json"
    write_json_atomic(
        session,
        {"accessToken": "tok", "tenantURL": "https://e5.api.augmentcode.com/", "scopes": ["r"]},
    )
    main(["--home", str(home), "add", "--email", "me@x.com", "--session", str(session)])
    capsys.readouterr()
    assert main(["--home", str(home), "export", "--env", "me@x.com"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("export AUGMENT_SESSION_AUTH=")
    assert "tok" in out


def test_import_self_and_export_self(home: Path, capsys):
    aug = home / "fake-augment"
    aug.mkdir()
    sess = {
        "accessToken": "live-token",
        "tenantURL": "https://e5.api.augmentcode.com/",
        "scopes": ["read"],
    }
    (aug / "session.json").write_text(json.dumps(sess), encoding="utf-8")
    pool_path = home / "pool.json"
    assert main(["--home", str(home), "list"]) == 0
    capsys.readouterr()
    pool = json.loads(pool_path.read_text())
    pool["augment_session_path"] = str(aug / "session.json")
    pool_path.write_text(json.dumps(pool), encoding="utf-8")

    assert main([
        "--home", str(home), "import", "--self",
        "--email", "alice@example.com",
    ]) == 0
    out = capsys.readouterr().out
    assert "alice@example.com" in out

    # refresh token
    sess2 = {**sess, "accessToken": "live-token-2"}
    (aug / "session.json").write_text(json.dumps(sess2), encoding="utf-8")
    assert main([
        "--home", str(home), "import", "--self",
        "--email", "alice@example.com",
    ]) == 0
    capsys.readouterr()

    # mark active then export --self
    assert main(["--home", str(home), "use", "alice@example.com"]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "export", "--self"]) == 0
    blob = capsys.readouterr().out.strip()
    env = decode_share_blob(blob)
    assert env["email"] == "alice@example.com"
    assert env["session"]["accessToken"] == "live-token-2"
    assert "id" not in env


def test_normalize_argv_defaults_to_auggie():
    from augpool.cli import _normalize_argv

    assert _normalize_argv([]) == ["run", "--", "auggie"]
    assert _normalize_argv(["-p", "-q", "hi"]) == [
        "run", "--", "auggie", "-p", "-q", "hi"
    ]
    assert _normalize_argv(["--email", "a@b.com", "--acp"]) == [
        "run", "--email", "a@b.com", "--", "auggie", "--acp"
    ]
    assert _normalize_argv(["--home", "/tmp/x", "list"]) == [
        "--home", "/tmp/x", "list"
    ]
    assert _normalize_argv(["run", "--", "auggie", "-p", "x"]) == [
        "run", "--", "auggie", "-p", "x"
    ]
    # already starts with auggie — do not double
    assert _normalize_argv(["auggie", "-p", "x"]) == [
        "run", "--", "auggie", "-p", "x"
    ]
    assert _normalize_argv(["--help"]) == ["--help"]
    assert _normalize_argv(["--version"]) == ["--version"]


def test_export_skips_analytics_when_email_given(home: Path, capsys, monkeypatch):
    from augpool import cli as cli_mod
    from augpool.session_io import write_json_atomic

    session = home / "in.json"
    write_json_atomic(
        session,
        {
            "accessToken": "tok",
            "tenantURL": "https://e5.api.augmentcode.com/",
            "scopes": [],
        },
    )
    assert main([
        "--home", str(home), "add",
        "--email", "me@x.com",
        "--session", str(session),
    ]) == 0
    capsys.readouterr()

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("analytics should not run for export with email")

    monkeypatch.setattr(cli_mod, "_usage_map", boom)
    assert main(["--home", str(home), "export", "me@x.com"]) == 0
    out = capsys.readouterr().out.strip()
    assert out  # blob printed
    assert calls["n"] == 0


def test_import_session_file(home: Path, capsys):
    from augpool.session_io import write_json_atomic

    sess = home / "s.json"
    write_json_atomic(
        sess,
        {
            "accessToken": "file-tok",
            "tenantURL": "https://e5.api.augmentcode.com/",
            "scopes": [],
        },
    )
    # missing email
    assert main([
        "--home", str(home), "import", "--session", str(sess),
    ]) == 1
    err = capsys.readouterr().err
    assert "--email" in err

    assert main([
        "--home", str(home), "import",
        "--session", str(sess),
        "--email", "bob@x.com",
    ]) == 0
    out = capsys.readouterr().out
    assert "bob@x.com" in out
    assert (home / "creds").exists()

    # conflict with blob
    assert main([
        "--home", str(home), "import", "eyJ",
        "--session", str(sess),
        "--email", "bob@x.com",
    ]) == 1

