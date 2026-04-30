# Common task patterns — korpus.cz

Once the script can authenticate, these are the patterns the LLM will route to specific subcommands. The user can phrase requests naturally; this file is for the LLM's reference and for the user when they want to know what's possible.

All examples assume the script is at `scripts/korpuscz_client.py` from the skill root and that the token resolver has been configured per `SKILL.md` Step 2.

## Always probe a corpus before querying

```
Use korpuscz to show metadata for syn2020
```

→ `python3 scripts/korpuscz_client.py corpus-info syn2020`

This returns text-type categories (`doc.txtype_group`, `doc.txtype`, `doc.first_published`, etc.) and structural attributes you can later filter on. Useful first step for any "how is X used in modern Czech?" question — confirms the corpus exists, is licensed, and surfaces text-type values you can pass as `text_types` filters.

## Concordance — find a word/pattern in context

```
Find concordance lines for "pravda" in syn2020
Show how the lemma "ironie" is used in publicistika (news genre)
20 examples of "celý + lemma=pravda" in modern written Czech
```

→ `python3 scripts/korpuscz_client.py concordance syn2020 '[lemma="pravda"]'`

The script does the two-phase dance: submits the query to `/query_submit`, gets a `q` ID, then fetches a page from `/view`. The output includes `q` so subsequent frequency / collocation calls can re-use the concordance.

To get just the persistent `q` ID without rendering KWIC lines (useful before computing frequency or collocations on a large concordance):

→ `python3 scripts/korpuscz_client.py concordance syn2020 '[lemma="pravda"]' --id-only`

## Frequency by lemma (token-level)

```
What lemmas appear inside the concordance "celý + pravda"?
Show me the most frequent right collocates of "celý"
Top 50 lemmas in the concordance
```

→ first run `concordance --id-only` to get `~ABCDEF`, then:

→ `python3 scripts/korpuscz_client.py freq '~ABCDEF' --fcrit 'lemma/e 0~0>0' --fmaxitems 50`

`fcrit` syntax (inherited from the Manatee/Sketch Engine layer):

- `lemma/e 0~0>0` — lemma at position 0, exact match, single position, ascending frequency
- `word/i 0~0>0` — same but case-insensitive
- `tag/e 0~0>0` — POS tag at the KWIC position
- `lemma/e -1~-1>0` — lemma one token to the left of the KWIC

Multi-position criteria are written by chaining offsets: `lemma/e 0~0>0 lemma/e 1~1>0` for the (KWIC, KWIC+1) bigram surface.

## Frequency by genre / period / source (structural-attribute)

```
Across what genres does "ironie" appear in syn2020?
Distribution of "X" by publication year
```

→ `python3 scripts/korpuscz_client.py freqtt '~ABCDEF' --fttattr doc.txtype_group --flimit 1`

For period: `--fttattr doc.first_published`. For source/medium: try `doc.medium`, `doc.publisher`, depending on the corpus — check `corpus-info` output for what is available in each corpus.

## Two-dimensional cross-tabulation

```
Cross-tab "lemma" by genre — what lemmas dominate which text type?
"verb tense × text type" cross-tab in publicistika
```

→ `python3 scripts/korpuscz_client.py freqct '~ABCDEF' --ctfcrit1 'lemma 0<0' --ctfcrit2 'doc.txtype_group 0' --ctminfreq 5`

Useful for: discourse analysis, register comparison, contrastive lexicography. The 4th column of each row is the base set size for ipm — see `references/api.md` for what that number means in the three different combinations of positional / structural dimensions.

## Collocations

```
Top collocates of "demokracie" in oral_v3
What words co-occur with "ironie" within ±5 tokens?
```

→ submit a single-token concordance, then:

→ `python3 scripts/korpuscz_client.py collocations '~ABCDEF' --cattr lemma --cfromw -5 --ctow 5 --cminfreq 5 --cbgrfns d t m --csortfn d`

Defaults are tuned to typical lexicographic work: ±5 window, lemma-level, sorted by logDice. For closer syntactic collocates, narrow the window (`--cfromw -2 --ctow 2`); for broader thematic associates, widen (`-10 +10`).

When you want T-score / MI as well as logDice, pass `--cbgrfns d t m`. KonText returns all requested measures in `Items[i].Stats[i][N].s`.

## Subcorpus creation

```
Create a subcorpus of syn2020 covering only fiction (FIC: beletrie)
Make a subcorpus of articles published after 2010
```

→ `python3 scripts/korpuscz_client.py subcorpus-create syn2020 fiction-only --form-type cql --cql '[] within <doc txtype_group="FIC: beletrie"/>'`

`--form-type tt-sel --text-types-json '{"doc.txtype_group": ["FIC: beletrie"]}'` is also accepted.

After creation, pass the new subcorpus name to subsequent `concordance` calls via the `usesubcorp` parameter — but the script's `concordance` subcommand does not currently expose that flag (TODO; for now, edit the JSON body directly or add a `--usesubcorp` flag).

