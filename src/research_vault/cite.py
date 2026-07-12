# SPDX-License-Identifier: AGPL-3.0-or-later
"""cite.py — Zotero citation-manager bridge for Research Vault.

When to use: ``rv cite <subcommand>`` to resolve, add, list, check, or link
citekeys against a project's Zotero collection.

Key constraints (re-implemented fresh from vault's cite.py as behavioral spec):
  - Secrets route through the SecretStore Protocol (ZOTERO_KEY env-var first →
    cross-platform keyring) — NEVER the macOS-only ``security`` binary.
  - All path resolution goes through Config — zero hardcoded paths or codenames.
  - Stdlib only (no third-party deps for core functionality).

Commands:
  rv cite whoami                   verify the API key → userID, username, write access
  rv cite add <doi|arxiv> [--dry-run] [--collection NAME]
  rv cite check [--project P]      every literature note's citekey resolves in Zotero
  rv cite get <citekey>            print a citekey's metadata from library.json
  rv cite ls [--collection NAME]   list top-level items

The ``default_project`` used by subcommands that need a notes path comes from
config (``default_project`` key) — never a compiled-in codename.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .adapters.base import EnvSecretStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZOTERO_API = "https://api.zotero.org"
UA = "research_vault/cite"


# ---------------------------------------------------------------------------
# Secret resolution (SecretStore Protocol — no macOS security binary)
# ---------------------------------------------------------------------------

def _get_zotero_key() -> str:
    """Resolve the Zotero API key via the SecretStore Protocol.

    Resolution order:
      1. $ZOTERO_KEY env var
      2. Cross-platform keyring (via EnvSecretStore which wraps keyring)

    Never calls the macOS-only ``security`` binary.
    Raises SystemExit with a clear remediation message if not found.
    """
    store = EnvSecretStore()
    try:
        return store.get("zotero-key")
    except KeyError:
        sys.exit(
            "Zotero API key not found.\n"
            "  Fix: export ZOTERO_KEY=<your-key>  or  "
            "keyring.set_password('research-vault', 'zotero-key', '<key>')"
        )


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _http(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _zotero(
    method: str,
    path: str,
    key: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    h: dict[str, str] = {"Zotero-API-Key": key, "Zotero-API-Version": "3"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
        if method == "POST":
            h.setdefault("Zotero-Write-Token", uuid.uuid4().hex)
    status, text = _http(ZOTERO_API + path, data=data, headers=h, method=method)
    try:
        return status, json.loads(text) if text else None
    except json.JSONDecodeError:
        return status, text


# ---------------------------------------------------------------------------
# Zotero API helpers
# ---------------------------------------------------------------------------

def _whoami(key: str) -> tuple[int, str, bool]:
    """Return (userID, username, write_access)."""
    status, body = _zotero("GET", "/keys/current", key)
    if status != 200 or not isinstance(body, dict):
        sys.exit(f"Zotero key check failed ({status}): {body}")
    uid = body.get("userID")
    write = bool(body.get("access", {}).get("user", {}).get("write"))
    return uid, body.get("username", ""), write


def _list_top(key: str, uid: int) -> list[dict]:
    """Return all top-level items (paginated)."""
    out, start = [], 0
    while True:
        status, items = _zotero("GET", f"/users/{uid}/items/top?limit=100&start={start}", key)
        if status != 200 or not isinstance(items, list):
            break
        out += items
        if len(items) < 100:
            break
        start += 100
    return out


def _find_collection(key: str, uid: int, name: str) -> str | None:
    status, body = _zotero("GET", f"/users/{uid}/collections", key)
    if status == 200 and isinstance(body, list):
        for c in body:
            if c.get("data", {}).get("name") == name:
                return c["key"]
    return None


def create_collection(name: str, *, key: str, uid: int) -> str:
    """Create a new Zotero collection and return its key.

    Reuses the existing _whoami/_zotero POST plumbing.
    Called only under the --zotero guard in `rv project new`.
    Idempotent: returns existing collection key if the collection already exists.

    Raises RuntimeError if the API call fails.
    """
    existing = _find_collection(key, uid, name)
    if existing is not None:
        return existing  # idempotent: return key if collection already exists

    status, body = _zotero(
        "POST",
        f"/users/{uid}/collections",
        key,
        body=[{"name": name, "parentCollection": False}],
    )
    if status not in (200, 201) or not isinstance(body, dict):
        raise RuntimeError(
            f"Zotero create-collection failed (status={status}): {body}"
        )
    # Response: {"success": {"0": "<key>"}, ...}
    success = body.get("success", {})
    if not success:
        raise RuntimeError(f"Zotero create-collection: no success key in response: {body}")
    coll_key = next(iter(success.values()))
    return coll_key


def sync_library(coll_key: str, *, key: str, uid: int, refs_path: "Path") -> list:
    """Sync the Zotero collection items into refs_path (library.json).

    Fetches all items in the collection (paginated) and writes them as a JSON
    list to refs_path. For a fresh empty collection this yields [].

    This is the wiring step: after create_collection, project new calls this so
    library.json reflects the Zotero collection from the start (mirror pattern).
    Reuses the _zotero GET plumbing — no new HTTP layer.

    Returns the list of item records written.
    """
    from pathlib import Path as _Path
    import json as _json

    out, start = [], 0
    while True:
        path = (
            f"/users/{uid}/collections/{coll_key}/items/top"
            f"?limit=100&start={start}"
        )
        status, items = _zotero("GET", path, key)
        if status != 200 or not isinstance(items, list):
            break
        out += items
        if len(items) < 100:
            break
        start += 100

    _Path(refs_path).write_text(_json.dumps(out, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Metadata fetching
# ---------------------------------------------------------------------------

def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _fetch_doi(doi: str) -> tuple[dict, str | None, str]:
    status, text = _http(
        f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
        headers={"Accept": "application/json"},
    )
    if status != 200:
        sys.exit(f"Crossref lookup failed ({status}) for {doi}")
    m = json.loads(text)["message"]
    auth = m.get("author", [])
    item = {
        "itemType": "journalArticle",
        "title": " ".join(m.get("title", []) or []),
        "creators": [
            {"creatorType": "author", "firstName": a.get("given", ""), "lastName": a.get("family", "")}
            for a in auth
        ],
        "date": str((m.get("issued", {}).get("date-parts", [[None]]) or [[None]])[0][0] or ""),
        "publicationTitle": " ".join(m.get("container-title", []) or []),
        "DOI": m.get("DOI", ""),
        "url": m.get("URL", ""),
        "abstractNote": _strip_tags(m.get("abstract", "")),
    }
    family = auth[0].get("family") if auth else None
    return item, family, item["date"][:4]


def _fetch_arxiv(aid: str) -> tuple[dict, str | None, str]:
    status, text = _http(f"http://export.arxiv.org/api/query?id_list={aid}")
    if status != 200:
        sys.exit(f"arXiv lookup failed ({status}) for {aid}")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = ET.fromstring(text).find("a:entry", ns)
    if e is None:
        sys.exit(f"arXiv id not found: {aid}")
    names = [n.findtext("a:name", default="", namespaces=ns) for n in e.findall("a:author", ns)]
    creators = []
    for nm in names:
        p = nm.rsplit(" ", 1)
        creators.append({
            "creatorType": "author",
            "firstName": p[0] if len(p) > 1 else "",
            "lastName": p[-1],
        })
    year = e.findtext("a:published", default="", namespaces=ns)[:4]
    item = {
        "itemType": "preprint",
        "title": _strip_tags(e.findtext("a:title", default="", namespaces=ns)),
        "creators": creators,
        "date": year,
        "repository": "arXiv",
        "archiveID": f"arXiv:{aid}",
        "url": f"https://arxiv.org/abs/{aid}",
        "abstractNote": _strip_tags(e.findtext("a:summary", default="", namespaces=ns)),
    }
    family = creators[0]["lastName"] if creators else None
    return item, family, year


def _resolve_ident(ident: str) -> tuple[str, str] | None:
    """Parse a DOI or arXiv identifier. Returns (kind, id) or None."""
    s = (ident or "").strip()
    d = re.search(r"10\.\d{4,9}/\S+", s)
    if d and "arxiv" not in s.lower():
        return ("doi", d.group(0).rstrip(").,"))
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", s)
    if m:
        return ("arxiv", m.group(1))
    if d:
        return ("doi", d.group(0).rstrip(").,"))
    return None


# ---------------------------------------------------------------------------
# Citekey generation (BBT-style)
# ---------------------------------------------------------------------------

_SKIP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on",
    "at", "for", "with", "from", "by", "as", "is", "are", "be",
    "into", "towards", "toward", "via", "using", "no", "not",
    "do", "does",
})


def _shorttitle(title: str, n: int = 3) -> str:
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    sig = [w for w in words if w.lower() not in _SKIP_WORDS]
    return "".join(w[:1].upper() + w[1:] for w in sig[:n])


def _all_citekeys(key: str, uid: int) -> set[str]:
    """Return all citekeys in the library (global namespace)."""
    keys: set[str] = set()
    for it in _list_top(key, uid):
        d = it.get("data", {})
        if d.get("citationKey"):
            keys.add(d["citationKey"])
        m = re.search(r"Citation Key:\s*(\S+)", d.get("extra", ""))
        if m:
            keys.add(m.group(1))
    return keys


# ---------------------------------------------------------------------------
#  the ONE canonical citekey convention (authorYearWord).
# ---------------------------------------------------------------------------
# Four incompatible schemes were reaching the corpus (arXiv-id / S2-id /
# OpenAlex-id / slug) because each ingestion path minted its own key. This is
# the single source of truth: ``familyShorttitleYear`` (+ an a/b/c
# disambiguation suffix on collision) — exactly what Zotero's Better BibTeX
# plugin produces, so a note filed by hand via Zotero and one computed here
# land on the same key.
#
# ``make_citekey`` is Zotero-free and pure: it takes an explicit ``existing``
# set rather than resolving one itself. ``cmd_add`` (below) passes the
# Zotero-backed set (``_all_citekeys``); the review loop / migration verb
# (``research.py``) passes a ``literature/*.md`` frontmatter scan instead
# (reusing ``research._load_notes_index``'s scan pattern) — same pure
# function, two different existing-key universes.
CITEKEY_RE = re.compile(r"^[a-z]+[A-Za-z]*\d{4}[a-z]?$")

# Visible fail-closed sentinel for a citekey that could not be computed
# (title/year metadata unresolved) — NEVER a guessed key.
# Mirrors the REPRO_SENTINEL convention (note.py): a loud, greppable hole,
# never a blank field or an invented value.
CITEKEY_SENTINEL = "CITEKEY-UNRESOLVED"


def make_citekey(family: str | None, title: str, year: str, existing: set[str]) -> str:
    """Compute the canonical ``familyShorttitleYear`` citekey.

    Pure + Zotero-free: ``existing`` is an explicit set of already-used keys
    (from whatever universe the caller cares about — a Zotero library, a
    project's filed literature notes, or both unioned). On collision,
    appends the first free ``a``/``b``/``c``/... disambiguation suffix.
    """
    base = re.sub(r"[^a-z0-9]", "", (family or "anon").lower()) + _shorttitle(title) + (year or "")
    if base not in existing:
        return base
    i = ord("a")
    while base + chr(i) in existing:
        i += 1
    return base + chr(i)


# Backward-compat alias — existing internal call sites (cmd_add below) and
# review/remediation.py's ``from ..cite import _make_citekey`` keep working
# unchanged. New code should import ``make_citekey`` (the public name).
_make_citekey = make_citekey


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_whoami(args: argparse.Namespace) -> int:
    key = _get_zotero_key()
    uid, username, write = _whoami(key)
    print(f"userID:   {uid}")
    print(f"username: {username}")
    print(f"write:    {write}")
    if not write:
        print("WARNING: this key is read-only — recreate it with write access.", file=sys.stderr)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    r = _resolve_ident(args.ident)
    if not r:
        print(f"rv cite add: cannot parse a DOI or arXiv id from: {args.ident!r}", file=sys.stderr)
        return 1
    kind, ident = r
    item, family, year = _fetch_arxiv(ident) if kind == "arxiv" else _fetch_doi(ident)
    key = _get_zotero_key()
    uid, _, write = _whoami(key)
    ck = _make_citekey(family, item.get("title", ""), year, _all_citekeys(key, uid))
    item["extra"] = f"Citation Key: {ck}"
    item.setdefault("tags", []).append({"tag": "status/to-read"})

    if args.dry_run:
        print(f"citekey: {ck}")
        print(json.dumps(item, indent=2, ensure_ascii=False))
        return 0

    if not write:
        print("rv cite add: API key lacks write access.", file=sys.stderr)
        return 1

    # Optionally file into a collection
    coll_key = None
    if args.collection:
        coll_key = _find_collection(key, uid, args.collection)
        if coll_key:
            item["collections"] = [coll_key]
        else:
            print(f"(no collection {args.collection!r} found — adding to library root)")

    status, body = _zotero("POST", f"/users/{uid}/items", key, body=[item])
    if status == 200 and isinstance(body, dict) and not body.get("failed"):
        print(f"Added: {ck}")
        return 0
    else:
        print(f"rv cite add: failed ({status}): {body}", file=sys.stderr)
        return 1


def cmd_check(args: argparse.Namespace) -> int:
    """Check that every literature note's citekey exists in the Zotero library."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv cite check: config error: {e}", file=sys.stderr)
        return 1

    # Resolve notes directory
    project = getattr(args, "project", None) or cfg._raw.get("default_project")
    if project:
        try:
            notes_dir = cfg.project_notes_dir(project)
        except KeyError as e:
            print(f"rv cite check: {e}", file=sys.stderr)
            return 1
    else:
        notes_dir = cfg.notes_root

    if not notes_dir.exists():
        print(f"Notes directory not found: {notes_dir}")
        return 0

    key = _get_zotero_key()
    uid, _, _ = _whoami(key)
    library_keys = _all_citekeys(key, uid)

    missing = []
    for f in sorted(notes_dir.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^citekey:\s*(\S+)", text, re.M)
        if not m:
            continue  # no citekey in this note — skip
        ck = m.group(1)
        if ck not in library_keys:
            missing.append((f.name, ck))

    print(f"{len(library_keys)} entries in Zotero; {len(missing)} note(s) with unresolved citekeys:")
    for fname, ck in missing:
        print(f"    {fname} — {ck!r} not in library")
    return 0 if not missing else 1


def cmd_get(args: argparse.Namespace) -> int:
    """Fetch metadata for a citekey from the live Zotero library."""
    key = _get_zotero_key()
    uid, _, _ = _whoami(key)
    for it in _list_top(key, uid):
        d = it.get("data", {})
        m = re.search(r"Citation Key:\s*(\S+)", d.get("extra", ""))
        ck = d.get("citationKey") or (m.group(1) if m else None)
        if ck == args.citekey:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return 0
    print(f"rv cite get: no item with citekey {args.citekey!r}", file=sys.stderr)
    return 1


def cmd_ls(args: argparse.Namespace) -> int:
    """List top-level Zotero items (optionally filtered by collection)."""
    key = _get_zotero_key()
    uid, _, _ = _whoami(key)
    limit = getattr(args, "limit", 50)
    collection = getattr(args, "collection", None)

    if collection:
        coll_key = _find_collection(key, uid, collection)
        if not coll_key:
            print(f"rv cite ls: no collection {collection!r}", file=sys.stderr)
            return 1
        path = f"/users/{uid}/collections/{coll_key}/items/top?limit={limit}"
    else:
        path = f"/users/{uid}/items/top?limit={limit}"

    status, items = _zotero("GET", path, key)
    if status != 200 or not isinstance(items, list):
        print(f"rv cite ls: failed ({status}): {items}", file=sys.stderr)
        return 1

    for it in items:
        d = it.get("data", {})
        m = re.search(r"Citation Key:\s*(\S+)", d.get("extra", ""))
        ck = d.get("citationKey") or (m.group(1) if m else "—")
        title = d.get("title", "")[:70]
        print(f"  {ck:<24} {title}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``cite`` verb.

    When to use: use ``rv cite <subcommand>`` to manage Zotero citations for a
    project. Secrets route through the SecretStore Protocol (env first, then
    cross-platform keyring) — never macOS-only binaries.
    """
    desc = (
        "Zotero citation-manager bridge. Requires ZOTERO_KEY env var "
        "(or keyring under 'research-vault'/'zotero-key')."
    )
    if parent is not None:
        p = parent.add_parser("cite", help="Manage Zotero citations.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv cite", description=desc)

    sub = p.add_subparsers(dest="cite_cmd", required=True)

    # whoami
    sub.add_parser("whoami", help="Verify the Zotero API key.")

    # add
    add_p = sub.add_parser("add", help="Add a paper by DOI or arXiv id.")
    add_p.add_argument("ident", help="DOI or arXiv id.")
    add_p.add_argument("--collection", default=None, help="Zotero collection name.")
    add_p.add_argument("--dry-run", action="store_true", help="Preview without writing.")

    # check
    chk_p = sub.add_parser("check", help="Check that all literature note citekeys exist in Zotero.")
    chk_p.add_argument("--project", default=None, help="Project slug (from config registry).")

    # get
    get_p = sub.add_parser("get", help="Get metadata for a citekey.")
    get_p.add_argument("citekey", help="The citekey to look up.")

    # ls
    ls_p = sub.add_parser("ls", help="List Zotero library items.")
    ls_p.add_argument("--collection", default=None, help="Zotero collection name.")
    ls_p.add_argument("--limit", type=int, default=50, help="Max items to return.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch cite subcommands. Returns exit code."""
    try:
        cmd = args.cite_cmd
        if cmd == "whoami":
            return cmd_whoami(args)
        elif cmd == "add":
            return cmd_add(args)
        elif cmd == "check":
            return cmd_check(args)
        elif cmd == "get":
            return cmd_get(args)
        elif cmd == "ls":
            return cmd_ls(args)
        else:
            print(f"rv cite: unknown subcommand {cmd!r}", file=sys.stderr)
            return 1
    except SystemExit:
        raise
    except Exception as e:
        print(f"rv cite: unexpected error: {e}", file=sys.stderr)
        return 1
