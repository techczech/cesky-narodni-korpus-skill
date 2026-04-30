# Output formats — what the script returns and how to feed downstream tools

Every table-returning subcommand of `korpuscz_client.py` emits **two** kinds of output:

1. **JSON** to stdout (or to `cache/<label>-<hash>.json` when the payload exceeds 64 KB). This is the live, lossless API response — every field, every nested structure. Use it when you need full provenance, structural attributes, the `pfilter` / `nfilter` operands for chained queries, or anything the CSV doesn't carry.
2. **CSV** to a path you specify with `--csv-out PATH`. One row per item, explicit columns, ready for `pandas.read_csv()`, `readr::read_csv()`, Excel, or whatever spreadsheet you like. The CSV is **a flattening of the JSON**, not a separate API call — there is no extra query cost.

When the user asks for analysis-ready data (anything mentioning pandas, R, Excel, charts, "for visualisation later", "structured", "machine readable"), pass `--csv-out` alongside the JSON.

## CSV column schemas

### `concordance` and `view` — KWIC lines

One row per concordance line.

| Column | Type | Source |
|----|----|----|
| `linenum` | int | `Lines[i].linenum` |
| `doc_title` | string | `Lines[i].ref[0]` (the first reference, usually `doc.title`) |
| `left_text` | string | space-joined `Lines[i].Left[].str` |
| `kwic_text` | string | space-joined `Lines[i].Kwic[].str` |
| `kwic_lemma` | string | space-joined first element of each `Lines[i].Kwic[].tail_posattrs` |
| `kwic_tag` | string | space-joined second element of each `Lines[i].Kwic[].tail_posattrs` |
| `right_text` | string | space-joined `Lines[i].Right[].str` |
| `left_lemmas` | string | space-joined lemmas of every left-side token |
| `left_tags` | string | space-joined tags of every left-side token |
| `right_lemmas` | string | space-joined lemmas of every right-side token |
| `right_tags` | string | space-joined tags of every right-side token |

The `kwic_*` fields are convenient when the KWIC is a single word; for multi-token KWICs they hold space-joined lists. The `*_lemmas` / `*_tags` columns mirror the surface-form columns at the same token positions, so a downstream pipeline can re-zip them.

`tail_posattrs` only populates when the request includes the right `attrs`. The bundled `concordance` and `view` subcommands default to `--attrs word,lemma,tag` so this is automatic; if you override `--attrs` and exclude `lemma` or `tag`, the CSV columns for those will be empty.

### `freq` and `freqtt` — frequency tables

| Column | Type | Source |
|----|----|----|
| `value` | string | `Blocks[0].Items[i].Word[0].n` |
| `freq` | int | `Blocks[0].Items[i].freq` |
| `ipm` | float | `Blocks[0].Items[i].rel` (instances per million) |

### `freqct` — two-dimensional cross-tab

One row per cell of the cross-tab. Column names use the actual attribute names from the response.

| Column | Type | Source |
|----|----|----|
| `<attr1>` | string | `data.data[i][0]` |
| `<attr2>` | string | `data.data[i][1]` |
| `freq` | int | `data.data[i][2]` |
| `base_size` | int | `data.data[i][3]` (the ipm denominator; `1000000` means "not applicable") |

### `collocations` — collocate ranking

| Column | Type | Source |
|----|----|----|
| `collocate` | string | `Items[i].str` |
| `freq` | int | `Items[i].freq` |
| (one column per measure) | float | `Items[i].Stats[k].s`, with the column **named after the measure** (`logDice`, `T_score`, `MI`, etc.) read from the response `Head` |

The measure-column names come from the live API response, not from `--cbgrfns` codes — so the CSV header tells the analyst which numbers are which without consulting the source. Spaces and hyphens in measure labels become underscores (`T-score` → `T_score`).

## Recipes

### Pandas

```python
import pandas as pd
conc = pd.read_csv("conc.csv")
freq = pd.read_csv("freq.csv")
coll = pd.read_csv("coll.csv")  # logDice etc. already typed as floats
```

### R

```r
library(readr)
conc <- read_csv("conc.csv")
coll <- read_csv("coll.csv")
```

### Spreadsheet

Both Excel and Numbers open the CSVs directly. The collocation CSV's named measure columns sort and chart cleanly.

## When to fall back to JSON

The CSV intentionally drops some things. Reach for the JSON when you need:

- **`pfilter` / `nfilter` operands** to build chained queries (`q2` strings)
- **Per-token positional attributes** in their original split form (the CSV joins them with spaces)
- **Concordance metadata**: `concsize`, `result_arf` (Average Reduced Frequency), `result_relative_freq`, `aligned_corpora`
- **Structural attributes** beyond `doc.title` — fetch them via `--refs` and read from the JSON's `ref[]` array
- **Sub-corpus context** — the `text_types_data` block returned alongside many endpoints
- **Pagination state** — `pagination.{firstPage, prevPage, nextPage, lastPage}` for walking large concordances

For most analytical workflows the CSV is enough; the JSON is there when it isn't.
