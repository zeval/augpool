"""augpool command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Sequence

from augpool import __version__, paths
from augpool.analytics import get_fresh_usage, load_usage_cache, refresh_usage
from augpool.pool import (
    add_account,
    find_account,
    load_pool,
    remove_account,
    resolve_session_file,
    set_active,
)
from augpool.runner import run_pooled
from augpool.select import pick, rank_accounts
from augpool.session_io import (
    backup_and_use,
    build_share_envelope,
    decode_share_blob,
    encode_share_blob,
    export_shell_line,
    load_session,
    normalize_email,
    read_share_blob_arg,
    restore_backup,
)
from augpool.state import locked_state, record_selection
from augpool.usage_view import render_usage_dashboard, usage_report_payload


def _root_from_args(args: argparse.Namespace) -> Path:
    if getattr(args, "home", None):
        return Path(args.home).expanduser().resolve()
    return paths.home()


def _resolve_who(pool, state, who: str | None, usage):
    if who:
        return find_account(pool, who)
    return pick(pool, state, usage).account


def _usage_map(pool, root: Path, *, force: bool = False, refresh_if_stale: bool = True):
    return get_fresh_usage(pool, root=root, force=force, refresh_if_stale=refresh_if_stale)


def cmd_add(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    account = add_account(
        pool,
        email=args.email,
        session_source=args.session,
        root=root,
        label=args.label or "",
        weight=args.weight,
        notes=args.notes or "",
        force=bool(getattr(args, "force", False)),
    )
    print(f"added {account.email} -> {account.session_path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import blob, --self (current session.json), or --session file + --email."""
    root = _root_from_args(args)
    pool = load_pool(root)

    session_flag = getattr(args, "session", None)
    modes = sum(bool(x) for x in (args.self, session_flag, args.blob))
    if modes > 1:
        raise ValueError(
            "pass only one of: blob, --self, or --session\n"
            "  examples:\n"
            "    augpool import eyJ...\n"
            "    augpool import --self --email you@company.com\n"
            "    augpool import --session ./session.json --email you@company.com"
        )

    if args.self or session_flag:
        if not args.email:
            which = "--self" if args.self else "--session"
            extra = "" if args.self else "./session.json "
            raise ValueError(
                f"import {which} requires --email\n"
                f"  example: augpool import {which} {extra}--email you@company.com"
            )
        email = normalize_email(args.email)
        if args.self:
            session_path = paths.expand(pool.augment_session_path)
            if not session_path.is_file():
                raise FileNotFoundError(
                    f"no current session at {session_path}\n"
                    "  fix: run auggie login first, then retry"
                )
            session = load_session(session_path)
            source_label = str(session_path)
            force = True
        else:
            if str(session_flag) == "-":
                import json as _json
                import sys

                try:
                    payload = _json.load(sys.stdin)
                except _json.JSONDecodeError as e:
                    raise ValueError(f"stdin is not valid JSON: {e}") from e
                from augpool.session_io import validate_session

                session = validate_session(payload)
                source_label = "stdin"
            else:
                session_path = paths.expand(session_flag)
                if not session_path.is_file():
                    raise FileNotFoundError(f"session file not found: {session_path}")
                session = load_session(session_path)
                source_label = str(session_path)
            force = bool(args.force)

        account = add_account(
            pool,
            email=email,
            session=session,
            root=root,
            label=args.label or email,
            force=force,
        )
        print(f"imported {account.email} from {source_label}")
        return 0

    if not args.blob:
        raise ValueError(
            "blob required, or use --self / --session\n"
            "  example: augpool import eyJ...\n"
            "  example: augpool import --self --email you@company.com\n"
            "  example: augpool import --session ./session.json --email you@company.com"
        )
    if args.email:
        raise ValueError(
            "--email is only valid with --self or --session "
            "(blob already embeds the email)"
        )
    raw = read_share_blob_arg(args.blob)
    env = decode_share_blob(raw)
    account = add_account(
        pool,
        email=env["email"],
        session=env["session"],
        root=root,
        label=env.get("label") or "",
        force=bool(args.force),
    )
    print(f"imported {account.email}")
    return 0



