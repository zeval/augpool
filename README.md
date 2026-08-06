# augpool

[![Build](https://github.com/zeval/augpool/actions/workflows/build.yml/badge.svg)](https://github.com/zeval/augpool/actions/workflows/build.yml)

Pool multiple [Augment Code](https://www.augmentcode.com) credentials and prefer
the **least-used** account so org credit spend stays balanced.

Identity is always the **full email**. No short ids.

| | |
|---|---|
| **Home** | `~/.augpool/` — override with `AUGPOOL_HOME` or `--home` |
| **Runtime** | Python **3.11+**, zero third-party deps |
| **Auth inject** | `AUGMENT_SESSION_AUTH` and `auggie --augment-session-json` |

---

## Install

The `augpool` binary must sit on a **stable PATH** that both your terminal and
agent hosts (e.g. kandev boot scripts) can resolve. A project venv alone is
usually **not** enough for agents.

### Recommended — pipx

```bash
pipx install augpool
# → ~/.local/bin/augpool   (keep ~/.local/bin on PATH)
```

### User install

```bash
python3 -m pip install --user augpool
# ensure ~/.local/bin is on PATH for login shells *and* GUI / agent processes
```

### Editable (development)

```bash
git clone https://github.com/zeval/augpool.git
cd augpool
python3 -m pip install -e ".[dev]"
```

Editable installs place the console script in **whichever** environment’s
`bin/` you pip into (conda base, Homebrew Python, …). That only works for
kandev if that `bin/` is on the agent’s PATH.

**Avoid for agent use:**

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install …
```

That works only while the venv is activated. kandev will not find `augpool`.

---

## First things to do

Do these in order. Every example uses placeholders only (`you@example.com`).

### 1. Import **your** account (start here)

You need at least one credential in the pool. Easiest path: whatever auggie is
already logged in as on this machine.

```bash
# After normal `auggie login` (or any existing ~/.augment/session.json)
augpool import --self --email you@example.com
```

Or point at a session file you already have:

```bash
augpool import --session ./session.json --email you@example.com

# stdin JSON
augpool import --session - --email you@example.com < session.json

# replace if that email is already in the pool
augpool import --session ./session.json --email you@example.com --force
```

Auggie session JSON looks like `{ "accessToken", "tenantURL", "scopes"? }`
(typical path: `~/.augment/session.json`). The file has **no email field**, so
`--email` is always required for `--self` / `--session`.

```bash
augpool list          # confirm you’re in the pool
augpool               # run auggie under the pool (= augpool run -- auggie)
augpool -p -q "ping"
```

### 2. Export **your** account (share with teammates)

```bash
# By email (preferred — no Analytics wait)
augpool export you@example.com
# → one line, unpadded base64url, shell-safe (no quotes):
# eyJlbWFpbCI6InlvdUBleGFtcGxlLmNvbSIs...

# Or whoever is currently written into ~/.augment/session.json
augpool export --self
```

Send that **one token** over a private channel. It is a full credential.

Optional shapes:

```bash
augpool export --json you@example.com    # pretty envelope
eval "$(augpool export --env you@example.com)"   # shell inject (history risk)
```

### 3. Import **from other users** (grow the pool)

They run step 2 and paste you a blob. You do **not** pass `--email` — it’s
inside the blob.

```bash
augpool import eyJlbWFpbCI6InRlYW1tYXRlQGV4YW1wbGUuY29tLC4uLg
augpool import --force eyJ...    # overwrite if that email already exists
```

If they give you a **session file** instead of a blob, you supply their email:

```bash
augpool import --session ./teammate-session.json --email teammate@example.com
# same thing:
augpool add --email teammate@example.com --session ./teammate-session.json
```

```bash
augpool list          # ranked least-used first
```

**Rules**

| Mode | Needs |
|---|---|
| Share blob (`eyJ…`) | token only (email embedded) |
| `--self` | `--email` |
| `--session` | `--email` |

Pass **only one** of blob / `--self` / `--session`.

Treat blobs and session files like passwords. Import others only with consent.
Never commit them. Never paste them into public chat.

Blob shape (v2): `{ v, email, label, session }` — unpadded base64url, no prefix.

---

## Default: auggie wrapper

Bare `augpool` is `augpool run -- auggie …`:

```bash
augpool                              # interactive
augpool -p -q "hello"                # print mode
augpool --email you@example.com …    # force account
augpool list                         # subcommands still work
```

### kandev / ACP

```bash
augpool --acp --allow-indexing
# equivalent:
augpool run -- npx -y @augmentcode/auggie --acp --allow-indexing
```

For `--acp` / `--mcp`, augpool does **not** buffer stdio: pick account → inject
auth → `os.exec` the child so the host owns the protocol pipes.

**Agent boot** must resolve `augpool` on PATH (pipx, user install, or an env
whose `bin/` is always on PATH — not an unactivated venv).

---

## IDE session file

```bash
augpool use                      # least-used → ~/.augment/session.json
augpool use you@example.com      # force
augpool restore                  # undo last use
```

Prefer `augpool` / `augpool run` for CLI so the global session file stays untouched.

---

## Analytics ranking

Least-used score uses each account’s session token against:

`GET https://api.augmentcode.com/analytics/v0/credit-usage-by-user`

- Cache: `~/.augpool/cache/` (default TTL ~5 minutes)
- Auto-pick (`list` / `next` / bare run) refreshes when stale
- Explicit targets (`export you@…`, `use you@…`) skip refresh
- No Analytics access → local use counters

```bash
augpool refresh
augpool list --refresh
```

---

## Commands

| Command | Purpose |
|---|---|
| `import --self --email …` | Load `~/.augment/session.json` |
| `import --session PATH --email …` | Load a session file (`-` = stdin JSON) |
| `import <blob>` | Import portable share blob |
| `add --email … --session …` | Add / replace from session file |
| `export [email] \| --self` | Print share blob (`--env`, `--json`) |
| `remove you@example.com` | Drop account |
| `list` | Ranked table (`--json`, `--refresh`) |
| `refresh` | Pull Analytics now |
| `next` | Print least-used email |
| `use [email]` | Write into `~/.augment/session.json` |
| `run -- <cmd…>` | Run with pooled auth + rate-limit failover |
| `status` | Home, active account, ranks |
| `restore` | Restore session file from backup |

---

## Layout

```text
~/.augpool/
  pool.json            # registry (emails, paths, weights)
  state.json           # local uses, cooldowns, locks
  cache/usage.json     # Analytics snapshot
  creds/<email>.json   # per-account session (mode 0600)
  backups/             # previous ~/.augment/session.json
```

---

## Security

- Cred files are mode `0600`
- Prefer `augpool run` over `export --env`
- Never commit `creds/`, session JSON, or share blobs
- A share blob **is** the full credential — rotate if it leaks

---

## Tests

```bash
python3 -m pip install -e ".[dev]"
pytest -q
```

---

## Limits

- Auth is fixed for one `auggie` process lifetime; failover restarts the child
  (appends `--continue` when safe)
- ACP / MCP protocol mode: single pick + `exec` (no mid-flight failover)
- MCP OAuth tokens are stored by Auggie **per Augment account**, separate from
  this pool
