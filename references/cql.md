# CQL for korpus.cz / KonText

CQL (Corpus Query Language) is the same dialect across NoSkE / Sketch Engine / KonText, but each platform's **POS / morph tagsets differ**, so the regex inside `tag="..."` patterns is corpus-specific. This file covers the parts that are universal, then the Czech-specific tagset details that matter for korpus.cz.

## Token-level patterns (universal)

A token is `[attribute="value"]`. Common attributes on Czech corpora:

```
[word="pravdou"]                  # exact word form
[lemma="pravda"]                  # lemma — covers all inflectional variants
[lc="pravdou"]                    # lowercased word form
[tag="N.FS7.*"]                   # POS tag, regex — feminine singular instrumental noun (see tag section below)
[pos="NOUN"]                      # universal POS (where supported — not on every Czech corpus)
```

Boolean combinators: `&` and, `|` or, `!` not.

```
[lemma="dělat" & tag="V.*"]       # the verb dělat (in CNC tagsets the verb form is also implied by the lemma — but explicit is safer)
[lemma="být" & !tag="VB.*"]       # být lemma but not the indicative finite forms
```

Regex on values:

```
[word=".*ová"]                    # words ending in -ová (often feminine surnames)
[lemma="dělat|činit|provádět"]    # any of three near-synonymous verbs
```

## Sequences

Tokens are juxtaposed; whitespace separates them.

```
[lemma="celý"] [lemma="pravda"]   # celá pravda + inflections (celou pravdu, celé pravdy, …)
```

## Quantifiers

```
[]                                # any one token
[]+                               # one or more
[]*                               # zero or more
[]?                               # zero or one
[]{0,3}                           # 0 to 3 tokens
```

Common applied patterns:

```
[lemma="být"] []{0,3} [tag="V.N.*"]                # být + ≤3 intervening tokens + past participle (analytic past)
[lemma="ne.*"] []{1,5} [lemma="ani"]               # ne... ani... (negation pattern), gap of 1–5 tokens
```

(Czech orthography keeps the negative prefix `ne-` attached to the verb, so `[lemma="ne.*"]` does not generally work for negation across word boundaries — see the tagset section below for proper finite-verb negation marking.)

## Anchoring within structure

Czech corpora use `<s>` for sentence, `<doc>` for document, `<p>` for paragraph, and frequently `<g/>` for token-glue (no space before next token).

```
"<s>" [lemma="však"]                              # sentence-initial však
[lemma="však"] within <s/>                        # však anywhere in a sentence
"<doc>" [lemma="opona"] within <p/>               # opona in the first paragraph of any document
```

## The Czech POS / morph tagset — corpus-specific

This is the part that varies the most. Two main tagsets you'll encounter on korpus.cz:

### CNC positional tagset (default for SYN family)

Each tag is a **15-character positional string**. Each position encodes a specific feature:

| Pos | Feature | Examples |
|----|----|----|
| 1 | POS | `N` noun, `A` adjective, `P` pronoun, `C` numeral, `V` verb, `D` adverb, `R` preposition, `J` conjunction, `T` particle, `I` interjection, `Z` punctuation |
| 2 | Sub-POS | (POS-dependent, e.g. `NN` common noun, `NP` proper noun) |
| 3 | Gender | `M` masc anim, `I` masc inanim, `F` fem, `N` neuter, `X` any |
| 4 | Number | `S` sg, `P` pl, `D` dual |
| 5 | Case | `1`–`7` (Czech grammatical case) |
| 6 | Possessor's gender | (rarely used) |
| 7 | Possessor's number | |
| 8 | Person | |
| 9 | Tense | |
| 10 | Degree | (comparative / superlative for adjectives, adverbs) |
| 11 | Negation | `A` affirmative, `N` negative |
| 12 | Voice | `A` active, `P` passive |
| 13 | Reserved | |
| 14 | Reserved | |
| 15 | Variant | (stylistic markers) |

Use `.` (regex any-char) for "any value at this position". Common queries:

```
[tag="NNF.*"]                                     # any feminine common noun
[tag="N.MS1.*"]                                   # masc-singular nominative noun
[tag="V.*"]                                       # any verb
[tag="V.....3....A.*"]                            # 3rd person singular active verb (positions 9 = present, 11 = active)
[tag=".........N.*"]                              # negated forms (position 11 = N)
[tag="A.....2.*"]                                 # comparative adjectives (position 10 = 2)
```

The exhaustive table is documented at <https://wiki.korpus.cz/doku.php/en:pojmy:tag>. To probe in ambiguous cases, run a small concordance with `--attrs word,lemma,tag` and read the actual tags returned.

### Universal Dependencies (UD) tags — newer corpora

Some corpora (notably the most recent SYN releases and InterCorp) carry both the positional tag (`tag`) and a UD tag (`pos` + structured features). When `pos` is available:

```
[pos="NOUN"]                                      # any noun
[pos="VERB" & feats="VerbForm=Fin|Mood=Ind"]      # finite indicative verb (when feats attribute is exposed)
```

