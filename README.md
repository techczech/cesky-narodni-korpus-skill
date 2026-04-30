# cesky-narodni-korpus-skill

A Claude Code / Claude Desktop skill for the [Czech National Corpus](https://www.korpus.cz/) (Český národní korpus, ČNK). Authenticates against the KonText HTTP API with a Personal Access Token and runs concordances, frequency distributions, collocations, corpus introspection, and subcorpus creation against the ~50 corpora hosted by CNC.

Pluggable token resolution: works with a plain env var, a chmod-600 file, the macOS Keychain, or [Bitwarden Secrets Manager](https://bitwarden.com/products/secrets-manager/) — pick whichever matches your machine. The skill itself takes no opinion.

## Status

End-to-end smoke-tested against the CNC instance on **2026-04-30** with a personal account. Working: login, `corpus-info`, `concordance`, `view`, `freq`, `freqtt`, `freqct`, `collocations`, `subcorpus-create`. Not yet wrapped: Multi-level token frequency distribution, Treq translation lookups, the `aligned_corpora` field for parallel queries — PRs welcome.

## What this skill does

When installed in your Claude environment, the skill triggers when you mention setting up korpus.cz / ČNK / KonText, or running corpus-linguistics tasks against Czech corpora.

It walks the user through:

1. Generating a Personal Access Token via the user profile at <https://www.korpus.cz/>.
2. Configuring one of five token-resolution methods (env var / file / Keychain / Bitwarden Secrets Manager / explicit `--token` flag).
3. Verifying the connection with a smoke test (`korpuscz login`, then `corpus-info syn2020`).

Once authenticated, the skill's reference files (`tasks.md`, `cql.md`, `corpora.md`, `api.md`, `troubleshooting.md`) help Claude pick the right operation, choose the right corpus, write the right CQL, and recover from common failure modes.

## Install (as a Claude / Codex skill)

Clone into the host's skill discovery path:

```bash
# Claude Code (user scope)
git clone https://github.com/techczech/cesky-narodni-korpus-skill ~/.claude/skills/cesky-narodni-korpus-skill

# Codex
git clone https://github.com/techczech/cesky-narodni-korpus-skill ~/.codex/skills/cesky-narodni-korpus-skill
```

The skill self-registers when the host scans the folder. To package as a `.skill` archive for distribution, use Anthropic's [skill-creator](https://github.com/anthropics/skills) tooling.

## Token setup — pick one path

Each path is opt-in. The script's resolver chain checks them in this order; the first one that yields a non-empty value wins. **Most users only need #2.**

### 1. `--token` CLI flag (testing only)

```bash
python3 scripts/korpuscz_client.py --token "<paste>" login
```

Visible in process listings — fine for one-off probing, do not script.

### 2. `KORPUSCZ_API_TOKEN` env var (the default everywhere)

```bash
echo 'export KORPUSCZ_API_TOKEN="..."' >> ~/.zshrc  # or ~/.bashrc
source ~/.zshrc
```

Works on Linux, macOS, and Windows (WSL) without any extra tooling.

### 3. `KORPUSCZ_TOKEN_FILE` — a chmod-600 file

```bash
mkdir -p ~/.config/korpuscz
echo "<your-token>" > ~/.config/korpuscz/token
chmod 600 ~/.config/korpuscz/token
echo 'export KORPUSCZ_TOKEN_FILE=~/.config/korpuscz/token' >> ~/.zshrc
```

Useful when you want the token in a file but not in shell history.

### 4. `KORPUSCZ_KEYCHAIN_SERVICE` — macOS Keychain

```bash
security add-generic-password -s korpuscz -a "$USER" -w "<your-token>"
echo 'export KORPUSCZ_KEYCHAIN_SERVICE=korpuscz' >> ~/.zshrc
```

Native macOS storage; survives reboots, encrypted at rest.

### 5. `KORPUSCZ_BWS_PROJECT` + `KORPUSCZ_BWS_KEY` — Bitwarden Secrets Manager

For users already on the [Bitwarden Secrets Manager CLI](https://bitwarden.com/help/secrets-manager-cli/):

```bash
# one-time: create the project (if it does not exist) and store the token
PROJECT_ID="$(bws project create korpus-cz --output json | jq -r .id)"
bws secret create KORPUSCZ_API_TOKEN "<your-token>" "$PROJECT_ID"

# per shell — point the client at the BWS slot (uses names, the script
# resolves the IDs at runtime)
export KORPUSCZ_BWS_PROJECT="korpus-cz"
export KORPUSCZ_BWS_KEY="KORPUSCZ_API_TOKEN"
```

Requires the `bws` CLI (`brew install bitwarden/tap/bws`) and a `BWS_ACCESS_TOKEN` either in the environment or in macOS Keychain (under service `BWS_ACCESS_TOKEN`). Token rotation becomes one `bws secret edit --value <new>` call — no shell refresh needed.

### Adding a sixth provider

The resolver chain is `scripts/korpuscz_client.py:resolve_token`. Adding a new path (1Password CLI, gopass, HashiCorp Vault, etc.) means appending one branch and documenting the env vars. PRs welcome.

## Verify

```bash
python3 scripts/korpuscz_client.py login
# {"ok": true, "token_source": "...", "cookie_file": "cache/session_cookies.txt", ...}

python3 scripts/korpuscz_client.py corpus-info syn2020
# Large JSON: text_types_data, structural attrs, etc.

python3 scripts/korpuscz_client.py concordance syn2020 '[lemma="pravda"]' --pagesize 5
# {"q": "~...", "Lines": [...], "concsize": ..., ...}
```

## Layout

```
SKILL.md                           # the skill definition Claude reads
README.md                          # this file
LICENSE                            # MIT
scripts/
  korpuscz_client.py               # CLI: login, corpus-info, concordance, view,
                                   # freq, freqtt, freqct, collocations,
                                   # subcorpus-create — stdlib only, no deps
references/
  api.md                           # exact endpoint specs (every parameter, every field)
  tasks.md                         # common task patterns
  cql.md                           # CQL cheatsheet, including CNC positional tagset
  corpora.md                       # the ~50 CNC corpora and what to use them for
  troubleshooting.md               # diagnostic recipes
cache/                             # session cookies + cached large responses (gitignored)
```

## Background

Generated as a sister to [`sketchengine-skill`](https://github.com/techczech/sketchengine-skill) for users whose primary corpus access is the Czech National Corpus rather than Sketch Engine. Where Sketch Engine focuses on global / English-leaning data, korpus.cz is the canonical source for Czech: SYN family for written, ORAL family for spoken, InterCorp for parallel, DIAKORP for historical.

The HTTP API is documented at:

- <https://wiki.korpus.cz/doku.php/en:manualy:api> — CNC-specific notes (login flow, rate limits)
- <https://github.com/czcorpus/kontext/wiki/HTTP-API> — generic KonText endpoint specifications

## License

MIT.
