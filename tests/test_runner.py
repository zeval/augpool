from __future__ import annotations

import sys
from pathlib import Path

from augpool.pool import load_pool
from augpool.runner import (
    _is_auggie,
    is_protocol_mode,
    looks_like_rate_limit,
    prepare_cmd,
    run_pooled,
    should_capture_output,
)
from augpool.state import empty_state


def test_looks_like_rate_limit():
    assert looks_like_rate_limit("Error: rate limit exceeded", 1)
    assert looks_like_rate_limit("HTTP 429", 1)
    assert looks_like_rate_limit("", 429)
    assert not looks_like_rate_limit("syntax error", 1)
    assert not looks_like_rate_limit("rate limit", 0)


def test_is_auggie_detects_npx():
    assert _is_auggie(["auggie", "--acp"])
    assert _is_auggie(["npx", "-y", "@augmentcode/auggie", "--acp"])
    assert _is_auggie(["npx", "auggie", "-p", "hi"])
    assert not _is_auggie(["echo", "hi"])


def test_protocol_mode_never_captures():
    cmd = ["npx", "-y", "@augmentcode/auggie", "--acp", "--allow-indexing"]
    assert is_protocol_mode(cmd)
    assert should_capture_output(cmd) is False
    assert should_capture_output(["auggie", "--mcp"]) is False
    assert should_capture_output(["auggie", "-p", "-q", "hi"]) is True


def test_prepare_cmd_adds_continue():
    assert "--continue" in prepare_cmd(["auggie", "-p", "hi"], add_continue=True)
    assert prepare_cmd(["auggie", "--continue"], add_continue=True).count("--continue") == 1
    assert "--continue" not in prepare_cmd(["echo", "hi"], add_continue=True)
    assert "--continue" not in prepare_cmd(
        ["npx", "-y", "@augmentcode/auggie", "--acp"], add_continue=True
    )


def test_run_success(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    result = run_pooled(
        pool,
        state,
        [sys.executable, "-c", "print('ok')"],
        root=home,
        usage={"alice@acme.com": 10, "bob@acme.com": 1},
        max_failovers=1,
    )
    assert result.exit_code == 0
    assert result.account_email == "bob@acme.com"
    assert state.for_account("bob@acme.com").local_uses == 1


def test_run_failover_on_rate_limit(two_account_pool, home: Path, tmp_path: Path):
    counter = tmp_path / "n"
    counter.write_text("0", encoding="utf-8")
    script = tmp_path / "flaky.py"
    script.write_text(
        f"""
import sys
p = {str(counter)!r}
n = int(open(p).read())
open(p, "w").write(str(n + 1))
if n == 0:
    print("rate limit exceeded", file=sys.stderr)
    sys.exit(1)
print("ok")
sys.exit(0)
""",
        encoding="utf-8",
    )
    pool = load_pool(home)
    state = empty_state()
    result = run_pooled(
        pool,
        state,
        [sys.executable, str(script)],
        root=home,
        usage={"alice@acme.com": 1, "bob@acme.com": 100},
        max_failovers=2,
        cooldown_seconds=60,
    )
    assert result.exit_code == 0
    assert result.failovers == 1
    assert result.account_email == "bob@acme.com"
    assert state.for_account("alice@acme.com").cooldown_until is not None


def test_run_non_rate_limit_no_failover(two_account_pool, home: Path):
    pool = load_pool(home)
    state = empty_state()
    result = run_pooled(
        pool,
        state,
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        root=home,
        usage={"alice@acme.com": 1, "bob@acme.com": 100},
        max_failovers=2,
    )
    assert result.exit_code == 3
    assert result.failovers == 0
    assert result.attempts == 1
