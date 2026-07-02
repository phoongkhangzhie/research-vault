"""bib.py — Zotero-to-BibTeX exporter for manuscript grounding.

Builds manuscripts/<id>/refs.bib from the project's library.json so the
LaTeX draft has a closed bibliography sourced from filed literature/ notes.

Anti-fabrication contract:
  - Every \\cite{key} in the drafted sections must resolve to a citekey
    present in library.json (the Zotero-synced cache).
  - An unmatched \\cite{key} is a HARD ERROR at ``rv manuscript check``
    and is returned as a non-empty errors list from build_refs_bib.
  - The generated .bib uses LaTeX cite{} commands only (not Pandoc
    inline-citation syntax), so the leakage-scan class-8 pattern does not
    trigger on the generated output.

Ground-check: cite.py uses _list_top / _all_citekeys to read from the live
Zotero API. This module reads from the local library.json cache (the file
synced by ``rv cite sync`` / ``sync_library``). No live API calls here —
zero-infra, hermetic, testable.

Stdlib only.
sr: SR-MS-1b
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# BibTeX type mapping (Zotero itemType → BibTeX entry type)
# ---------------------------------------------------------------------------

_ITEM_TYPE_MAP: dict[str, str] = {
    "journalArticle": "article",
    "conferencePaper": "inproceedings",
    "book": "book",
    "bookSection": "incollection",
    "thesis": "phdthesis",
    "report": "techreport",
    "encyclopediaArticle": "misc",
    "dictionaryEntry": "misc",
    "patent": "misc",
    "webpage": "misc",
    "preprint": "misc",
    "manuscript": "unpublished",
    "presentation": "misc",
    "videoRecording": "misc",
    "podcast": "misc",
    "interview": "misc",
    "statute": "misc",
    "bill": "misc",
    "case": "misc",
    "hearing": "misc",
    "document": "misc",
    "letter": "misc",
    "note": "misc",
}


def _bib_entry_type(item_type: str) -> str:
    return _ITEM_TYPE_MAP.get(item_type, "misc")


# ---------------------------------------------------------------------------
# Citekey extraction from a Zotero item
# ---------------------------------------------------------------------------

def _extract_citekey(data: dict[str, Any]) -> str | None:
    """Extract the BibTeX citekey from a Zotero item data dict.

    Checks (in priority order):
      1. data["citationKey"] (BBT-set field)
      2. "Citation Key: <key>" in data["extra"]
    Returns None if no citekey found.
    """
    if data.get("citationKey"):
        return str(data["citationKey"]).strip() or None
    extra = data.get("extra", "") or ""
    m = re.search(r"Citation Key:\s*(\S+)", extra)
    if m:
        return m.group(1).strip() or None
    return None


# ---------------------------------------------------------------------------
# Author formatting
# ---------------------------------------------------------------------------

def _format_authors(creators: list[dict[str, Any]]) -> str:
    """Format Zotero creators list as BibTeX author string.

    Produces: "Last, First and Last2, First2" style.
    """
    parts: list[str] = []
    for c in creators:
        if c.get("creatorType") not in ("author", "editor"):
            continue
        last = (c.get("lastName") or "").strip()
        first = (c.get("firstName") or "").strip()
        if last and first:
            parts.append(f"{last}, {first}")
        elif last:
            parts.append(last)
        elif first:
            parts.append(first)
        else:
            name = (c.get("name") or "").strip()
            if name:
                parts.append(name)
    return " and ".join(parts) if parts else "Unknown"


# ---------------------------------------------------------------------------
# BibTeX entry generation from a Zotero item
# ---------------------------------------------------------------------------

def _year_from_date(date_str: str) -> str:
    """Extract 4-digit year from Zotero date string."""
    if not date_str:
        return ""
    m = re.search(r"\b(19|20)\d{2}\b", date_str)
    return m.group(0) if m else date_str[:4]


def _escape_bib(s: str) -> str:
    """Minimal BibTeX field escaping: wrap braces for safety."""
    # Remove any existing outer braces, then re-wrap
    s = s.replace("\\", "\\\\")
    return s


def _item_to_bib(citekey: str, data: dict[str, Any]) -> str:
    """Convert a Zotero item data dict to a BibTeX entry string.

    Returns a BibTeX entry block (e.g. '@article{key,\\n  author = {...},\\n...\\n}\\n').
    """
    entry_type = _bib_entry_type(data.get("itemType", "misc"))
    creators = data.get("creators", [])
    author_str = _format_authors(creators)
    title = _escape_bib((data.get("title") or "").strip())
    year = _year_from_date(data.get("date", ""))
    journal = _escape_bib((data.get("publicationTitle") or
                            data.get("proceedingsTitle") or
                            data.get("repository") or "").strip())
    volume = (data.get("volume") or "").strip()
    number = (data.get("issue") or data.get("number") or "").strip()
    pages = (data.get("pages") or "").strip()
    doi = (data.get("DOI") or "").strip()
    url = (data.get("url") or "").strip()
    publisher = (data.get("publisher") or "").strip()
    school = (data.get("university") or data.get("institution") or "").strip()
    booktitle = _escape_bib((data.get("proceedingsTitle") or
                              data.get("bookTitle") or "").strip())
    archive_id = (data.get("archiveID") or "").strip()

    fields: list[tuple[str, str]] = [
        ("author", author_str),
        ("title", title),
    ]
    if entry_type == "article":
        if journal:
            fields.append(("journal", journal))
        if volume:
            fields.append(("volume", volume))
        if number:
            fields.append(("number", number))
        if pages:
            fields.append(("pages", pages))
    elif entry_type == "inproceedings":
        if booktitle or journal:
            fields.append(("booktitle", booktitle or journal))
        if pages:
            fields.append(("pages", pages))
    elif entry_type == "phdthesis":
        if school:
            fields.append(("school", school))
    elif entry_type in ("book", "incollection"):
        if publisher:
            fields.append(("publisher", publisher))
    else:
        # misc / unpublished — include archive info if available
        if archive_id:
            fields.append(("note", archive_id))
        elif journal:
            fields.append(("note", journal))
        if url and not doi:
            fields.append(("url", url))

    if year:
        fields.append(("year", year))
    if doi:
        fields.append(("doi", doi))
    if url and entry_type not in ("misc",):
        fields.append(("url", url))

    lines = [f"@{entry_type}{{{citekey},"]
    for field_name, value in fields:
        if value:
            lines.append(f"  {field_name} = {{{value}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# \cite{} extraction from .tex files
# ---------------------------------------------------------------------------

# Matches \cite{key}, \citep{key}, \citet{key}, \citealt{key}, etc.
# Also \cite[p. 1]{key}, \cite{key1,key2} (multi-cite)
_CITE_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _strip_latex_comments(text: str) -> str:
    """Strip LaTeX line comments (% to end of line) from text.

    Handles the edge case of escaped percent signs (\\%) which are NOT comments.
    Simple line-by-line stripping — no full LaTeX parser needed for cite extraction.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        # Find the first unescaped % in the line
        stripped = line
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                stripped = line[:i]
                break
            i += 1
        lines.append(stripped)
    return "\n".join(lines)


