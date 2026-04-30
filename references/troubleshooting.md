# korpus.cz skill — troubleshooting

Symptoms grouped from most common (auth) to most surprising (downstream API quirks). When in doubt, work top-to-bottom.

## Quick triage

```bash
# 1. Is the script reaching the server at all?
curl -sS -o /dev/null -w "HTTP %{http_code} time_total=%{time_total}s\n" https://www.korpus.cz/

# 2. Does the script see a token?
python3 scripts/korpuscz_client.py login

# 3. Does the session cookie work for an API call?
python3 scripts/korpuscz_client.py corpus-info syn2020 | head -c 200
```

The login output names which provider yielded the token (`token_source`). If that field is wrong, the precedence chain in `scripts/korpuscz_client.py:resolve_token` is the source of truth.

## Auth / token

### `No korpus.cz token available. Set $KORPUSCZ_API_TOKEN, ...`

None of the configured providers returned a token. Check (in resolver-priority order):

1. `echo "${KORPUSCZ_API_TOKEN:0:6}..."` — is the env var set in *this shell*? Sub-shells / IDE-launched terminals often do not inherit `~/.zshrc` exports.
2. `cat "$KORPUSCZ_TOKEN_FILE"` — if you set `KORPUSCZ_TOKEN_FILE`, does the file exist and contain a token?
3. `security find-generic-password -s "$KORPUSCZ_KEYCHAIN_SERVICE" -a "$USER" -w` — does Keychain have the entry under the configured service name?
4. BWS path: see the next section.

### `Login returned 200 but no session cookie was set`

The server accepted the POST but did not issue a session — i.e. the personal access token is **invalid, revoked, or expired**.

Recover:

1. Log in to <https://www.korpus.cz/>, go to the user profile.
2. Generate a fresh Personal Access Token. **It is shown once** — copy it immediately.
3. Re-store via whichever provider is configured:
   - env var: `export KORPUSCZ_API_TOKEN="<new>"`
   - file: `echo '<new>' > "$KORPUSCZ_TOKEN_FILE" && chmod 600 "$KORPUSCZ_TOKEN_FILE"`
   - Keychain: `security delete-generic-password -s "$KORPUSCZ_KEYCHAIN_SERVICE" -a "$USER" 2>/dev/null; security add-generic-password -s "$KORPUSCZ_KEYCHAIN_SERVICE" -a "$USER" -w "<new>"`
   - BWS: `SECRET_ID="$(bws secret list "$(bws project list --output json | jq -r '.[]|select(.name=="korpus-cz").id')" --output json | jq -r '.[]|select(.key=="KORPUSCZ_API_TOKEN").id')"; bws secret edit --value "<new>" "$SECRET_ID"`
4. Delete the stale session: `rm -f cache/session_cookies.txt`
5. Retry: `python3 scripts/korpuscz_client.py login`

### `BWS_ACCESS_TOKEN is not set and not in macOS Keychain`

You configured the BWS provider (`KORPUSCZ_BWS_PROJECT` + `KORPUSCZ_BWS_KEY`) but the script can't reach Bitwarden. Verify the underlying machine token is in place:

```bash
security find-generic-password -s BWS_ACCESS_TOKEN -a "$USER" -w | wc -c
# should print something > 0
```

If empty, store a fresh BWS access token. With the token in your clipboard:

```bash
security delete-generic-password -s BWS_ACCESS_TOKEN -a "$USER" 2>/dev/null
security add-generic-password -s BWS_ACCESS_TOKEN -a "$USER" -w "$(pbpaste)"
```

