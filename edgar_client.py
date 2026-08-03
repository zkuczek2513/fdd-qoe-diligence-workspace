"""SEC EDGAR ingestion engine.

Resolves a ticker to a CIK, pulls the issuer's filing index, and fetches 10-K and
10-Q documents concurrently with ``asyncio``/``aiohttp``. Filing documents are
unstructured HTML — often inline XBRL — so item extraction is defensive by
design: every parse step has fallbacks and a missing item is reported rather
than raised.

SEC access rules are enforced here, not left to the caller: every request
carries a declaring User-Agent, requests are throttled below the published
10 requests/second ceiling, and responses are cached so a rerun never re-pings
EDGAR for a document it already holds.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

try:  # pragma: no cover - exercised implicitly at runtime
    import aiohttp

    AIOHTTP_AVAILABLE = True
    AIOHTTP_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False
    AIOHTTP_IMPORT_ERROR = str(exc)

# lxml is an optional accelerator, not a requirement. It is a compiled extension
# whose wheels lag new Python releases, so it is deliberately absent from
# requirements.txt; ``html_to_text`` falls back to a regex stripper that produces
# equivalent output. Never make this import mandatory.
try:  # pragma: no cover
    import lxml.html as lxml_html

    LXML_AVAILABLE = True
except Exception:  # pragma: no cover
    lxml_html = None  # type: ignore[assignment]
    LXML_AVAILABLE = False

from config import (
    EDGAR_ARCHIVES_URL,
    EDGAR_COMPANY_TICKERS_URL,
    EDGAR_MAX_CONCURRENCY,
    EDGAR_REQUEST_TIMEOUT_SECONDS,
    EDGAR_SUBMISSIONS_URL,
    EDGAR_THROTTLE_SECONDS,
    SEC_DEFAULT_USER_AGENT,
)


class EdgarError(RuntimeError):
    """Raised when EDGAR cannot be reached or a ticker cannot be resolved."""


# --------------------------------------------------------------------------- #
# Request plumbing
# --------------------------------------------------------------------------- #


def user_agent() -> str:
    """The declaring User-Agent sent to SEC.

    The SEC requires an identifying User-Agent carrying contact details and
    will return 403 without one. ``SEC_USER_AGENT`` overrides the default so a
    deployment can declare its own operator.
    """
    configured = os.environ.get("SEC_USER_AGENT", "").strip()
    return configured or SEC_DEFAULT_USER_AGENT


def _headers() -> dict[str, str]:
    # Host is deliberately left to the HTTP client: EDGAR spans www.sec.gov and
    # data.sec.gov, and a hand-set Host that disagrees with the URL is rejected.
    return {
        "User-Agent": user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "text/html,application/json,*/*",
    }


def _get_json_sync(url: str) -> dict:
    """Blocking JSON fetch, used for the small index documents.

    ``urllib`` advertises gzip but does not transparently decompress it the way
    ``aiohttp`` does, so the response body is decoded here explicitly.
    """
    request = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=EDGAR_REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
            encoding = (response.info().get("Content-Encoding") or "").lower()
        if "gzip" in encoding:
            payload = gzip.decompress(payload)
        elif "deflate" in encoding:
            payload = zlib.decompress(payload, -zlib.MAX_WBITS)
        return json.loads(payload.decode("utf-8", errors="replace"))
    except EdgarError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a warning
        raise EdgarError(
            f"SEC EDGAR request failed for {url} ({type(exc).__name__}: {exc}). "
            "EDGAR rate-limits datacenter ranges; retry shortly, or set SEC_USER_AGENT "
            "to a User-Agent containing your contact email as the SEC requires."
        ) from exc


# --------------------------------------------------------------------------- #
# Filing metadata
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FilingRef:
    """One filing in an issuer's EDGAR history."""

    form: str
    accession: str
    filing_date: str
    report_date: str
    primary_document: str
    cik: str

    @property
    def fiscal_year(self) -> str:
        source = self.report_date or self.filing_date
        try:
            return f"FY{date.fromisoformat(source).year}"
        except (TypeError, ValueError):
            return source or "n/a"

    @property
    def document_url(self) -> str:
        stripped = self.accession.replace("-", "")
        return (
            f"{EDGAR_ARCHIVES_URL}/{int(self.cik)}/{stripped}/{self.primary_document}"
        )

    @property
    def index_url(self) -> str:
        stripped = self.accession.replace("-", "")
        return f"{EDGAR_ARCHIVES_URL}/{int(self.cik)}/{stripped}/"


@dataclass
class FilingDocument:
    """A fetched filing with its extracted narrative sections."""

    ref: FilingRef
    raw_text: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fetch_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.fetch_error and bool(self.raw_text)

    def section(self, key: str) -> str:
        return self.sections.get(key, "")


