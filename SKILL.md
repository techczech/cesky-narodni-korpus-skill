---
name: cesky-narodni-korpus-skill
description: |
  Query the Czech National Corpus (korpus.cz / KonText) HTTP API from Claude — concordances, frequency distributions (token, structural, two-dimensional), collocations, corpus metadata, and subcorpus creation — over ~50 Czech corpora (SYN family, oral_v3, the InterCorp parallel corpora, and more). Authenticates with a Personal Access Token via a configurable resolver chain (env var / file / macOS Keychain / Bitwarden Secrets Manager).

  TRIGGERS: Use when:
  - User asks to set up, install, or configure korpus.cz, ČNK, KonText, or "Český národní korpus" access
  - User mentions a korpus.cz Personal Access Token, a `kontext-api` URL, or wants to authenticate against the Czech National Corpus
  - User wants to run corpus-linguistics tasks against Czech corpora (SYN2020, SYN2015, oral_v3, InterCorp, etc.) from inside Claude
  - User asks for Czech concordances, frequency lists, or collocations and references korpus.cz / ČNK / KonText / Lindat
  - User wants to write or run CQL queries against Czech corpora via an agent

  DO NOT trigger for:
  - Sketch Engine corpora (English BNC, COCA, EnTenTen, Hansard, etc.) — see sketchengine-skill
  - Generic corpus-linguistics work in Czech that doesn't involve korpus.cz — local NLTK / spaCy / CWB workflows belong elsewhere
  - The Lindat KonText instance (kontext.korpus.cz redirects from CNC, but the tokens here authenticate the CNC instance only)
---

# Český národní korpus (korpus.cz / KonText) skill

Query the Czech National Corpus over its HTTP API. Authentication is by Personal Access Token; calls go to `https://www.korpus.cz/kontext-api/v0.17/`.

## Source-of-truth notes

- API base: `https://www.korpus.cz/kontext-api/v0.17/`
- Auth: POST a Personal Access Token to `https://www.korpus.cz/login`, receive a session cookie (`cnc_toolbar_sid`), use that cookie on every subsequent call. **Not stateless Bearer auth.**
- Rate limits at retrieval (2026-04-30): **12 requests/minute, 5000/day** for the CNC KonText instance. Treq is more generous (30/min). Hitting the limit returns HTTP 429 → see `references/troubleshooting.md`.
- API version on the wire as of 2026-04-30: KonText 0.20.0; the API path is pinned at `v0.17` for backward compatibility.
- Upstream KonText source: <https://github.com/czcorpus/kontext> (GPL-2.0).
- Wiki: <https://wiki.korpus.cz/doku.php/en:manualy:api> (some pages require login).

## Mental model — two-phase querying

KonText is **stateful within a session**. Almost every operation runs against an existing *concordance ID*, not against the raw corpus:

1. **Submit a CQL query → get a concordance ID** (`POST /query_submit`). The ID looks like `~gUgICee6K2ka` and is persistent — you can re-use it for hours/days without re-running the query.
2. **Ask questions of that concordance**:
   - `GET /view` — paginated KWIC lines
   - `GET /freqs` — token-level frequency distribution (e.g. "what lemmas appear in this concordance?")
   - `GET /freqtt` — structural-attribute frequency distribution (e.g. "across what genres?")
   - `GET /freqct` — two-dimensional cross-tab (e.g. "lemma × genre")
   - `GET /collx` — collocation profile in a context window

So `concordance` always comes first; `freq` / `collocations` / etc. take the `q` ID returned by it. The bundled `korpuscz_client.py` exposes both phases.

## Setup workflow

The user always needs three things before queries work: (1) a CNC account with a Personal Access Token, (2) a way for the script to read that token, (3) Python 3.10+ on PATH.

### Step 1 — get the token

1. Sign up at <https://www.korpus.cz/signup> (free; institutional access also possible).
2. Log in, click your username (top-right) → user profile → **Personal access tokens** section.
3. Click **New**, pick a validity, click **OK**, copy the token. **It is shown only once** — if lost, generate a new one.

### Step 2 — make the token available to the script

The `scripts/korpuscz_client.py` resolves the token from the first available source in this chain:

