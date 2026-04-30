# korpus.cz / KonText HTTP API — endpoint reference

This is a working reference distilled from the upstream KonText wiki (<https://github.com/czcorpus/kontext/wiki/HTTP-API>) plus the CNC-specific notes at <https://wiki.korpus.cz/doku.php/en:manualy:api>. Documented endpoints as of 2026-04-30.

## Base URL & auth recap

- **API base**: `https://www.korpus.cz/kontext-api/v0.17/`
- **Login URL**: `https://www.korpus.cz/login`
- **Auth flow**: POST `personal_access_token=<TOKEN>` (form-encoded) → server returns a session cookie named `cnc_toolbar_sid`. Send that cookie on every API request. The CNC instance differs from a vanilla KonText install: every API endpoint requires login, and `format=json` is the default for actions that also have an HTML view.
- **Rate limit**: 12 req/min, 5000/day. Exceeding returns HTTP 429.

The bundled `scripts/korpuscz_client.py` handles login, cookie persistence, and 401/403 retry for you. The notes below are the underlying contracts for when you need to bypass the script (debugging, custom integrations).

## Endpoint summary

| HTTP | Path | Purpose |
|----|----|----|
| POST | `/query_submit?format=json` | Submit a CQL query, get a persistent concordance ID. |
| GET  | `/view` | Render concordance lines (KWIC / sentence / aligned). |
| GET  | `/query?corpname=X&format=json` | Get text-types and structural metadata for a corpus. |
| GET  | `/freqs` | Token-level frequency distribution over a concordance. |
| GET  | `/freqtt` | Structural-attribute frequency distribution. |
| GET  | `/freqct` | Two-dimensional frequency distribution (cross-tab). |
| GET  | `/collx` | Collocation profile in a context window. |
| POST | `/subcorpus/create?format=json` | Create a named subcorpus from a CQL/text-types/within definition. |

The Multi-level token frequency endpoint is mentioned in the wiki TOC but its page was empty at retrieval — not wrapped by the script. If you need it, inspect the network panel of <https://www.korpus.cz/kontext> while clicking the corresponding UI control.

---

## POST /query_submit — create a concordance

Submits a query and returns a persistent ID. Always use `qtype=advanced` (raw CQL) — the `simple` query type is meant for the GUI and has a complex internal expansion you do not want to recreate by hand.

**Minimal body:**

```json
{
  "type": "concQueryArgs",
  "queries": [
    {
      "qtype": "advanced",
      "corpname": "syn2020",
      "query": "[lemma=\"pravda\"]"
    }
  ]
}
```

**Full body (every property the wiki documents):**

```json
{
  "type": "concQueryArgs",
  "maincorp": "syn2020",
  "usesubcorp": null,
  "viewmode": "kwic",
  "pagesize": 40,
  "attrs": ["word","tag"],
  "attr_vmode": "visible-kwic",
  "base_viewattr": "word",
  "ctxattrs": [],
  "structs": ["text","p","g"],
  "refs": ["%3Ddoc.author"],
  "fromp": 0,
  "shuffle": 0,
  "queries": [
    {
      "qtype": "advanced",
      "corpname": "syn2020",
      "query": "[word=\"celou\"] [lemma=\"pravda\"]",
      "pcq_pos_neg": "pos",
      "include_empty": false,
      "default_attr": "word"
    }
  ],
  "text_types": {
    "doc.txtype_group": ["FIC: beletrie", "NMG: publicistika"]
  },
  "context": {
    "fc_lemword_wsize": [-5, 5],
    "fc_lemword": "",
    "fc_lemword_type": "all",
    "fc_pos_wsize": [-5, 5],
    "fc_pos": [],
    "fc_pos_type": "all"
  },
  "async": false
}
```

**Response (HTTP 201):**

```json
{
  "size": 110,
  "finished": true,
  "conc_args": { "...": "..." },
  "Q": ["~gUgICee6K2ka"],
  "conc_persistence_op_id": "gUgICee6K2ka"
}
```

The `conc_persistence_op_id` is what every other endpoint takes (with a leading `~`) as `q`. The `Q` array element already includes the `~`. Either form is acceptable in the script — internally it is normalised.

**Quirk: `refs` encoding.** Each `refs` entry needs an `=` prefix that must be URL-encoded as `%3D`. So adding the `doc.author` reference is `"%3Ddoc.author"`, not `"doc.author"`. This is historical and weird; budget for it.

---

## GET /view — render a concordance

| Param | Required? | Default | Notes |
|----|----|----|----|
| `q` | yes | — | concordance ID with leading `~`, e.g. `~gUgICee6K2ka` |
| `format` | yes | — | `json` or `xml` |
| `viewmode` | no | `kwic` | `kwic`, `sen`, `align` (parallel only) |
| `pagesize` | no | 40 | positive int |
| `fromp` | no | 1 | page number; line offset = `(fromp-1) * pagesize` |
| `attrs` | no | — | multi-value (`?attrs=word&attrs=lemma&...`) |
| `attr_vmode` | no | `visible-kwic` | `visible-all`/`visible-kwic`/`visible-multiline`/`mouseover` |
| `base_viewattr` | no | `word` | main attribute the line text is built from |
| `refs` | no | — | multi-value, with `%3D` prefix each (see above) |
| `structs` | no | — | structural attributes to display |

**Response (subset):**

```json
{
  "kwiclen": 2,
  "Lines": [
    {
      "Left": [{"str": "Tato", "class": "", "tail_posattrs": ["tento", "PDFS1----------"]}],
      "Kwic":  [{"str": "celá",  "class": "col0 coll", "tail_posattrs": ["celý", "AAFS1----1A----"]}],
      "Right": [{"str": "pravda", "class": "", "tail_posattrs": ["pravda", "NNFS1-----A----"]}]
    }
  ],
  "conc_persistence_op_id": "RSiw4GIgW08s",
  "concsize": 115,
  "result_arf": 51.31,
  "result_relative_freq": 0.94
}
```

`result_arf` is the *Average Reduced Frequency* — a CNC-flavoured stability metric that down-weights words concentrated in a single document. `result_relative_freq` is the standard ipm.

---

## GET /query?corpname=X — corpus metadata

Returns a large JSON describing text-types, available structural attributes, normalisation lists, etc. Useful as a discovery step before constructing a `text_types` filter.

Sample response keys: `text_types_data.Blocks[].Line[].name`, `Normslist`, `aligned_corpora`. There is no separate "list of corpora" endpoint — see <https://www.korpus.cz/kontext> for the human-facing browser.

---

## GET /freqs — token frequency distribution

Computes `lemma|word|tag` frequencies inside an existing concordance.

| Param | Notes |
|----|----|
| `q` | concordance ID |
| `fcrit` | criterion, e.g. `lemma/e 0~0>0` (lemma, exact, single position). Borrows the Sketch Engine grammar — see <https://www.sketchengine.eu/documentation/methods-documentation/> |
| `freq_type` | `tokens` |
| `fmaxitems` | cap on rows; pagination still possible by re-querying |
| `format` | `json` |

**Response shape:**

```json
{
  "Blocks": [{
    "Total": 12345,
    "Head":  ["value", "freq", "ipm"],
    "Items": [
      { "Word": [{"n": "pravda"}], "freq": 4321, "rel": 12.7 }
    ]
  }]
}
```

The outer `Blocks` is always length 1 — kept as an array for backward compatibility.

---

## GET /freqtt — structural-attribute distribution

Like `/freqs` but the criterion is a structural attribute (`doc.txtype_group`, `doc.first_published`, etc.), so you get one row per text-type value.

| Param | Notes |
|----|----|
| `q` | concordance ID |
| `fttattr` | dot-separated structure + attribute, e.g. `doc.txtype_group` |
| `ftt_include_empty` | `0` or `1`. With `1`, values with zero occurrences appear too. |
| `flimit` | minimum absolute freq filter |
| `format` | `json` |

Same `Blocks[].Items[]` shape as `/freqs`.

---

## GET /freqct — two-dimensional cross-tab

The 2D version: every (`attr1`, `attr2`) combination becomes a row. Both dimensions can be positional or structural.

| Param | Notes |
|----|----|
| `q` | concordance ID |
| `ctfcrit1` | dim 1: attr + range, e.g. `lemma 0<0` (lemma at position 0, sorted ascending) |
| `ctfcrit2` | dim 2 |
| `ctminfreq` | minimum-freq cutoff |
| `ctminfreq_type` | `abs` (absolute) / `pabs` (percentile of abs) / `ipm` / `pipm` |

**Response shape:**

```json
{
  "freq_type": "2-attribute",
  "attr1": "lemma",
  "attr2": "doc.txtype_group",
  "data": {
    "data": [
      ["pravda", "FIC: beletrie", 1247, 41591113],
      ["pravda", "NMG: publicistika", 982, 39966492]
    ]
  }
}
```

The 4th column is the base set size for ipm calculation: `1000000` means "not applicable" (structure × structure), the concordance size for positional × positional, or the subcorpus token count for positional × structural.

---

## GET /collx — collocations

Computes collocate ranking inside a context window of an existing concordance.

| Param | Notes |
|----|----|
| `q` | concordance ID |
| `cattr` | positional attribute for collocate values (`lemma`, `word`, `tag`...) |
| `cfromw` | left edge of window (negative, e.g. `-5`) |
| `ctow` | right edge (positive) |
| `cminfreq` | minimum collocate freq in the *whole corpus* |
| `cminbgr` | minimum collocate freq in the *window* |
| `cbgrfns` | multi-value, list of measures to compute |
| `csortfn` | which measure to sort by |
| `citemsperpage` / `collpage` | pagination |
| `format` | `json` |

**Measure codes:**

| Code | Measure |
|----|----|
| `t` | T-score |
| `m` | Pointwise mutual information |
| `3` | MI3 |
| `l` | log likelihood |
| `s` | minimum sensitivity |
| `d` | log Dice |
| `p` | MI − log f |
| `r` | relative frequency |

`d` (logDice) is the field default in modern KonText work — robust against rare items, stable across corpus sizes. The script defaults `--cbgrfns d t m` (logDice + T + MI) and sorts by logDice.

**Response shape (live, smoke-tested 2026-04-30):**

```json
{
  "Head": [
    {"n": ""},
    {"n": "Freq", "s": "f"},
    {"n": "logDice", "s": "d"},
    {"n": "T-score", "s": "t"},
    {"n": "MI", "s": "m"}
  ],
  "attrname": "lemma",
  "Items": [
    {
      "str": "mít",
      "freq": 8302,
      "Stats": [
        {"s": "8.451"},
        {"s": "88.983"},
        {"s": "5.417"}
      ]
    }
  ]
}
```

`Items[i].Stats` is **a flat array of `{s: "value"}`** in `cbgrfns` order. The wiki's `Items[i].Stats[i][0].s` notation does not match the live response — the `[0]` level does not exist. Read `Items[i].Stats[k].s` where `k` is the index of the measure you asked for in `cbgrfns`.

---

## POST /subcorpus/create — define a subcorpus

Create a named, persistent subcorpus from one of three filter types: `tt-sel` (text-type values), `within` (structural query), or `cql` (CQL filter against the corpus).

```json
{
  "corpname": "syn2015",
  "subcname": "s2015-books",
  "form_type": "cql",
  "cql": "[] within <doc medium = \"B: kniha\"/>",
  "description": null
}
```

Response:

```json
{
  "processed_subc": [{
    "ident": "d9ec8608-...",
    "label": "syn2015/s2015-books",
    "status": "PENDING",
    "category": "subcorpus"
  }]
}
```

Large subcorpora are processed asynchronously; status moves PENDING → READY (poll the user's subcorpus list page to confirm). Small ones may complete synchronously.

After creation, pass the subcorpus name to subsequent queries via `usesubcorp` in `query_submit` body.