When in doubt, prefer the positional `tag` — it's available on every Czech corpus.

## Worked patterns

### Idiomatic *celá pravda* and inflections

```
[lemma="celý"] [lemma="pravda"]
```

Catches all inflectional variants: celá pravda, celou pravdu, celé pravdy, …

### Verb + reflexive pronoun

```
[tag="V.*"] [word="se" | word="si"]
```

For the reflexive proper, you may want to filter on the dative/accusative reading; `word="se"` and `word="si"` cover both core forms.

### Passive-with-být

```
[lemma="být"] [tag="V.....X..R.A.*"]?  [tag="Vs.*"]
```

`Vs` is the passive participle prefix in many CNC tagsets. The `?` allows for an intervening adverb.

### Negation (positional tagset)

```
[tag=".........N.*"]
```

Position 11 = N marks negative finite verbs (`nevidím`, `nedělá`).

### Foreign borrowings ending in -ing

```
[word=".*ing" & tag="N.*"]
```

Useful for tracking Anglicism uptake.

## Output controls and CQL together

CQL only describes *what to match*. The script's CLI flags configure the rest:

- Page size, starting page → `--pagesize`, `--fromp` on `view`
- Context width on collocations → `--cfromw`, `--ctow`
- Sort order on collocations → `--csortfn`
- Subcorpus filter — currently set inside the `query_submit` JSON body (`usesubcorp`); not yet a CLI flag. Edit the script body or pass `--text-types-json` for one-off filters at submit time.

## When CQL is the wrong tool

- Frequency lists across a whole corpus (no concordance filter) — use `corpus-info` first to find a structural attribute, then a wildcard query like `[]` followed by `freqtt`. But this can return huge results.
- "Why does this word collocate with that word?" — that is *interpretive*; CQL only retrieves examples. The user (or Claude) reads the lines.

## CNC positional-tag cookbook — copy-pasteable patterns

The 15-character positional tag is fiddly to write from scratch. Below are patterns that match the most-asked-for morphological slices, ready to drop into a CQL token. Position numbering is 1-based per the wiki; in regex form each position is one `.` and the rest are wildcards. Keep the trailing `.*` so future-added positions don't break matches.

### By case (position 5)

| Slot | What it matches | Pattern |
|----|----|----|
| Nominative | Any nominative noun/adj/pron | `tag="....1.*"` (combine with POS: `tag="N...1.*"`) |
| Genitive   | Any genitive form           | `tag="....2.*"` |
| Dative     | Any dative form             | `tag="....3.*"` |
| Accusative | Any accusative form         | `tag="....4.*"` |
| Vocative   | Any vocative form           | `tag="....5.*"` |
| Locative   | Any locative form           | `tag="....6.*"` |
| Instrumental | Any instrumental form     | `tag="....7.*"` |

So **dative-only `demokracie`**: `[lemma="demokracie" & tag="....3.*"]` (any number/gender/POS, just case=3). To narrow to just the singular feminine noun in dative: `[lemma="demokracie" & tag="N.FS3.*"]`.

### By POS + sub-features (position 1, sometimes 11)

| Slot | What it matches | Pattern |
|----|----|----|
| Any noun | All nouns | `tag="N.*"` |
| Any verb | All verb forms | `tag="V.*"` |
| Any adjective | All adjectives | `tag="A.*"` |
| Verb, finite + indicative | Excludes infinitive, participle, transgressive | `tag="V[Bp].*"` (B = finite, p = past) |
| Negated finite verb | Position 11 = N | `tag="V.........N.*"` |
| Comparative adjective | Position 10 = 2 | `tag="A........2.*"` |
| Passive participle | Sub-POS = `Vs` | `tag="Vs.*"` |
| Past participle (active, l-form) | Sub-POS = `Vp` | `tag="Vp.*"` |

### Number × case combos

| Slot | Pattern |
|----|----|
| Feminine singular nominative noun | `tag="N.FS1.*"` |
| Masculine plural genitive noun | `tag="N.MP2.*"` |
| Any plural | `tag="...P.*"` |
| Any singular | `tag="...S.*"` |

### Lexical-family / lemma-pattern queries

When the user asks for "verbs in the same family as X":

```
[lemma="demokr.*" & tag="V.*"]    # all verb forms whose lemma starts demokr-
[lemma="(de)?mokrat.*"]            # demokrat- and the prefix-shifted form, any POS
```

The CNC lemma list does NOT use `demokracie` as a verb lemma — verbs sharing the root are lemmatised under `demokratizovat`, `zdemokratizovat`, etc. So `[lemma="demokracie" & tag="V.*"]` will match zero (correctly). The lemma-prefix pattern is the right move.

### Reference

- KonText / NoSkE CQL reference: <https://www.sketchengine.eu/documentation/corpus-querying/> (same dialect)
- CNC positional tagset: <https://wiki.korpus.cz/doku.php/en:pojmy:tag>
- IMS CWB CQP manual (the spec CQL inherits from): <https://cwb.sourceforge.io/files/CQP_Manual/>