| Priority | Source | How to set up |
|----|----|----|
| 1 | `--token <value>` CLI flag | one-off testing only — visible in process listings, do not script |
| 2 | `$KORPUSCZ_API_TOKEN` env var | `export KORPUSCZ_API_TOKEN=...` in your shell rc |
| 3 | file at `$KORPUSCZ_TOKEN_FILE` | put the token in a chmod-600 file, `export KORPUSCZ_TOKEN_FILE=~/.config/korpuscz/token` |
| 4 | macOS Keychain at `$KORPUSCZ_KEYCHAIN_SERVICE` | `security add-generic-password -s korpuscz -a "$USER" -w` then `export KORPUSCZ_KEYCHAIN_SERVICE=korpuscz` |
| 5 | Bitwarden Secrets Manager via `$KORPUSCZ_BWS_PROJECT` + `$KORPUSCZ_BWS_KEY` | requires `bws` CLI and a stored `BWS_ACCESS_TOKEN`; see [the BWS path](#optional-bitwarden-secrets-manager-bws-path) below |

Each provider is opt-in — the skill stays portable across environments without picking a favourite. Pick the one that matches your machine. Most users on a single machine should set `KORPUSCZ_API_TOKEN` directly and move on.

#### Optional: Bitwarden Secrets Manager (BWS) path

If the user already manages other project tokens through [Bitwarden Secrets Manager](https://bitwarden.com/help/secrets-manager-cli/), they can store the korpus.cz token there too. The skill never depends on BWS — it just *supports* it.

```bash
# One-time: create the project (if absent) and store the token
PROJECT_ID="$(bws project create korpus-cz --output json | jq -r .id)"
bws secret create KORPUSCZ_API_TOKEN "<your-token>" "$PROJECT_ID"

# Per shell (or in ~/.zshrc): point the client at the BWS slot by name —
# the script resolves the IDs at runtime
export KORPUSCZ_BWS_PROJECT="korpus-cz"
export KORPUSCZ_BWS_KEY="KORPUSCZ_API_TOKEN"
```

The script then calls `bws secret get` at runtime — token rotation becomes one `bws secret edit --value <new> <secret-id>` call, no environment refresh needed.

### Step 3 — verify

```bash
python3 scripts/korpuscz_client.py login
# {"ok": true, "token_source": "...", "cookies_received": ["cnc_toolbar_sid"]}

python3 scripts/korpuscz_client.py corpus-info syn2020
# Large JSON: text_types_data, structural attrs, etc.
```

If `login` works but `corpus-info` returns HTML or 403, the token authenticated but the account doesn't have the corpus licensed. See `references/troubleshooting.md`.

## Common task patterns

The bundled script exposes one subcommand per API endpoint. Claude will route natural-language asks to the right subcommand; the user never has to memorise these. For the standard patterns and example invocations, see [`references/tasks.md`](references/tasks.md).

For CQL specifically (Sketch Engine and KonText share the dialect, but morphological tagsets differ across Czech corpora), see [`references/cql.md`](references/cql.md).

For corpus selection (which of the ~50 corpora hosted at korpus.cz to use for which question — SYN family for written modern Czech, oral_v3 for spoken, InterCorp for parallel, DIAKORP for diachronic, etc.), see [`references/corpora.md`](references/corpora.md).

For exact endpoint specs (every parameter, every response field), see [`references/api.md`](references/api.md).

For analysis-ready output formats (CSV column schemas for downstream pandas / R / Excel work), see [`references/output-formats.md`](references/output-formats.md). When the user signals they want to do their own analysis — mentions of *pandas, R, Excel, structured data, machine readable, for charts, for visualisation later* — **always pass `--csv-out PATH`** alongside the JSON. Every table-returning subcommand supports it; the JSON still goes to stdout, the CSV is a flattening of it, and there is no extra API cost.

## When things break

For full triage, see [`references/troubleshooting.md`](references/troubleshooting.md). Quick pointers:

- **`Login returned 200 but no session cookie was set`** — token is invalid, expired, or revoked. Generate a new one on the user profile page; re-store it via whichever provider is configured.
- **`HTTP 429: Rate limited`** — back off. Limits are 12 req/min, 5000/day. The script does not throttle for you.
- **`HTTP 403` after a successful `login`** — the corpus is not licensed for this account. Some corpora are CNC-only; others require institutional registration.
- **Concordance `q` ID 404s after a few hours** — KonText archives queries but inactive ones are recomputed on demand; calling `view` again with the same `q` triggers the recompute. Just retry; the second call usually works.
- **Response too large** — payloads >64 KB are written to `cache/<label>-<hash>.json` and the path is returned to keep the LLM context window manageable. Override with `--full` if you need inline output.

## Out of scope

- Building or uploading custom corpora to korpus.cz — done via the CNC web UI's KonText, not the API.
- Treq parallel-corpus translation lookups — exposed by a separate API (`https://treq.korpus.cz/api/v1`); the script does not currently wrap it. A `references/treq.md` is a TODO if there's demand.
- LLM-mediated interpretation of results — the script returns JSON; interpretation happens in the conversation.
- The Lindat KonText instance (`https://lindat.mff.cuni.cz/services/kontext/`). It runs the same software, but tokens issued by korpus.cz do not authenticate against it. The script's `KORPUSCZ_API_BASE` and `KORPUSCZ_LOGIN_URL` env vars allow pointing at a different instance if a user has separate credentials.
