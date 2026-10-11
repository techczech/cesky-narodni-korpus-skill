#!/usr/bin/env python3
"""CLI for the Czech National Corpus (korpus.cz / KonText) HTTP API.

The CNC KonText API is session-cookie-based, not stateless: a Personal Access
Token is exchanged at POST /login for a session cookie which authenticates
every subsequent request. This client caches the cookie on disk and
re-authenticates only when needed, so token resolution happens once per
session rather than once per request.

Token resolution chain (first non-empty wins):

  1. --token <value>                    (explicit override; mostly for testing)
  2. $KORPUSCZ_API_TOKEN                (works everywhere)
  3. file at $KORPUSCZ_TOKEN_FILE       (one token per file, leading/trailing whitespace stripped)
  4. macOS Keychain generic-password
     under service $KORPUSCZ_KEYCHAIN_SERVICE
     (account = $USER)
  5. Bitwarden Secrets Manager:
     $KORPUSCZ_BWS_PROJECT + $KORPUSCZ_BWS_KEY  (uses the `bws` CLI; needs
     BWS_ACCESS_TOKEN in env or in Keychain under service BWS_ACCESS_TOKEN)

Each provider is opt-in via env var, so the skill stays portable: contributors
can wire any secret store without touching the script. The repo's README
documents one-time setup for each.

Output: JSON to stdout by default. Large payloads are written to
$KORPUSCZ_CACHE_DIR/<hash>.json and the path is returned, to avoid blowing up
the LLM context window.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = os.environ.get("KORPUSCZ_API_BASE", "https://www.korpus.cz/kontext-api/v0.17")
LOGIN_URL = os.environ.get("KORPUSCZ_LOGIN_URL", "https://www.korpus.cz/login")
USER_AGENT = "korpuscz-skill/0.1 (+https://github.com/techczech/cesky-narodni-korpus-skill)"

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR = Path(os.environ.get("KORPUSCZ_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
COOKIE_FILE = CACHE_DIR / "session_cookies.txt"

# CNC's published rate limit at retrieval (2026-04-30) is 12 req/min, 5000/day.
# We don't hard-enforce; we surface in error messages when 429 comes back.
LARGE_PAYLOAD_THRESHOLD_BYTES = int(os.environ.get("KORPUSCZ_LARGE_PAYLOAD_BYTES", "65536"))


class KorpusError(RuntimeError):
    """Raised for any user-facing failure (auth, network, API, parsing)."""


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def resolve_token(cli_token: str | None) -> tuple[str, str]:
    """Return (token, source_label). Raises KorpusError if no provider yields one."""
    if cli_token:
        return cli_token.strip(), "--token"

    env_token = os.environ.get("KORPUSCZ_API_TOKEN", "").strip()
    if env_token:
        return env_token, "$KORPUSCZ_API_TOKEN"

    token_file = os.environ.get("KORPUSCZ_TOKEN_FILE", "").strip()
    if token_file:
        try:
            value = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise KorpusError(f"KORPUSCZ_TOKEN_FILE={token_file!r} unreadable: {exc}") from exc
        if value:
            return value, f"file:{token_file}"

    keychain_service = os.environ.get("KORPUSCZ_KEYCHAIN_SERVICE", "").strip()
    if keychain_service:
        value = _read_macos_keychain(keychain_service)
        if value:
            return value, f"keychain:{keychain_service}"

    bws_project = os.environ.get("KORPUSCZ_BWS_PROJECT", "").strip()
    bws_key = os.environ.get("KORPUSCZ_BWS_KEY", "").strip()
    if bws_project and bws_key:
        value = _read_bws_secret(project=bws_project, key=bws_key)
        if value:
            return value, f"bws:{bws_project}/{bws_key}"

    raise KorpusError(
        "No korpus.cz token available. Set $KORPUSCZ_API_TOKEN, or configure one "
        "of: $KORPUSCZ_TOKEN_FILE, $KORPUSCZ_KEYCHAIN_SERVICE, or "
        "$KORPUSCZ_BWS_PROJECT + $KORPUSCZ_BWS_KEY. See README.md for setup."
    )


def _read_macos_keychain(service: str) -> str:
    if sys.platform != "darwin" or not shutil.which("security"):
        return ""
    completed = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", os.environ.get("USER", "")],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _read_bws_secret(*, project: str, key: str) -> str:
    binary = shutil.which("bws")
    if not binary:
        raise KorpusError(
            "$KORPUSCZ_BWS_PROJECT/$KORPUSCZ_BWS_KEY are set but `bws` is not on PATH. "
            "Install: brew install bitwarden/tap/bws"
        )
    env = os.environ.copy()
    if not env.get("BWS_ACCESS_TOKEN") and sys.platform == "darwin":
        keychain_value = _read_macos_keychain("BWS_ACCESS_TOKEN")
        if keychain_value:
            env["BWS_ACCESS_TOKEN"] = keychain_value
    if not env.get("BWS_ACCESS_TOKEN"):
        raise KorpusError(
            "BWS_ACCESS_TOKEN is not set and not in macOS Keychain (service "
            "'BWS_ACCESS_TOKEN', account $USER). Either export it in this shell, "
            "or store it in Keychain: "
            "security add-generic-password -s BWS_ACCESS_TOKEN -a \"$USER\" -w \"<token>\". "
            "See https://bitwarden.com/help/secrets-manager-cli/ for how to mint one."
        )
    proj = subprocess.run([binary, "project", "list"], capture_output=True, text=True, check=False, env=env)
    if proj.returncode != 0:
        raise KorpusError(f"bws project list failed: {proj.stderr.strip() or proj.stdout.strip()}")
    try:
        projects = json.loads(proj.stdout)
    except json.JSONDecodeError as exc:
        raise KorpusError(f"bws project list returned non-JSON: {exc}") from exc
    project_id = next((p["id"] for p in projects if p.get("name") == project), None)
    if not project_id:
        raise KorpusError(f"bws project {project!r} not found. Available: {[p.get('name') for p in projects]}")
    secrets = subprocess.run([binary, "secret", "list", project_id], capture_output=True, text=True, check=False, env=env)
    if secrets.returncode != 0:
        raise KorpusError(f"bws secret list failed: {secrets.stderr.strip() or secrets.stdout.strip()}")
    try:
        secret_list = json.loads(secrets.stdout)
    except json.JSONDecodeError as exc:
        raise KorpusError(f"bws secret list returned non-JSON: {exc}") from exc
    secret_id = next((s["id"] for s in secret_list if s.get("key") == key), None)
    if not secret_id:
        raise KorpusError(f"bws secret {key!r} not found in project {project!r}.")
    fetched = subprocess.run([binary, "secret", "get", secret_id], capture_output=True, text=True, check=False, env=env)
    if fetched.returncode != 0:
        raise KorpusError(f"bws secret get failed: {fetched.stderr.strip() or fetched.stdout.strip()}")
    try:
        return json.loads(fetched.stdout).get("value", "").strip()
    except json.JSONDecodeError as exc:
        raise KorpusError(f"bws secret get returned non-JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Session handling
# ---------------------------------------------------------------------------


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _build_opener(jar: http.cookiejar.MozillaCookieJar) -> urllib.request.OpenerDirector:
    handler = urllib.request.HTTPCookieProcessor(jar)
    opener = urllib.request.build_opener(handler)
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _load_cookie_jar() -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    if COOKIE_FILE.exists():
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (http.cookiejar.LoadError, OSError):
            pass
    return jar


def _save_cookie_jar(jar: http.cookiejar.MozillaCookieJar) -> None:
    _ensure_cache_dir()
    jar.save(ignore_discard=True, ignore_expires=True)
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass


def _has_session_cookie(jar: http.cookiejar.MozillaCookieJar) -> bool:
    return any(c.name in {"cnc_toolbar_sid", "session_id"} and c.value for c in jar)


def login(token: str) -> http.cookiejar.MozillaCookieJar:
    """Exchange the personal access token for a session cookie. Caches to disk."""
    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    opener = _build_opener(jar)
    body = urllib.parse.urlencode({"personal_access_token": token}).encode("utf-8")
    req = urllib.request.Request(
        LOGIN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        raise KorpusError(f"Login failed (HTTP {exc.code}): {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise KorpusError(f"Login transport error: {exc}") from exc
    if not _has_session_cookie(jar):
        raise KorpusError("Login returned 200 but no session cookie was set. Token may be invalid or revoked.")
    _save_cookie_jar(jar)
    return jar


def get_session(token: str | None = None, *, force_refresh: bool = False) -> http.cookiejar.MozillaCookieJar:
    """Return a usable cookie jar, logging in if the cached one is missing/expired."""
    if not force_refresh:
        jar = _load_cookie_jar()
        if _has_session_cookie(jar):
            return jar
    actual_token, _source = resolve_token(token)
    return login(actual_token)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


_LAST_REQUEST_TIME = 0.0
_THROTTLE_SECONDS = 0.0


def _request_json(
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    token: str | None = None,
    retry_on_auth: bool = True,
) -> dict[str, Any]:
    # Optional in-process throttle — sleep so calls are at least N seconds apart.
    # Useful when batching: CNC's published limit is 12 req/min (5s minimum).
    global _LAST_REQUEST_TIME
    if _THROTTLE_SECONDS > 0:
        wait = _THROTTLE_SECONDS - (time.time() - _LAST_REQUEST_TIME)
        if wait > 0:
            time.sleep(wait)
    _LAST_REQUEST_TIME = time.time()
    jar = get_session(token=token)
    return _do_request(method=method, path=path, params=params, json_body=json_body, jar=jar, token=token, retry_on_auth=retry_on_auth)


def _do_request(
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    jar: http.cookiejar.MozillaCookieJar,
    token: str | None,
    retry_on_auth: bool,
) -> dict[str, Any]:
    opener = _build_opener(jar)
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{_encode_params(params)}"
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403) and retry_on_auth:
            # Session likely expired — re-login once and retry.
            actual_token, _src = resolve_token(token)
            new_jar = login(actual_token)
            return _do_request(
                method=method, path=path, params=params, json_body=json_body,
                jar=new_jar, token=token, retry_on_auth=False,
            )
        if exc.code == 429:
            raise KorpusError(
                "Rate limited (HTTP 429). CNC limit at retrieval: 12 req/min, 5000 req/day. "
                "Slow the call cadence or wait."
            ) from exc
        raise KorpusError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise KorpusError(f"Network error contacting {url}: {exc}") from exc
    if "application/json" not in ct:
        raise KorpusError(f"Expected JSON from {url} but got Content-Type={ct}; body[:200]={raw[:200]!r}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KorpusError(f"Could not parse JSON from {url}: {exc}") from exc


def _encode_params(params: dict[str, Any]) -> str:
    """Encode query params, expanding list values into repeated keys."""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs, safe="~")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit_payload(payload: Any, *, label: str, force_inline: bool) -> None:
    """Print payload to stdout, or stash to cache/ if it's huge."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if force_inline or len(text.encode("utf-8")) <= LARGE_PAYLOAD_THRESHOLD_BYTES:
        print(text)
        return
    _ensure_cache_dir()
    digest = hashlib.sha256(f"{label}:{time.time()}".encode("utf-8")).hexdigest()[:12]
    cache_path = CACHE_DIR / f"{label}-{digest}.json"
    cache_path.write_text(text, encoding="utf-8")
    print(json.dumps({
        "cached": True,
        "reason": f"Response > {LARGE_PAYLOAD_THRESHOLD_BYTES} bytes; full JSON written to disk.",
        "path": str(cache_path),
        "size_bytes": len(text.encode("utf-8")),
        "preview_keys": list(payload.keys()) if isinstance(payload, dict) else None,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CSV emitters — analyst-friendly flat output for pandas / R / spreadsheets.
# Each emitter takes the live JSON response and writes one CSV row per item.
# Schemas documented in references/output-formats.md.
# ---------------------------------------------------------------------------


def _flatten_token_run(items: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Take a Left/Kwic/Right token list and return (text, lemma_concat, tag_concat).
    `tail_posattrs` is delimited by U+001F (information separator one) on the wire."""
    text_parts: list[str] = []
    lemma_parts: list[str] = []
    tag_parts: list[str] = []
    for tok in items or []:
        text_parts.append(tok.get("str", "").strip())
        tail = tok.get("tail_posattrs") or []
        lemma_str, tag_str = "", ""
        for raw in tail:
            # tail_posattrs format: "\x1flemma\x1ftag" or just "\x1flemma" depending on attrs
            cleaned = [p for p in raw.split("\x1f") if p]
            if cleaned:
                if not lemma_str:
                    lemma_str = cleaned[0]
                if len(cleaned) > 1 and not tag_str:
                    tag_str = cleaned[1]
        lemma_parts.append(lemma_str)
        tag_parts.append(tag_str)
    return " ".join(text_parts).strip(), " ".join(lemma_parts).strip(), " ".join(tag_parts).strip()


def _write_csv_concordance(payload: dict[str, Any], path: Path) -> int:
    """One row per KWIC line. Columns: linenum, doc_title, left_text, kwic_text,
    kwic_lemma, kwic_tag, right_text, left_lemmas, left_tags, right_lemmas, right_tags."""
    lines = payload.get("Lines", [])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "linenum", "doc_title", "left_text", "kwic_text", "kwic_lemma", "kwic_tag",
            "right_text", "left_lemmas", "left_tags", "right_lemmas", "right_tags",
        ])
        for line in lines:
            left_text, left_lemmas, left_tags = _flatten_token_run(line.get("Left", []))
            kwic_text, kwic_lemma, kwic_tag = _flatten_token_run(line.get("Kwic", []))
            right_text, right_lemmas, right_tags = _flatten_token_run(line.get("Right", []))
            ref = line.get("ref") or []
            doc_title = ref[0] if ref else ""
            w.writerow([
                line.get("linenum"), doc_title, left_text, kwic_text, kwic_lemma, kwic_tag,
                right_text, left_lemmas, left_tags, right_lemmas, right_tags,
            ])
    return len(lines)


def _write_csv_freq(payload: dict[str, Any], path: Path) -> int:
    """One row per frequency entry. Columns: value, freq, ipm.
    Works for /freqs and /freqtt (both use Blocks[0].Items)."""
    items = (payload.get("Blocks") or [{}])[0].get("Items", []) or []
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["value", "freq", "ipm"])
        for it in items:
            words = it.get("Word", [])
            value = words[0].get("n", "") if words else ""
            w.writerow([value, it.get("freq"), it.get("rel")])
    return len(items)


def _write_csv_freqct(payload: dict[str, Any], path: Path) -> int:
    """One row per cell of the 2D cross-tab. Columns: attr1_value, attr2_value, freq, base_size.
    `base_size` is the ipm denominator (1_000_000 = "not applicable" for structure x structure)."""
    rows = (payload.get("data") or {}).get("data", []) or []
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([payload.get("attr1", "attr1"), payload.get("attr2", "attr2"), "freq", "base_size"])
        for row in rows:
            if len(row) >= 4:
                w.writerow([row[0], row[1], row[2], row[3]])
    return len(rows)


def _write_csv_collocations(payload: dict[str, Any], path: Path, measure_codes: list[str]) -> int:
    """One row per collocate. Columns: collocate, freq, <one column per measure code>.
    Measure column names come from the live response Head, falling back to codes."""
    head = payload.get("Head", []) or []
    items = payload.get("Items", []) or []
    # Head[0] is empty (collocate column); Head[1] is freq; Head[2:] are the measures.
    measure_names: list[str] = []
    for h in head[2:]:
        name = h.get("n") or h.get("s") or ""
        # Normalise: "T-score" -> "t_score", "logDice" stays, "MI" stays.
        measure_names.append(name.replace(" ", "_").replace("-", "_") or "measure")
    if not measure_names:
        measure_names = list(measure_codes)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["collocate", "freq"] + measure_names)
        for it in items:
            row = [it.get("str"), it.get("freq")]
            for stat in (it.get("Stats") or []):
                # Convert numeric strings to floats where possible for analyst-friendly output.
                v = stat.get("s") if isinstance(stat, dict) else None
                try:
                    row.append(float(v))
                except (TypeError, ValueError):
                    row.append(v)
            w.writerow(row)
    return len(items)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    actual_token, source = resolve_token(args.token)
    jar = login(actual_token)
    cookies = [c.name for c in jar]
    print(json.dumps({
        "ok": True,
        "token_source": source,
        "cookie_file": str(COOKIE_FILE),
        "cookies_received": cookies,
    }, indent=2))
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    jar = get_session(token=args.token)
    cookies = [c.name for c in jar]
    print(json.dumps({
        "session_cached": _has_session_cookie(jar),
        "cookie_file": str(COOKIE_FILE),
        "cookies": cookies,
        "api_base": API_BASE,
    }, indent=2))
    return 0


def cmd_corpus_info(args: argparse.Namespace) -> int:
    payload = _request_json(method="GET", path="/query", params={"corpname": args.corpus, "format": "json"}, token=args.token)
    _emit_payload(payload, label=f"corpus-{args.corpus}", force_inline=args.full)
    return 0


def cmd_concordance(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {
        "type": "concQueryArgs",
        "maincorp": args.corpus,
        "viewmode": args.viewmode,
        "pagesize": args.pagesize,
        "attrs": args.attrs.split(",") if args.attrs else ["word"],
        "structs": args.structs.split(",") if args.structs else [],
        "refs": [f"%3D{r}" for r in args.refs.split(",")] if args.refs else [],
        "queries": [{
            "qtype": "advanced",
            "corpname": args.corpus,
            "query": args.query,
            "default_attr": args.default_attr,
        }],
        "async": False,
    }
    if args.usesubcorp:
        body["usesubcorp"] = args.usesubcorp
    submit = _request_json(method="POST", path="/query_submit", params={"format": "json"}, json_body=body, token=args.token)
    conc_id = submit.get("conc_persistence_op_id")
    if not conc_id:
        raise KorpusError(f"query_submit returned no conc_persistence_op_id: {submit}")
    if args.quiet_id:
        # Shell-friendly: just `~XYZ`, nothing else. Useful for `Q=$(... --quiet-id)`.
        print(f"~{conc_id}")
        return 0
    if args.id_only:
        print(json.dumps({"q": f"~{conc_id}", "size": submit.get("size")}, indent=2))
        return 0
    view = _request_json(method="GET", path="/view", params={
        "q": f"~{conc_id}",
        "format": "json",
        "pagesize": args.pagesize,
        "fromp": args.fromp,
        "viewmode": args.viewmode,
        "attrs": args.attrs.split(",") if args.attrs else ["word"],
    }, token=args.token)
    view["q"] = f"~{conc_id}"
    view["concsize"] = view.get("concsize", submit.get("size"))
    if args.csv_out:
        n = _write_csv_concordance(view, Path(args.csv_out))
        view["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(view, label=f"conc-{args.corpus}", force_inline=args.full)
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    # Pass attrs through so tail_posattrs comes back populated — that's what
    # downstream callers (and the CSV emitter) need to extract lemma/tag per token.
    payload = _request_json(method="GET", path="/view", params={
        "q": args.q if args.q.startswith("~") else f"~{args.q}",
        "format": "json",
        "pagesize": args.pagesize,
        "fromp": args.fromp,
        "viewmode": args.viewmode,
        "attrs": args.attrs.split(",") if args.attrs else ["word", "lemma", "tag"],
        "structs": args.structs.split(",") if args.structs else None,
        "refs": [f"%3D{r}" for r in args.refs.split(",")] if args.refs else None,
    }, token=args.token)
    if args.csv_out:
        n = _write_csv_concordance(payload, Path(args.csv_out))
        payload["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(payload, label="view", force_inline=args.full)
    return 0


def cmd_freq(args: argparse.Namespace) -> int:
    payload = _request_json(method="GET", path="/freqs", params={
        "q": args.q if args.q.startswith("~") else f"~{args.q}",
        "fcrit": args.fcrit,
        "freq_type": "tokens",
        "fmaxitems": args.fmaxitems,
        "format": "json",
    }, token=args.token)
    if args.csv_out:
        n = _write_csv_freq(payload, Path(args.csv_out))
        payload["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(payload, label="freq", force_inline=args.full)
    return 0


def cmd_freqtt(args: argparse.Namespace) -> int:
    payload = _request_json(method="GET", path="/freqtt", params={
        "q": args.q if args.q.startswith("~") else f"~{args.q}",
        "fttattr": args.fttattr,
        "ftt_include_empty": 1 if args.include_empty else 0,
        "flimit": args.flimit,
        "format": "json",
    }, token=args.token)
    if args.csv_out:
        n = _write_csv_freq(payload, Path(args.csv_out))
        payload["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(payload, label="freqtt", force_inline=args.full)
    return 0


def cmd_freqct(args: argparse.Namespace) -> int:
    payload = _request_json(method="GET", path="/freqct", params={
        "q": args.q if args.q.startswith("~") else f"~{args.q}",
        "ctfcrit1": args.ctfcrit1,
        "ctfcrit2": args.ctfcrit2,
        "ctminfreq": args.ctminfreq,
        "ctminfreq_type": args.ctminfreq_type,
        "format": "json",
    }, token=args.token)
    if args.csv_out:
        n = _write_csv_freqct(payload, Path(args.csv_out))
        payload["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(payload, label="freqct", force_inline=args.full)
    return 0


def cmd_collocations(args: argparse.Namespace) -> int:
    payload = _request_json(method="GET", path="/collx", params={
        "q": args.q if args.q.startswith("~") else f"~{args.q}",
        "cattr": args.cattr,
        "cfromw": args.cfromw,
        "ctow": args.ctow,
        "cminfreq": args.cminfreq,
        "cminbgr": args.cminbgr,
        "cbgrfns": list(args.cbgrfns),
        "csortfn": args.csortfn,
        "citemsperpage": args.citemsperpage,
        "collpage": args.collpage,
        "format": "json",
    }, token=args.token)
    if args.csv_out:
        n = _write_csv_collocations(payload, Path(args.csv_out), measure_codes=list(args.cbgrfns))
        payload["csv_out"] = {"path": str(args.csv_out), "rows": n}
    _emit_payload(payload, label="collx", force_inline=args.full)
    return 0


def cmd_subcorpus_create(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {
        "corpname": args.corpus,
        "subcname": args.name,
        "form_type": args.form_type,
    }
    if args.description:
        body["description"] = args.description
    if args.cql:
        body["cql"] = args.cql
    if args.text_types_json:
        body["text_types"] = json.loads(args.text_types_json)
    payload = _request_json(method="POST", path="/subcorpus/create", params={"format": "json"}, json_body=body, token=args.token)
    _emit_payload(payload, label="subcorpus-create", force_inline=True)
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="korpuscz",
        description="Query the Czech National Corpus (korpus.cz / KonText) HTTP API.",
    )
    parser.add_argument("--token", help="Override token (default: resolve from env / file / Keychain / BWS).")
    parser.add_argument("--full", action="store_true", help="Always print full JSON inline; never write to cache/.")
    parser.add_argument("--throttle", type=float, default=0.0, metavar="SEC",
                        help="Minimum seconds between API calls within this process (CNC limit: 12 req/min ≈ 5s minimum).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="Probe the token and cache a session cookie.")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("whoami", help="Show the cached session state without re-logging-in.")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("corpus-info", help="Fetch metadata for a corpus (text-types, structures).")
    p.add_argument("corpus", help="Corpus identifier, e.g. syn2020, oral_v3, intercorp_v15_en.")
    p.set_defaults(func=cmd_corpus_info)

    p = sub.add_parser("concordance", help="Run a CQL query, fetch a concordance, return KWIC lines.")
    p.add_argument("corpus", help="Corpus identifier.")
    p.add_argument("query", help='CQL query, e.g. \'[lemma="pravda"]\'.')
    p.add_argument("--default-attr", default="word", help="Positional attribute used when CQL has bare quoted strings (default: word).")
    p.add_argument("--viewmode", default="kwic", choices=["kwic", "sen", "align"])
    p.add_argument("--pagesize", type=int, default=40)
    p.add_argument("--fromp", type=int, default=1)
    p.add_argument("--attrs", default="word,lemma,tag", help="Comma-separated positional attributes (default: word,lemma,tag).")
    p.add_argument("--structs", default="", help="Comma-separated structural attributes.")
    p.add_argument("--refs", default="doc.title", help="Comma-separated structure refs (default: doc.title).")
    p.add_argument("--usesubcorp", default="", help="Restrict the query to a named subcorpus.")
    p.add_argument("--id-only", action="store_true", help="Submit query and return only the persistent q ID + size (skip /view).")
    p.add_argument("--quiet-id", action="store_true", help="Print only `~XYZ` and nothing else (best for shell piping).")
    p.add_argument("--csv-out", metavar="PATH", help="Also write KWIC lines as flat CSV (one row per concordance line).")
    p.set_defaults(func=cmd_concordance)

    p = sub.add_parser("view", help="Render an existing concordance by q ID.")
    p.add_argument("q", help="Concordance persistence ID (with or without leading ~).")
    p.add_argument("--viewmode", default="kwic", choices=["kwic", "sen", "align"])
    p.add_argument("--pagesize", type=int, default=40)
    p.add_argument("--fromp", type=int, default=1)
    p.add_argument("--attrs", default="word,lemma,tag", help="Comma-separated positional attributes (default: word,lemma,tag) — required for tail_posattrs.")
    p.add_argument("--structs", default="", help="Comma-separated structural attributes.")
    p.add_argument("--refs", default="doc.title", help="Comma-separated structure refs (default: doc.title).")
    p.add_argument("--csv-out", metavar="PATH", help="Also write KWIC lines as flat CSV.")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("freq", help="Token-level frequency distribution over an existing concordance (/freqs).")
    p.add_argument("q", help="Concordance persistence ID.")
    p.add_argument("--fcrit", required=True, help='Frequency criterion, e.g. \'lemma/e 0~0>0\'.')
    p.add_argument("--fmaxitems", type=int, default=100)
    p.add_argument("--csv-out", metavar="PATH", help="Also write frequency table as flat CSV (value, freq, ipm).")
    p.set_defaults(func=cmd_freq)

    p = sub.add_parser("freqtt", help="Structural-attribute frequency distribution (/freqtt).")
    p.add_argument("q", help="Concordance persistence ID.")
    p.add_argument("--fttattr", required=True, help='Dot-separated structural attribute, e.g. doc.txtype_group.')
    p.add_argument("--include-empty", action="store_true")
    p.add_argument("--flimit", type=int, default=1)
    p.add_argument("--csv-out", metavar="PATH", help="Also write frequency table as flat CSV (value, freq, ipm).")
    p.set_defaults(func=cmd_freqtt)

    p = sub.add_parser("freqct", help="Two-dimensional frequency distribution (/freqct).")
    p.add_argument("q", help="Concordance persistence ID.")
    p.add_argument("--ctfcrit1", required=True, help='1st-dimension criterion, e.g. \'lemma 0<0\'.')
    p.add_argument("--ctfcrit2", required=True, help='2nd-dimension criterion, e.g. \'doc.txtype_group 0\'.')
    p.add_argument("--ctminfreq", type=int, default=5)
    p.add_argument("--ctminfreq-type", default="abs", choices=["abs", "pabs", "ipm", "pipm"])
    p.add_argument("--csv-out", metavar="PATH", help="Also write the cross-tab as flat CSV (one row per cell).")
    p.set_defaults(func=cmd_freqct)

    p = sub.add_parser("collocations", help="Collocation profile for an existing concordance (/collx).")
    p.add_argument("q", help="Concordance persistence ID.")
    p.add_argument("--cattr", default="lemma", help="Positional attribute for collocate values (default: lemma).")
    p.add_argument("--cfromw", type=int, default=-5)
    p.add_argument("--ctow", type=int, default=5)
    p.add_argument("--cminfreq", type=int, default=5)
    p.add_argument("--cminbgr", type=int, default=3)
    p.add_argument(
        "--cbgrfns", nargs="+", default=["d", "t", "m"],
        help="Measures: t=t-score, m=MI, 3=MI3, l=loglik, s=min-sens, d=logdice, p=MI-log_f, r=relfreq.",
    )
    p.add_argument("--csortfn", default="d", help="Sort by measure code (default: d=logdice).")
    p.add_argument("--citemsperpage", type=int, default=50)
    p.add_argument("--collpage", type=int, default=1)
    p.add_argument("--csv-out", metavar="PATH",
                   help="Also write collocates as flat CSV. Stats[] is expanded into one column per measure (named per response Head).")
    p.set_defaults(func=cmd_collocations)

    p = sub.add_parser("subcorpus-create", help="Create a named subcorpus (/subcorpus/create).")
    p.add_argument("corpus", help="Corpus identifier.")
    p.add_argument("name", help="New subcorpus name.")
    p.add_argument("--form-type", default="cql", choices=["tt-sel", "within", "cql"])
    p.add_argument("--cql", help="CQL definition (required when --form-type=cql).")
    p.add_argument("--text-types-json", help="JSON object for tt-sel form-type.")
    p.add_argument("--description", help="Optional description.")
    p.set_defaults(func=cmd_subcorpus_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    global _THROTTLE_SECONDS
    _THROTTLE_SECONDS = float(getattr(args, "throttle", 0.0) or 0.0)
    try:
        return int(args.func(args))
    except KorpusError as exc:
        print(f"korpuscz: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