def cmd_remove(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    account = remove_account(pool, args.email, root=root)
    print(f"removed {account.email}")
    return 0
def cmd_list(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    with locked_state(root) as state:
        usage = _usage_map(pool, root, force=args.refresh, refresh_if_stale=True)
        ranked = rank_accounts(pool, state, usage)
    if args.json:
        payload = [
            {
                "email": r.account.email,
                "enabled": r.account.enabled,
                "weight": r.account.weight,
                "score": r.score,
                "credits_consumed": r.credits_consumed,
                "local_uses": r.local_uses,
                "source": r.source,
                "in_cooldown": r.in_cooldown,
                "active": r.account.email == pool.active_email,
            }
            for r in ranked
        ]
        enabled_emails = {r.account.email for r in ranked}
        for a in pool.accounts:
            if a.email not in enabled_emails:
                payload.append(
                    {
                        "email": a.email,
                        "enabled": a.enabled,
                        "weight": a.weight,
                        "score": None,
                        "credits_consumed": None,
                        "local_uses": None,
                        "source": None,
                        "in_cooldown": False,
                        "active": a.email == pool.active_email,
                    }
                )
        print(json.dumps(payload, indent=2))
        return 0
    if not pool.accounts:
        print("pool empty")
        print("  augpool import --self --email you@company.com")
        print("  augpool import <blob>")
        return 0
    headers = ("EMAIL", "SCORE", "CREDITS", "LOCAL", "SRC", "COOL", "ACTIVE")
    rows = []
    for r in ranked:
        rows.append(
            (
                r.account.email[:40],
                f"{r.score:.1f}",
                f"{r.credits_consumed:.0f}",
                str(r.local_uses),
                r.source[:5],
                "yes" if r.in_cooldown else "-",
                "*" if r.account.email == pool.active_email else "",
            )
        )
    for a in pool.accounts:
        if not a.enabled:
            rows.append((a.email[:40], "-", "-", "-", "off", "-", ""))
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))
    cache = load_usage_cache(root)
    if cache and cache.get("fetched_at"):
        age = int(time.time() - float(cache["fetched_at"]))
        print(f"\nusage cache age: {age}s  window: {cache.get('start_date')}..{cache.get('end_date')}")
        if cache.get("errors"):
            print("cache notes: " + "; ".join(cache["errors"]))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    if not pool.accounts:
        print("pool empty — nothing to refresh", file=sys.stderr)
        return 1
    cache = refresh_usage(pool, root=root)
    print(f"refreshed {len(cache.get('by_id') or {})} accounts ({cache.get('start_date')}..{cache.get('end_date')})")
    for err in cache.get("errors") or []:
        print(f"warning: {err}", file=sys.stderr)
    for email, credits in sorted((cache.get("by_id") or {}).items()):
        print(f"  {email}: {credits}")
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    with locked_state(root) as state:
        usage = _usage_map(pool, root, force=args.refresh, refresh_if_stale=True)
        cache = load_usage_cache(root)
        if args.json:
            print(
                json.dumps(
                    usage_report_payload(pool, state, usage, cache),
                    indent=2,
                )
            )
            return 0

        color = (
            not args.no_color
            and sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM") != "dumb"
        )
        width = min(120, shutil.get_terminal_size(fallback=(88, 24)).columns)
        print(
            render_usage_dashboard(
                pool,
                state,
                usage,
                cache,
                width=width,
                color=color,
            )
        )
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    with locked_state(root) as state:
        usage = _usage_map(pool, root)
        ranked = pick(pool, state, usage)
    if args.json:
        print(json.dumps({"email": ranked.account.email, "score": ranked.score}))
    else:
        print(ranked.account.email)
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    with locked_state(root) as state:
        if args.email:
            account = find_account(pool, args.email)
        else:
            usage = _usage_map(pool, root)
            account = pick(pool, state, usage).account
        session = load_session(resolve_session_file(account, root))
        target = pool.augment_session_path
        backup = backup_and_use(session, target, root=root)
        record_selection(state, account.email)
        set_active(pool, account.email, root=root)
    print(f"active -> {account.email}")
    print(f"wrote {paths.expand(target)}")
    if backup:
        print(f"backup {backup}")
    return 0



