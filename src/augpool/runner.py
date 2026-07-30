"""Wrap a command with pooled credentials and rate-limit failover."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from augpool.pool import Account, Pool, resolve_session_file
from augpool.select import pick
from augpool.session_io import apply_env, load_session
from augpool.state import State, record_selection, set_cooldown

RATE_LIMIT_PATTERNS = (
    re.compile(r"rate\s*limit", re.I),
    re.compile(r"\b429\b"),
    re.compile(r"too many requests", re.I),
    re.compile(r"\bquota\b", re.I),
    re.compile(r"credits?\s*(exhausted|exceeded|limit)", re.I),
    re.compile(r"\bbudget\b", re.I),
    re.compile(r"insufficient\s+credits", re.I),
)

DEFAULT_COOLDOWN_SECONDS = 300.0


@dataclass
class RunResult:
    exit_code: int
    account_email: str | None
    attempts: int
    failovers: int


def looks_like_rate_limit(text: str, exit_code: int) -> bool:
    if exit_code == 0:
        return False
    if not text:
        return exit_code == 429
    return any(p.search(text) for p in RATE_LIMIT_PATTERNS) or exit_code == 429


def _is_auggie(cmd: Sequence[str]) -> bool:
    """True for direct auggie binary or npx/npm exec of @augmentcode/auggie."""
    if not cmd:
        return False
    base = Path(cmd[0]).name.lower()
    if base in {"auggie", "auggie.exe"}:
        return True
    # npx -y @augmentcode/auggie ...  /  npm exec auggie
    joined = " ".join(cmd).lower()
    if "@augmentcode/auggie" in joined:
        return True
    if base in {"npx", "npx.cmd", "npm", "npm.cmd", "pnpm", "yarn", "bunx"}:
        for a in cmd[1:]:
            al = a.lower()
            if al in {"auggie", "@augmentcode/auggie"} or al.endswith("/auggie"):
                return True
    return False


def is_protocol_mode(cmd: Sequence[str]) -> bool:
    """ACP/MCP need raw inherited stdio — never buffer, prefer exec."""
    for a in cmd:
        if a in {"--acp", "--mcp"} or a.startswith("--acp=") or a.startswith("--mcp="):
            return True
    return False


def should_capture_output(cmd: Sequence[str], *, no_capture: bool = False) -> bool:
    """
    Decide whether to buffer child stdio.

    Capture only for one-shot print-mode runs where we want rate-limit text detection.
    Never capture ACP/MCP/interactive — that breaks the protocol handshake (kandev).
    """
    if no_capture:
        return False
    if is_protocol_mode(cmd):
        return False
    if _is_auggie(cmd):
        print_mode = any(
            a in {"-p", "--print"} or a.startswith("--print=") for a in cmd
        )
        # Interactive auggie: passthrough. Print mode: capture for failover heuristics.
        return print_mode
    # Non-auggie: capture only when stdout is not a TTY (scripts); TTY passthrough.
    if sys.stdout.isatty():
        return False
    # Non-TTY non-auggie still defaults to capture for failover — but never for protocols
    return True


def _has_resume_flag(cmd: Sequence[str]) -> bool:
    for a in cmd:
        if a in {"--continue", "-c", "--resume", "-r"}:
            return True
        if a.startswith("--resume=") or a.startswith("-r="):
            return True
    return False


def _has_session_flag(cmd: Sequence[str]) -> bool:
    return any(
        a == "--augment-session-json" or a.startswith("--augment-session-json=")
        for a in cmd
    )


def prepare_cmd(cmd: list[str], *, add_continue: bool) -> list[str]:
    out = list(cmd)
    # Never inject --continue into ACP/MCP servers
    if (
        add_continue
        and _is_auggie(out)
        and not _has_resume_flag(out)
        and not is_protocol_mode(out)
    ):
        out.append("--continue")
    return out


def _build_env_and_cmd(
    cmd: Sequence[str],
    account: Account,
    *,
    root: Path | None,
    env: dict[str, str] | None,
    add_continue: bool,
) -> tuple[list[str], dict[str, str], str | None]:
    """Return (final_cmd, env, optional temp session path to delete later)."""
    session = load_session(resolve_session_file(account, root))
    run_env = apply_env(session, env)
    final_cmd = prepare_cmd(list(cmd), add_continue=add_continue)

    tmp_path: str | None = None
    if _is_auggie(final_cmd) and not _has_session_flag(final_cmd):
        fd, tmp_path = tempfile.mkstemp(prefix="augpool-session-", suffix=".json")
        os.close(fd)
        Path(tmp_path).write_text(
            __import__("json").dumps(session), encoding="utf-8"
        )
        os.chmod(tmp_path, 0o600)
        final_cmd = list(final_cmd) + ["--augment-session-json", tmp_path]
    return final_cmd, run_env, tmp_path


def run_with_account(
    cmd: Sequence[str],
    account: Account,
    *,
    root: Path | None,
    env: dict[str, str] | None = None,
    add_continue: bool = False,
    capture: bool = True,
    use_exec: bool = False,
) -> tuple[int, str]:
    final_cmd, run_env, tmp_path = _build_env_and_cmd(
        cmd, account, root=root, env=env, add_continue=add_continue
    )

    # ACP/MCP: replace this process so the host (kandev) speaks stdio to auggie
    # with no intermediate parent buffering or waiting.
    if use_exec or (is_protocol_mode(final_cmd) and not capture):
        # Best-effort: record selection already done by caller for protocol path.
        # Temp session file is intentionally left for the child lifetime; OS cleans /tmp.
        try:
            os.execvpe(final_cmd[0], final_cmd, run_env)
        except OSError as e:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise RuntimeError(f"exec failed for {final_cmd[0]!r}: {e}") from e

    try:
        if capture:
            proc = subprocess.run(
                final_cmd,
                env=run_env,
                text=True,
                capture_output=True,
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            # Replay output so user still sees it
            if proc.stdout:
                sys.stdout.write(proc.stdout)
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            return proc.returncode, combined
        proc = subprocess.run(final_cmd, env=run_env)
        return proc.returncode, ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def run_pooled(
    pool: Pool,
    state: State,
    cmd: Sequence[str],
    *,
    root: Path | None = None,
    account_email: str | None = None,
    usage: dict[str, float] | None = None,
    max_failovers: int = 2,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    capture: bool | None = None,
    on_before_exec=None,
) -> RunResult:
    """
    Run cmd under a pooled account.

    For ACP/MCP (`--acp` / `--mcp`), picks an account, optionally calls
    on_before_exec(account_email) so the caller can persist state, then os.exec's
    the child (does not return).
    """
    if not cmd:
        raise ValueError("command required")

    if capture is None:
        capture = should_capture_output(cmd)

    protocol = is_protocol_mode(cmd)
    # Protocol servers cannot failover mid-flight; single pick + exec.
    if protocol:
        if account_email:
            account = pool.get(account_email)
        else:
            account = pick(pool, state, usage).account
        record_selection(state, account.email)
        if on_before_exec is not None:
            on_before_exec(account.email)
        # never returns on success
        run_with_account(
            cmd,
            account,
            root=root,
            add_continue=False,
            capture=False,
            use_exec=True,
        )
        # unreachable
        return RunResult(exit_code=1, account_email=account.email, attempts=1, failovers=0)

    excluded: list[str] = []
    attempts = 0
    failovers = 0
    add_continue = False

    while True:
        if account_email and attempts == 0:
            account = pool.get(account_email)
            from augpool.select import RankedAccount

            ranked = RankedAccount(
                account=account,
                credits_consumed=0,
                local_uses=0,
                last_selected_at=None,
                score=0,
                in_cooldown=False,
                cooldown_until=None,
                source="forced",
            )
        else:
            ranked = pick(pool, state, usage, exclude_ids=excluded)

        attempts += 1
        code, output = run_with_account(
            cmd,
            ranked.account,
            root=root,
            add_continue=add_continue,
            capture=capture,
        )

        if code == 0:
            record_selection(state, ranked.account.email)
            return RunResult(
                exit_code=0,
                account_email=ranked.account.email,
                attempts=attempts,
                failovers=failovers,
            )

        if looks_like_rate_limit(output, code) and failovers < max_failovers:
            set_cooldown(
                state,
                ranked.account.email,
                cooldown_seconds,
                error="rate_limit",
            )
            excluded.append(ranked.account.email)
            failovers += 1
            add_continue = True
            account_email = None  # auto-pick next
            continue

        record_selection(state, ranked.account.email)
        return RunResult(
            exit_code=code,
            account_email=ranked.account.email,
            attempts=attempts,
            failovers=failovers,
        )
