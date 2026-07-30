# augpool

Pool multiple Augment Code credentials and pick the least-used account.

**Identity is always the full email.** No separate ids, no me.

## Install

    cd augpool
    pip install -e ".[dev]"

Requires Python 3.11+. Zero runtime deps.

## Default: auggie wrapper

Bare `augpool` is `augpool run -- auggie`:

    augpool                         # interactive auggie with pooled auth
    augpool -p -q "ping"            # print mode
    augpool --acp --allow-indexing  # kandev ACP
    augpool --email you@x.com -p hi # force account
    augpool list                    # still a subcommand

## Quick start

    # Register the currently logged-in auggie user
    augpool import --self --email you@company.com
    augpool import --session ./session.json --email you@company.com

    # Or add any session.json
    augpool add --email other@company.com --session ./other-session.json

    # Share (one shell-safe base64url token — no quotes; email is inside the blob)
    augpool export you@company.com
    # eyJlbWFpbCI6InlvdUBjb21wYW55LmNvbSIs...

    # After augpool use you@company.com:
    augpool export --self

    # Teammate pastes:
    augpool import eyJlbWFpbCI6InlvdUBjb21wYW55LmNvbSIs...

    augpool list
    augpool run -- auggie -p -q "ping"
    eval "$(augpool export --env you@company.com)"
    augpool use
    augpool use you@company.com
    augpool restore

Data lives in ~/.augpool/ (override with AUGPOOL_HOME or --home).

## Share blob

export prints unpadded base64url of {v,email,label,session} (v=2).
No prefix → paste without quoting: augpool import eyJ…

The blob is the full credential. Do not put it in git/public chat.

## Analytics

Usage-aware ranking uses each account session accessToken against:

GET https://api.augmentcode.com/analytics/v0/credit-usage-by-user

Cache TTL defaults to 5 minutes. use / run / list / next refresh when expired.

## Commands

| Command | Purpose |
|---|---|
| import --self --email … | Load ~/.augment/session.json under that email |
| import blob\|file\|- | Import share blob from export |
| add --email … --session … | Add session.json keyed by email |
| export you@x / export --self | Print base64url share blob (--env / --json) |
| remove you@x | Drop an account |
| list | Ranked table (--json, --refresh) |
| refresh | Pull Analytics now |
| next | Print least-used email |
| use [email] | Write session to ~/.augment/session.json |
| run -- cmd | Run cmd with pooled auth + failover |
| status | Home, active, ranks |
| restore | Undo last use |

## ACP / MCP (kandev)

    augpool run -- npx -y @augmentcode/auggie --acp --allow-indexing

For --acp / --mcp, run does not capture stdio. It picks an account, injects auth, then os.execs the child.

## Security

- Creds stored mode 0600 under ~/.augpool/creds/
- Prefer run over export --env
- Never commit session files or share blobs

## Tests

    pip install -e ".[dev]"
    pytest -q