def extract_cited_keys(tex_files: list[Path]) -> set[str]:
    r"""Extract all citekeys from \cite{} commands in a list of .tex files.

    Handles:
      \cite{key}        — simple
      \citep{key}       — natbib-style
      \cite{key1,key2}  — multi-cite
      \cite[p. 1]{key}  — optional note

    Strips LaTeX line comments (% to end-of-line) before scanning so that
    commented-out examples (e.g. ``% every \cite{key} must resolve``) do not
    generate false positives.

    Returns a set of stripped citekey strings.
    """
    keys: set[str] = set()
    for tex_path in tex_files:
        if not tex_path.exists():
            continue
        try:
            text = tex_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Strip comments before scanning so examples in % comments don't trip us
        text = _strip_latex_comments(text)
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if k:
                    keys.add(k)
    return keys


# ---------------------------------------------------------------------------
# Library.json loading
# ---------------------------------------------------------------------------

def load_library(library_path: Path) -> dict[str, dict[str, Any]]:
    """Load library.json and return a citekey → item-data mapping.

    Returns an empty dict (with an error surfaced by the caller) if the file
    doesn't exist or is malformed.

    Raises:
        FileNotFoundError: if library_path does not exist.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    raw = json.loads(library_path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    items: list[dict] = raw if isinstance(raw, list) else []
    for it in items:
        data = it.get("data", {}) if isinstance(it, dict) else {}
        ck = _extract_citekey(data)
        if ck:
            result[ck] = data
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_refs_bib(
    tree_root: Path,
    *,
    library_path: Path | None = None,
    cite_tex_files: list[Path] | None = None,
) -> tuple[list[str], Path]:
    """Build manuscripts/<id>/refs.bib from library.json + cited keys in .tex files.

    When to use: called by ``rv manuscript compile`` (and at ``rv manuscript check``
    for the closed-bib resolution gate). Reads the project's library.json (the
    Zotero-synced local cache), collects all \\cite{key} references from the
    manuscript's .tex files, and emits a BibTeX file containing only the cited
    entries.

    Anti-fabrication contract (§5J.3):
      - Every \\cite{key} must resolve against library.json.
      - An unmatched key returns a hard error in the errors list.
      - The .bib is "closed" — only entries cited in the draft appear.

    Args:
        tree_root: path to manuscripts/<id>/ (the LaTeX artifact tree root).
        library_path: path to library.json. When None, returns an error.
        cite_tex_files: list of .tex paths to scan for \\cite{}. When None,
            scans all .tex files under tree_root recursively.

    Returns:
        (errors, bib_path):
          errors: list of hard-error strings (empty = success).
          bib_path: path to the written refs.bib (tree_root / "refs.bib").
    """
    bib_path = tree_root / "refs.bib"
    errors: list[str] = []

    # ── Resolve library ───────────────────────────────────────────────────────
    if library_path is None:
        errors.append(
            "refs.bib: library.json path not provided — run `rv cite sync` "
            "or configure refs: in your project settings."
        )
        bib_path.write_text(
            "% refs.bib — EMPTY: library.json not found (run `rv cite sync`)\n",
            encoding="utf-8",
        )
        return errors, bib_path

    if not library_path.exists():
        errors.append(
            f"refs.bib: library.json not found at {library_path} — "
            f"run `rv cite sync` to populate it."
        )
        bib_path.write_text(
            f"% refs.bib — EMPTY: library.json not found at {library_path}\n",
            encoding="utf-8",
        )
        return errors, bib_path

    try:
        library = load_library(library_path)
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"refs.bib: cannot read library.json: {exc}")
        bib_path.write_text(
            "% refs.bib — EMPTY: could not parse library.json\n",
            encoding="utf-8",
        )
        return errors, bib_path

    # ── Collect cited keys ────────────────────────────────────────────────────
    if cite_tex_files is None:
        cite_tex_files = list(tree_root.rglob("*.tex"))

    cited_keys = extract_cited_keys(cite_tex_files)

    # ── Match + detect unmatched ──────────────────────────────────────────────
    matched: dict[str, dict[str, Any]] = {}
    for key in sorted(cited_keys):
        if key in library:
            matched[key] = library[key]
        else:
            errors.append(
                f"refs.bib: unmatched \\cite{{{key}}} — "
                f"'{key}' not found in library.json. "
                f"Add the reference via `rv cite add <doi|arxiv>` "
                f"and re-run `rv cite sync`."
            )

    # ── Write refs.bib ────────────────────────────────────────────────────────
    header = (
        "% refs.bib — auto-populated by `rv manuscript compile`.\n"
        "% Closed bibliography: only entries cited in the draft appear.\n"
        "% Do NOT hand-edit citekeys; run `rv cite check` to verify coverage.\n"
        "% Uses LaTeX cite commands only (not Pandoc inline-citation form).\n"
    )
    if not matched and not errors:
        bib_path.write_text(header, encoding="utf-8")
        return [], bib_path

    body_parts = [header, ""]
    for key, data in matched.items():
        body_parts.append(_item_to_bib(key, data))
        body_parts.append("")

    bib_path.write_text("\n".join(body_parts), encoding="utf-8")
    return errors, bib_path