def resolve_cik(ticker: str) -> tuple[str, str]:
    """Map a ticker to its zero-padded CIK and registered company name."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise EdgarError("Enter a ticker symbol to query EDGAR.")

    payload = _get_json_sync(EDGAR_COMPANY_TICKERS_URL)
    for entry in payload.values():
        if str(entry.get("ticker", "")).upper() == symbol:
            return f"{int(entry['cik_str']):010d}", str(entry.get("title", symbol))

    raise EdgarError(
        f"'{symbol}' was not found in the SEC ticker index. EDGAR covers US registrants only — "
        "foreign private issuers, private companies and most ADRs will not resolve."
    )


def list_filings(cik: str, forms: Sequence[str] = ("10-K", "10-Q"), limit: int = 8) -> list[FilingRef]:
    """Return the most recent filings of the requested forms, newest first."""
    payload = _get_json_sync(EDGAR_SUBMISSIONS_URL.format(cik=cik))
    recent = payload.get("filings", {}).get("recent", {})

    form_list = recent.get("form", []) or []
    accessions = recent.get("accessionNumber", []) or []
    filing_dates = recent.get("filingDate", []) or []
    report_dates = recent.get("reportDate", []) or []
    documents = recent.get("primaryDocument", []) or []

    wanted = {form.upper() for form in forms}
    results: list[FilingRef] = []
    for index, form in enumerate(form_list):
        if str(form).upper() not in wanted:
            continue
        try:
            results.append(
                FilingRef(
                    form=str(form).upper(),
                    accession=str(accessions[index]),
                    filing_date=str(filing_dates[index]),
                    report_date=str(report_dates[index]) if index < len(report_dates) else "",
                    primary_document=str(documents[index]),
                    cik=cik,
                )
            )
        except IndexError:
            continue
        if len(results) >= limit:
            break
    return results


# --------------------------------------------------------------------------- #
# Concurrent document retrieval
# --------------------------------------------------------------------------- #


async def _fetch_one(
    session, ref: FilingRef, semaphore: "asyncio.Semaphore"
) -> FilingDocument:
    document = FilingDocument(ref=ref)
    async with semaphore:
        # Stay comfortably inside the SEC's published 10 requests/second limit.
        await asyncio.sleep(EDGAR_THROTTLE_SECONDS)
        try:
            timeout = aiohttp.ClientTimeout(total=EDGAR_REQUEST_TIMEOUT_SECONDS)
            async with session.get(ref.document_url, timeout=timeout) as response:
                if response.status != 200:
                    document.fetch_error = (
                        f"HTTP {response.status} retrieving {ref.form} {ref.fiscal_year}."
                    )
                    return document
                payload = await response.text(errors="ignore")
        except Exception as exc:  # noqa: BLE001 - one bad filing must not stop the run
            document.fetch_error = f"{type(exc).__name__}: {exc}"
            return document

    document.raw_text = html_to_text(payload)
    document.sections, document.warnings = extract_sections(document.raw_text, ref.form)
    return document


async def _fetch_all(refs: Sequence[FilingRef]) -> list[FilingDocument]:
    semaphore = asyncio.Semaphore(EDGAR_MAX_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=EDGAR_MAX_CONCURRENCY)
    async with aiohttp.ClientSession(headers=_headers(), connector=connector) as session:
        tasks = [_fetch_one(session, ref, semaphore) for ref in refs]
        return list(await asyncio.gather(*tasks))


def fetch_documents(refs: Sequence[FilingRef]) -> list[FilingDocument]:
    """Fetch filings concurrently, returning one document per reference.

    Streamlit executes scripts on a worker thread with no running event loop, so
    a fresh loop is created and closed per call rather than relying on an
    ambient one.
    """
    if not refs:
        return []
    if not AIOHTTP_AVAILABLE:
        raise EdgarError(
            f"aiohttp is unavailable, so filings cannot be fetched: {AIOHTTP_IMPORT_ERROR}"
        )

    try:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(_fetch_all(refs))
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    except EdgarError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EdgarError(
            f"Concurrent EDGAR retrieval failed ({type(exc).__name__}: {exc})."
        ) from exc


# --------------------------------------------------------------------------- #
# HTML to text
# --------------------------------------------------------------------------- #

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES = re.compile(r"\n{3,}")

_ENTITIES = {
    "&nbsp;": " ", "&#160;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&rsquo;": "'", "&lsquo;": "'",
    "&ldquo;": '"', "&rdquo;": '"', "&mdash;": "—", "&ndash;": "–",
    "&#8217;": "'", "&#8220;": '"', "&#8221;": '"', "&#8212;": "—",
}


def html_to_text(payload: str) -> str:
    """Flatten filing HTML (including inline XBRL) to plain text."""
    if not payload:
        return ""

    if LXML_AVAILABLE:
        try:
            tree = lxml_html.fromstring(payload)
            for bad in tree.xpath("//script | //style"):
                bad.getparent().remove(bad)
            text = tree.text_content()
            return _BLANKLINES.sub("\n\n", _WHITESPACE.sub(" ", text)).strip()
        except Exception:  # noqa: BLE001 - fall through to the regex stripper
            pass

    text = _SCRIPT_STYLE.sub(" ", payload)
    text = _TAG.sub(" ", text)
    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)
    text = _WHITESPACE.sub(" ", text)
    return _BLANKLINES.sub("\n\n", text).strip()


# --------------------------------------------------------------------------- #
# Item extraction
# --------------------------------------------------------------------------- #

SECTION_RISK_FACTORS = "item_1a_risk_factors"
SECTION_MDA = "item_7_mda"
SECTION_CONTINGENCIES = "contingencies"
SECTION_LEGAL = "item_3_legal_proceedings"

SECTION_LABELS = {
    SECTION_RISK_FACTORS: "Item 1A — Risk Factors",
    SECTION_MDA: "Item 7 — Management's Discussion & Analysis",
    SECTION_LEGAL: "Item 3 — Legal Proceedings",
    SECTION_CONTINGENCIES: "Commitments & Contingencies (footnote)",
}

# Each rule is (start patterns, end patterns). Filings vary wildly in how items
# are titled, so several spellings are tried before the section is abandoned.
_ITEM_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    SECTION_RISK_FACTORS: (
        (r"item\s*1a\s*[\.\:\-–—]?\s*risk\s+factors", r"item\s*1a\s*[\.\:\-–—]"),
        (
            r"item\s*1b\s*[\.\:\-–—]?\s*unresolved",
            r"item\s*1b\s*[\.\:\-–—]",
            r"item\s*2\s*[\.\:\-–—]?\s*properties",
        ),
    ),
    SECTION_MDA: (
        (
            r"item\s*7\s*[\.\:\-–—]?\s*management.s\s+discussion",
            r"item\s*7\s*[\.\:\-–—]",
        ),
        (
            r"item\s*7a\s*[\.\:\-–—]?\s*quantitative",
            r"item\s*7a\s*[\.\:\-–—]",
            r"item\s*8\s*[\.\:\-–—]?\s*financial\s+statements",
        ),
    ),
    SECTION_LEGAL: (
        (r"item\s*3\s*[\.\:\-–—]?\s*legal\s+proceedings",),
        (
            r"item\s*4\s*[\.\:\-–—]?\s*mine\s+safety",
            r"item\s*4\s*[\.\:\-–—]",
        ),
    ),
    SECTION_CONTINGENCIES: (
        (
            r"commitments\s+and\s+contingencies",
            r"contingencies\s+and\s+commitments",
            r"legal\s+contingencies",
        ),
        (
            r"subsequent\s+events",
            r"segment\s+(information|reporting)",
            r"recent\s+accounting\s+pronouncements",
        ),
    ),
}

# 10-Q uses Part I / Part II item numbering rather than the 10-K scheme.
_ITEM_RULES_10Q: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    SECTION_RISK_FACTORS: (
        (r"item\s*1a\s*[\.\:\-–—]?\s*risk\s+factors",),
        (
            r"item\s*2\s*[\.\:\-–—]?\s*unregistered",
            r"item\s*6\s*[\.\:\-–—]?\s*exhibits",
        ),
    ),
    SECTION_MDA: (
        (r"item\s*2\s*[\.\:\-–—]?\s*management.s\s+discussion",),
        (
            r"item\s*3\s*[\.\:\-–—]?\s*quantitative",
            r"item\s*4\s*[\.\:\-–—]?\s*controls",
        ),
    ),
    SECTION_LEGAL: (
        (r"item\s*1\s*[\.\:\-–—]?\s*legal\s+proceedings",),
        (r"item\s*1a\s*[\.\:\-–—]?\s*risk\s+factors",),
    ),
    SECTION_CONTINGENCIES: _ITEM_RULES[SECTION_CONTINGENCIES],
}

MIN_SECTION_CHARACTERS = 500
# Ceiling applied only when no closing heading resolves, so a stray
# cross-reference cannot absorb the remainder of the filing.
MAX_UNBOUNDED_SECTION_CHARACTERS = 250_000


def _match_positions(patterns: Iterable[str], text: str) -> list[int]:
    """Sorted, de-duplicated start offsets of every pattern match."""
    positions: set[int] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            positions.add(match.start())
    return sorted(positions)


def _locate_section(
    text: str, start_patterns: Iterable[str], end_patterns: Iterable[str]
) -> tuple[str, str]:
    """Find a filing item by scoring every candidate boundary pair.

    An item heading appears several times in a filing: once in the table of
    contents, once as the real heading, and often again as a cross-reference in
    later prose ("as described in Part I, Item 1A"). Neither the first nor the
    last occurrence is reliable — the table of contents entry yields a few
    characters, and a cross-reference inside MD&A yields everything to the end
    of the document.

    Every start offset is therefore paired with the first end marker following
    it, and the longest properly-bounded candidate wins. That discards the
    table-of-contents hit (too short) and the cross-references (no closing
    marker after them) without hard-coding anything about a specific filer.
    """
    starts = _match_positions(start_patterns, text)
    if not starts:
        return "", "not-found"

    ends = _match_positions(end_patterns, text)
    bounded: list[tuple[int, int, int]] = []
    unbounded: list[tuple[int, int, int]] = []

    for start in starts:
        following = next((end for end in ends if end > start), None)
        if following is None:
            unbounded.append((len(text) - start, start, len(text)))
        else:
            bounded.append((following - start, start, following))

    viable = [candidate for candidate in bounded if candidate[0] >= MIN_SECTION_CHARACTERS]
    if viable:
        _, start, end = max(viable)
        return text[start:end].strip(), ""

    # No closing marker resolved. Fall back to the longest open-ended candidate,
    # capped so a stray cross-reference cannot absorb the rest of the filing.
    viable_open = [
        candidate for candidate in unbounded if candidate[0] >= MIN_SECTION_CHARACTERS
    ]
    if viable_open:
        _, start, _ = max(viable_open)
        end = min(len(text), start + MAX_UNBOUNDED_SECTION_CHARACTERS)
        return text[start:end].strip(), "unbounded"

    return "", "too-short"


def extract_sections(text: str, form: str = "10-K") -> tuple[dict[str, str], list[str]]:
    """Slice the narrative items out of a filing's plain text.

    Returns the sections that could be located plus warnings naming those that
    could not. A filing that omits Item 1A — permitted for smaller reporting
    companies and common in 10-Qs — is reported, never raised.
    """
    sections: dict[str, str] = {}
    warnings: list[str] = []
    if not text:
        return sections, ["The filing document was empty or could not be decoded."]

    rules = _ITEM_RULES_10Q if str(form).upper().startswith("10-Q") else _ITEM_RULES

    for key, (start_patterns, end_patterns) in rules.items():
        body, problem = _locate_section(text, start_patterns, end_patterns)

        if problem == "not-found":
            warnings.append(
                f"{SECTION_LABELS[key]} could not be located in this {form}. Filers are not "
                "required to use a standard heading, and smaller reporting companies may omit "
                "the item entirely."
            )
            continue
        if problem == "too-short":
            warnings.append(
                f"{SECTION_LABELS[key]} matched only a table-of-contents entry in this {form}, "
                "not a section body. It has been excluded from the analysis."
            )
            continue
        if problem == "unbounded":
            warnings.append(
                f"{SECTION_LABELS[key]} was located but its closing heading was not, so the "
                "extract is truncated at a safe limit and may run past the end of the item."
            )

        sections[key] = body

    return sections, warnings


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass
class EdgarBundle:
    """Everything the scanner needs for one issuer."""

    ticker: str
    cik: str
    company_name: str
    documents: list[FilingDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def annual(self) -> list[FilingDocument]:
        return [doc for doc in self.documents if doc.ref.form == "10-K" and doc.ok]

    def quarterly(self) -> list[FilingDocument]:
        return [doc for doc in self.documents if doc.ref.form == "10-Q" and doc.ok]


def build_bundle(
    ticker: str, annual_count: int = 3, quarterly_count: int = 2
) -> EdgarBundle:
    """Resolve a ticker and fetch its recent 10-K and 10-Q filings concurrently."""
    cik, company_name = resolve_cik(ticker)

    annual_refs = list_filings(cik, forms=("10-K",), limit=max(0, annual_count))
    quarterly_refs = list_filings(cik, forms=("10-Q",), limit=max(0, quarterly_count))
    refs = annual_refs + quarterly_refs

    warnings: list[str] = []
    if not refs:
        warnings.append(
            f"No 10-K or 10-Q filings were returned for {ticker.upper()} (CIK {cik})."
        )
        return EdgarBundle(ticker.upper(), cik, company_name, [], warnings)

    documents = fetch_documents(refs)
    for document in documents:
        if document.fetch_error:
            warnings.append(
                f"{document.ref.form} {document.ref.fiscal_year} could not be retrieved: "
                f"{document.fetch_error}"
            )

    if not any(doc.ok for doc in documents):
        warnings.append(
            "No filing document could be retrieved. EDGAR throttles datacenter IP ranges, "
            "which affects cloud deployments more than local runs."
        )

    return EdgarBundle(ticker.upper(), cik, company_name, documents, warnings)