def cmd_export(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    with locked_state(root) as state:
        if args.self and args.email:
            raise ValueError("pass either --self or an email, not both")
        # Known target -> no analytics. Auto-pick only then ranks usage.
        if args.self:
            who = pool.active_email
            if not who and len(pool.accounts) == 1:
                who = pool.accounts[0].email
            if not who:
                raise ValueError(
                    "export --self needs an active account\n"
                    "  fix: augpool use you@company.com\n"
                    "  or:  augpool export you@company.com"
                )
            account = find_account(pool, who)
        elif args.email:
            account = find_account(pool, args.email)
        else:
            usage = _usage_map(pool, root)
            account = pick(pool, state, usage).account
        if args.record:
            record_selection(state, account.email)
        session_path = resolve_session_file(account, root)
        if not session_path.is_file():
            raise FileNotFoundError(
                f"missing session file for {account.email}: {session_path}\n"
                "  fix: re-import the account"
            )
        session = load_session(session_path)
    envelope = build_share_envelope(email=account.email, session=session, label=account.label)
    if args.env:
        print(export_shell_line(session))
    elif args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        print(encode_share_blob(envelope))
    print(f"# exported {account.email}", file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    cmd = list(args.cmd)
    if not cmd:
        print("usage: augpool run -- <command...>", file=sys.stderr)
        return 2
    pool = load_pool(root)
    if not pool.accounts:
        print("error: pool empty — import an account first", file=sys.stderr)
        print("  augpool import --self --email you@company.com", file=sys.stderr)
        return 1
    usage = _usage_map(pool, root)
    from augpool.runner import is_protocol_mode, should_capture_output
    from augpool.state import load_state, save_state
    state = load_state(root)
    capture = should_capture_output(cmd, no_capture=args.no_capture)
    forced_email = None
    if args.email:
        forced_email = find_account(pool, args.email).email
    def _persist(account_email: str) -> None:
        save_state(state, root)
        set_active(pool, account_email, root=root)
        print(f"# augpool: account={account_email} (protocol exec)", file=sys.stderr)
    result = run_pooled(
        pool,
        state,
        cmd,
        root=root,
        account_email=forced_email,
        usage=usage,
        max_failovers=args.max_failovers,
        capture=capture,
        on_before_exec=_persist if is_protocol_mode(cmd) else None,
    )
    save_state(state, root)
    if result.account_email:
        set_active(pool, result.account_email, root=root)
    if result.failovers:
        print(
            f"# augpool: {result.attempts} attempts, {result.failovers} failovers, "
            f"last={result.account_email}",
            file=sys.stderr,
        )
    return result.exit_code


def cmd_status(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    session_path = paths.expand(pool.augment_session_path)
    print(f"home:     {root}")
    print(f"session:  {session_path}  ({'exists' if session_path.is_file() else 'missing'})")
    print(f"active:   {pool.active_email or '(none)'}")
    print(f"accounts: {len(pool.accounts)}  strategy: {pool.strategy}")
    cache = load_usage_cache(root)
    if cache:
        age = int(time.time() - float(cache.get("fetched_at") or 0))
        print(f"usage:    cache age {age}s  ttl {pool.usage_cache_ttl_seconds}s")
    else:
        print("usage:    no cache (run augpool refresh)")
    return cmd_list(args)


def cmd_restore(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    pool = load_pool(root)
    target = restore_backup(pool.augment_session_path, root=root)
    set_active(pool, None, root=root)
    print(f"restored {target} from backup")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="augpool",
        description="Pool and load-balance Augment Code credentials (identity = email)",
    )
    p.add_argument("--version", action="version", version=f"augpool {__version__}")
    p.add_argument("--home", help="override AUGPOOL_HOME / ~/.augpool")
    sub = p.add_subparsers(dest="command", required=True)
    add_p = sub.add_parser("add", help="add a session.json keyed by email")
    add_p.add_argument("--email", required=True, help="account email (unique key)")
    add_p.add_argument("--session", required=True, help="path to session.json, or - for stdin")
    add_p.add_argument("--label", default="")
    add_p.add_argument("--weight", type=float, default=1.0)
    add_p.add_argument("--notes", default="")
    add_p.add_argument("--force", action="store_true", help="replace existing email")
    add_p.set_defaults(func=cmd_add)
    imp_p = sub.add_parser(
        "import",
        help="import share blob, --self, or --session file (email required for file/self)",
    )
    imp_p.add_argument("blob", nargs="?", default=None, help="base64url blob, or - for stdin blob")
    imp_p.add_argument(
        "--self",
        action="store_true",
        help="import ~/.augment/session.json (requires --email)",
    )
    imp_p.add_argument(
        "--session",
        default=None,
        help="path to session.json (or - for stdin JSON); requires --email",
    )
    imp_p.add_argument(
        "--email",
        default=None,
        help="required with --self or --session",
    )
    imp_p.add_argument("--label", default="")
    imp_p.add_argument("--force", action="store_true", help="replace existing email")
    imp_p.set_defaults(func=cmd_import)
    rm_p = sub.add_parser("remove", help="remove an account by email")
    rm_p.add_argument("email", help="full email (or unique local-part)")
    rm_p.set_defaults(func=cmd_remove)
    ls_p = sub.add_parser("list", help="list accounts ranked least-used first")
    ls_p.add_argument("--json", action="store_true")
    ls_p.add_argument("--refresh", action="store_true")
    ls_p.set_defaults(func=cmd_list)
    rf_p = sub.add_parser("refresh", help="pull Analytics credit usage now")
    rf_p.set_defaults(func=cmd_refresh)
    usage_p = sub.add_parser(
        "usage",
        help="show account credits and local sessions as a terminal dashboard",
    )
    usage_p.add_argument(
        "--json",
        action="store_true",
        help="emit structured JSON instead of the dashboard",
    )
    usage_p.add_argument(
        "--refresh",
        action="store_true",
        help="refresh Analytics before rendering",
    )
    usage_p.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI color",
    )
    usage_p.set_defaults(func=cmd_usage)
    nx_p = sub.add_parser("next", help="print least-used account email")
    nx_p.add_argument("--json", action="store_true")
    nx_p.set_defaults(func=cmd_next)
    use_p = sub.add_parser("use", help="write account session to ~/.augment/session.json")
    use_p.add_argument("email", nargs="?", default=None)
    use_p.set_defaults(func=cmd_use)
    ex_p = sub.add_parser("export", help="print portable share blob (email is identity)")
    ex_p.add_argument("email", nargs="?", default=None)
    ex_p.add_argument("--self", action="store_true", help="export active account")
    ex_p.add_argument("--record", action="store_true")
    ex_p.add_argument("--env", action="store_true")
    ex_p.add_argument("--json", action="store_true")
    ex_p.set_defaults(func=cmd_export)
    run_p = sub.add_parser("run", help="run command with pooled auth + failover")
    run_p.add_argument("--email", default=None)
    run_p.add_argument("--max-failovers", type=int, default=2)
    run_p.add_argument("--no-capture", action="store_true")
    run_p.add_argument("cmd", nargs=argparse.REMAINDER)
    run_p.set_defaults(func=cmd_run)
    st_p = sub.add_parser("status", help="show pool + active session status")
    st_p.add_argument("--json", action="store_true")
    st_p.add_argument("--refresh", action="store_true")
    st_p.set_defaults(func=cmd_status)
    rs_p = sub.add_parser("restore", help="restore ~/.augment/session.json from backup")
    rs_p.set_defaults(func=cmd_restore)
    return p


# Built-in subcommands. Anything else at argv[0] is treated as auggie args
# via the default: augpool [run-opts] [auggie-args...]  ==  augpool run [run-opts] -- auggie [auggie-args...]
_SUBCOMMANDS = frozenset({
    "add", "import", "remove", "list", "refresh", "next", "use", "export",
    "run", "status", "restore", "usage", "help",
})


def _normalize_argv(argv: list[str]) -> list[str]:
    """Rewrite bare invocation into an explicit `run -- auggie ...` form.

    Examples:
      augpool                         -> run -- auggie
      augpool -p -q hi                -> run -- auggie -p -q hi
      augpool --email x@y -p hi       -> run --email x@y -- auggie -p hi
      augpool --acp --allow-indexing  -> run -- auggie --acp --allow-indexing
      augpool run -- auggie -p hi     -> unchanged
      augpool list                    -> unchanged
    """
    if not argv:
        return ["run", "--", "auggie"]

    # Global flags that belong to augpool itself (before any subcommand).
    # --home / --version are on the root parser; leave them in place.
    i = 0
    prefix: list[str] = []
    while i < len(argv):
        a = argv[i]
        if a in {"--home"} and i + 1 < len(argv):
            prefix.extend([a, argv[i + 1]])
            i += 2
            continue
        if a.startswith("--home="):
            prefix.append(a)
            i += 1
            continue
        if a in {"--version", "-h", "--help"}:
            # Let argparse handle help/version on root or after rewrite.
            break
        break

    rest = argv[i:]
    if not rest:
        return prefix + ["run", "--", "auggie"]

    head = rest[0]
    # Root help/version must stay on the root parser (not auggie).
    if head in {"-h", "--help", "--version"}:
        return prefix + rest
    # Explicit subcommand (including `run`) — pass through.
    if head in _SUBCOMMANDS:
        return prefix + rest

    # Otherwise: optional run-specific flags, then auggie args.
    # Run flags: --email, --max-failovers, --no-capture
    run_opts: list[str] = []
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "--email" and j + 1 < len(rest):
            run_opts.extend([a, rest[j + 1]])
            j += 2
            continue
        if a.startswith("--email="):
            run_opts.append(a)
            j += 1
            continue
        if a == "--max-failovers" and j + 1 < len(rest):
            run_opts.extend([a, rest[j + 1]])
            j += 2
            continue
        if a.startswith("--max-failovers="):
            run_opts.append(a)
            j += 1
            continue
        if a == "--no-capture":
            run_opts.append(a)
            j += 1
            continue
        # Stop at first non-run flag — remainder is auggie argv.
        break

    auggie_args = rest[j:]
    # If user already wrote `auggie` as first arg, do not double-prefix.
    if auggie_args and Path(auggie_args[0]).name in {"auggie", "auggie.exe"}:
        cmd = auggie_args
    else:
        cmd = ["auggie", *auggie_args]
    return prefix + ["run", *run_opts, "--", *cmd]


def main(argv: Sequence[str] | None = None) -> int:
    argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run" and args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return 0
    except (KeyError, ValueError, FileNotFoundError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
