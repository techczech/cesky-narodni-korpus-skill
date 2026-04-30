# korpus.cz corpora — what to use for what

The Czech National Corpus (Český národní korpus, ČNK) hosts ~50 corpora, organised by what they're for: synchronic written Czech (the SYN family), spoken Czech (the ORAL family), parallel corpora across languages (InterCorp), specialised corpora (web, academic, learner Czech, dialect), and diachronic / historical Czech (DIAKORP, manuscripts).

**Two things to know before using any name in this file:**

1. **API identifiers are lowercase** and usually match the corpus's URL slug at `https://www.korpus.cz/`. So "SYN2020" is `syn2020` on the wire.
2. **Some corpora are licensed separately.** Most are free to all CNC-registered users; a few (recent reference corpora, some parallel sources) require an extra agreement signed via the user profile. If a query returns HTTP 403 or empty results, the account does not have the corpus licensed — see `troubleshooting.md`.

For the canonical browser, including freshness dates and exact composition, see <https://www.korpus.cz/> (KonText) or the documentation portal at <https://wiki.korpus.cz/>.

## Modern written Czech — the SYN family

The SYN family is the workhorse for "what does modern Czech look like?". Two parallel reference series:

| Slug | Approx. size | What it's for |
|----|----|----|
| `syn2020` | 100M words | The current balanced reference for written contemporary Czech. Default first choice for any "modern written Czech" question. |
| `syn2015` | 100M words | Predecessor of SYN2020. Use when comparing with previously published research that cited SYN2015 specifically. |
| `syn2010` / `syn2009pub` / `syn2005` | 100M each | Older snapshots; useful only for diachronic work spanning the 2000s. |
| `syn` (or `syn_v13` etc.) | several billion | The cumulative SYN — every SYN snapshot stitched together. Largest available; less balanced. Use when raw scale matters more than genre balance. |
| `synv11`, `synv9`, `synv8` | varying | Versioned cumulative releases. Pick the most recent unless reproducing a specific paper. |

**`syn2020` is the right default unless the user has a specific reason otherwise.**

## Spoken Czech — ORAL family

| Slug | Approx. size | What it's for |
|----|----|----|
| `oral_v3` | ~6M words, balanced | Standard reference for spoken contemporary Czech. Sociolinguistically balanced (gender, age, region). Default for spoken questions. |
| `ortofon_v2` | ~1M words | Phonetically transcribed (orthographic + phonetic tier). Use for pronunciation / phonetic-variant work. |
| `dialekt`, `bmk`, `pmk` | small (<1M each) | Dialect / regional variety corpora. Use for variationist work. |

`oral_v3` is the modern default. The older `ORAL2013` / `ORAL2008` and the morphologically tagged `oral_v1` are still available and used in older publications.

## Web / informal Czech

| Slug | Approx. size | What it's for |
|----|----|----|
| `online_now` / `online_archive` | tens of billions | Web news, continuously updated. Use for currency-of-language questions ("when did *X* enter Czech?"). |
| `cstenten17_mj` (TenTen Czech 2017) | ~5B | Web Czech, broad coverage, less curated than SYN. Useful when SYN is too small or too formal. |

## Diachronic / historical Czech

| Slug | Span | What it's for |
|----|----|----|
| `diakorp_v6` | ca. 1300–1990 | Diachronic Czech, multi-period. The corpus most useful for tracking change over time. |
| `diakon` | older periods | Older diachronic snapshots — overlap with diakorp_v6 in many periods; check before choosing. |

## Specialised corpora

| Slug | What it's for |
|----|----|
| `koditex` | Czech school-textbook subcorpus across stages |
| `merlin_cs` | Learner Czech (CEFR-leveled) — for SLA research |
| `ksk` | Czech academic corpus |
| `dotko` | Children's literature in Czech |
| `lina` | A specialised literary subcorpus |

If unsure whether one of these is on the user's account, run `corpus-info <slug>`; HTTP 403 means licensing.

## Parallel corpora — InterCorp

InterCorp is the CNC's flagship parallel corpus, anchored on Czech. The naming scheme is `intercorp_v<release>_<lang>`:

- `intercorp_v15_en` — Czech ↔ English
- `intercorp_v15_de` — Czech ↔ German
- `intercorp_v15_fr`, `_es`, `_it`, `_pl`, `_ru`, `_pt`, `_nl`, `_sv`, `_no`, `_da`, `_fi`, `_lat`, `_grc`, etc.

Around 40 languages. The release number rolls forward annually-ish; pick the most recent (`v15` at retrieval) unless reproducing prior work.

For parallel queries, set `viewmode=align` and use the `aligned_corpora` field in `query_submit` (the script does not currently expose this flag — TODO; for now build the request body manually if you need it). Treq, exposed at `https://treq.korpus.cz/api/v1/`, is a separate translation-equivalent service that the script does not yet wrap.

## Choosing the right corpus — quick decision rules

- **Modern written Czech, no preference** → `syn2020`
- **Modern written Czech, biggest available** → `syn` (cumulative)
- **Spoken Czech** → `oral_v3` (balanced) or `ortofon_v2` (phonetic)
- **Web / news, very current** → `online_now`
- **Web, lots of words** → `cstenten17_mj`
- **Diachronic** → `diakorp_v6`
- **Learner Czech** → `merlin_cs`
- **Czech ↔ English parallel** → `intercorp_v15_en` (in `align` mode)

## When the corpus the user wants isn't on korpus.cz

Two paths:

1. **Upload a custom corpus through the CNC web UI** — supported for registered users with quotas applied to disk and processing time. The API does not currently expose corpus upload.
2. **Switch to a local toolkit** — NLTK/spaCy/CWB-based local processing if the data is yours and you don't need CNC's tagging pipeline. The skill cannot help with this; it is a different toolchain.