Or set `BWS_ACCESS_TOKEN` directly in the shell environment. See the [Bitwarden Secrets Manager CLI docs](https://bitwarden.com/help/secrets-manager-cli/) for how to mint a machine-account access token.

### `bws project list failed: ...`

Either the BWS access token is bad, or the user's Bitwarden organisation has changed. `bws project list` from the shell is the fastest probe. Common causes: rotated token in the web UI, expired machine account, organisation migration.

## API behaviour

### HTTP 429 — rate limit

CNC limits at retrieval (2026-04-30): **12 requests/minute, 5000/day**. The script does not throttle. When batching, sleep ~5 seconds between sub-script invocations (or 6 to stay under 10 req/min). Script triplet `concordance + freq + collocations` is 3 API calls plus a possible `/login`, so a careless loop can blow the limit fast.

### HTTP 403 after a successful `login`

The token authenticated, but the corpus is **not licensed for this account**. Some corpora are gated:

- General access: `syn2020`, `oral_v3`, the InterCorp v15 family — usually free for registered users.
- Restricted: a few legacy reference corpora and some specialised ones — require an extra agreement clicked through in the user profile.

If the user expects access and is denied, point them to <https://www.korpus.cz/> → user profile → the corpus's licence agreement.

### Empty `Lines` / `Items` / `Blocks`

Either the query matched nothing, or the corpus identifier is subtly wrong (e.g. `syn-2020` instead of `syn2020`). Run `corpus-info <slug>` first; if that returns 404 or HTML, the slug is wrong.

### Transient HTTP 500 with body `{"Q": [], "messages": [["error", "'NoneType' object is not iterable"]]}`

A server-side glitch. Smoke-tested 2026-04-30: the *same* `q` and `fcrit` that fails will succeed when retried a few seconds later, often without any client change. Looks like a race condition in CNC's query-archive layer. **Just retry once.** If it persistently fails, the `q` may be genuinely stale — re-mint with a fresh `concordance` call.

**Common trigger and workaround**: this 500 fires reliably when the *first* call after `concordance --id-only` (or `--quiet-id`) is anything other than `view`. The query archive seems to need a render of the actual concordance to "warm" before frequency / collocation operations work against it. Recipe:

```bash
QID=$(... concordance --quiet-id ...)
... view "$QID" --pagesize 1 > /dev/null   # warm the q
... freq "$QID" --fcrit 'lemma/e 0~0>0'    # now succeeds
```

This is upstream behaviour, not a client bug. If you see the 500 in a fresh script, add a tiny `view` between `--quiet-id` and your real call.

### `q` ID 404s after a few hours

KonText archives concordances long-term, but **inactive ones are recomputed on demand** when you re-`view` them. A 404 on the *first* call after a delay is the recomputation kicking off; **just retry** — the second call typically succeeds. If it persistently 404s, the underlying CQL had a transient error or the corpus was reindexed; re-run `concordance` to mint a new ID.

### Response too large — `cached: true, path: cache/...`

Default behaviour: payloads larger than 64 KB (`KORPUSCZ_LARGE_PAYLOAD_BYTES`) are written to disk and the path is returned, to keep the LLM context manageable.

- To read a cached file: `cat cache/<label>-<hash>.json | jq .`
- To force inline regardless of size: `python3 scripts/korpuscz_client.py --full <subcommand> ...`
- To raise the threshold session-wide: `export KORPUSCZ_LARGE_PAYLOAD_BYTES=262144`

## Network / TLS

### `Network error contacting https://www.korpus.cz/...`

Generic transport failure. Most useful triage step: hit the same hostname from `curl` and compare:

```bash
curl -sS --max-time 8 -o /dev/null -w "HTTP %{http_code} time_connect=%{time_connect}\n" https://www.korpus.cz/
```

If `curl` works but the script fails, check Python's TLS root-certificate setup (`certifi` may be stale on a system Python; `uv run` or the system Homebrew Python will both have current roots).

### Behind an institutional proxy or VPN

`urllib` honours `HTTPS_PROXY` / `https_proxy`. If the user is on a corporate or institutional VPN and the API hangs, set the proxy explicitly. Unlike Sketch Engine, the CNC API does not (at retrieval) blocklist commercial-VPN IP ranges; if a request hangs and `curl` from the same shell hangs too, suspect DNS or routing rather than IP-based blocking.

## Cookie file gotchas

### Stale `session_cookies.txt`

If you copy the skill folder between machines or leave a session idle for many days, the cookie file may be stale. Delete it: `rm -f cache/session_cookies.txt`. The next call re-logs in.

### Permissions

The script chmods the cookie file to 0600 after writing. If you copy / restore the skill folder via `cp -r`, the permissions may not survive — re-run `python3 scripts/korpuscz_client.py login` to re-create it cleanly.

## Config drift (for shared / public users)

If a contributor's setup uses a *different* token-resolution provider from what the README suggests (e.g. they've extended the script with a 1Password CLI lookup), and the CI / smoke test fails, the issue is almost always that the env vars expected by the chosen provider are not set in the test environment. The resolver chain is documented in the script's docstring — that is the source of truth.