## Workflow patterns the LLM should know

### Reuse the `q` ID

KonText archives concordances. If the user already ran a concordance and asks a follow-up question ("now show me the collocations"), the right move is *not* to re-submit the query — it is to re-use the `q` ID that was returned. The skill keeps the ID in the session output explicitly. Look at the previous response, find `"q": "~..."`, and pass it to `freq`/`collocations`/`view`.

### Sanity-check counts with KWIC examples

When the user asks a quantitative question ("how often does *X* mean *Y*?"), numbers without examples mislead. After computing a frequency or collocation table, run a small `view --pagesize 20` to look at actual lines.

### Handle 429 by spacing calls

The CNC limit is 12 req/min. The two-phase model can chew through that quickly: every concordance + freq + collocations triplet is 3 calls, plus the one-time login. If you batch operations across multiple corpora in a single conversation, throttle to ~5-second gaps between sub-script calls.

### Pick the right corpus

For "modern written Czech", default to `syn2020` (the freshest large balanced reference). For spoken, `oral_v3`. For diachronic, the DIAKORP family. For parallel work, `intercorp_v15_<lang>`. Full map in [`corpora.md`](corpora.md). When in doubt, run `corpus-info <name>` first — if the corpus name is wrong or unlicensed, the response will tell you (HTTP 403 / empty Blocks).

### Subcorpus before re-running queries on a slice

If the user is going to ask several questions about the same slice ("compare Czech in fiction vs publicistika"), creating named subcorpora with `subcorpus-create` is faster across calls than passing `text_types` filters every time — KonText caches per-subcorpus.

### Producing analysis-ready CSV output

When the user mentions pandas, R, Excel, "structured data", "for charts", "machine readable", or "I want to do my own analysis", **always pass `--csv-out PATH` alongside the JSON**. Every table-returning subcommand supports it. The CSV column schemas are documented in [`output-formats.md`](output-formats.md). The JSON still goes to stdout (or cache/) — you get both.

```bash
python3 scripts/korpuscz_client.py concordance syn2020 '[lemma="demokracie" & tag="....3.*"]' \
  --pagesize 50 --csv-out demokracie_dative_kwic.csv > demokracie_dative.json
```

The `concordance` CSV gives you one row per KWIC line with `kwic_lemma`, `kwic_tag`, `left_text`, `right_text`, etc. The `collocations` CSV expands `Stats[]` into one column per measure (`logDice`, `T_score`, `MI`) — no need for the user (or LLM) to remember the cbgrfns order.

### Cross-tab on a single position of the positional tag

The `freqct` endpoint cannot project onto a substring of `tag` — you can't ask it for "case (position 5) × genre" directly. Workaround: cross-tab the *full* `tag` × the structural attribute, then aggregate the case character (or whichever position you care about) client-side.

```python
import pandas as pd
df = pd.read_csv("case_x_genre_full_tag.csv")
df["case"] = df["tag"].str[4]   # 0-indexed: position 5 is index 4
case_genre = df.groupby(["case", "doc.txtype_group"], as_index=False)["freq"].sum()
```

Document this once in your response so the user knows what they're getting; the JSON `attr1` field will still say `"tag"` even though they asked for case.

### When the user asks for morphological slices

Refer to [`cql.md`](cql.md) — the "CNC positional-tag cookbook" section gives copy-pasteable patterns for case, POS, negation, comparative degree, etc. Don't reconstruct from first principles when the cookbook has it.

### Throttling for batch workflows

When chaining several API calls in one shell session, pass `--throttle 6` (or higher) to keep within the 12-req/min CNC limit:

```bash
python3 scripts/korpuscz_client.py --throttle 6 concordance syn2020 '[lemma="demokracie"]' --quiet-id
python3 scripts/korpuscz_client.py --throttle 6 freq "$Q" --fcrit 'lemma/e 0~0>0'
```

The throttle is in-process per script invocation, so it only helps if you reuse a long-running invocation. Across separate `python3 scripts/korpuscz_client.py …` calls, `sleep 6` between calls is the cleaner approach.

### Warm a fresh `q` before non-`view` operations

Smoke-tested behaviour: a `q` returned by `concordance --quiet-id` (or `--id-only`) sometimes returns HTTP 500 with `"Q": []` on the first `freq` / `freqct` / `collx` call. A single `view` call against the `q` "warms" it and the subsequent operation succeeds. See [`troubleshooting.md`](troubleshooting.md). Recipe:

```bash
Q=$(python3 scripts/korpuscz_client.py concordance syn2020 '[lemma="demokracie" & tag="....3.*"]' --quiet-id)
python3 scripts/korpuscz_client.py view "$Q" --pagesize 1 > /dev/null   # warm
python3 scripts/korpuscz_client.py freqct "$Q" --ctfcrit1 'tag 0<0' --ctfcrit2 'doc.txtype_group 0' --ctminfreq 5
```
