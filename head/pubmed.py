"""Evidence search via NCBI PubMed E-utilities (recent, peer-reviewed, full-text-filtered).

The fabrication-proof citation engine for the nursing-tutor feature (#10): returns REAL article metadata
so the model can cite only what actually exists, with a ready-to-use APA 7th reference per result. Two
calls (esearch -> esummary), no API key required (NCBI allows 3 req/s). Fail-soft: returns a string,
never raises into a query. Mirrors websearch.search().
"""
import time
from xml.etree import ElementTree as ET
import httpx
import config


def _eutils():
    return config.PUBMED_EUTILS_URL.rstrip("/")


def _key_params():
    return {"api_key": config.NCBI_API_KEY} if config.NCBI_API_KEY else {}


_last_call = [0.0]


def _pause(seconds):  # indirection so tests can disable the real sleep
    if seconds > 0:
        time.sleep(seconds)


def _get(url, params):
    """Throttled GET that stays under NCBI's rate limit (3/s, or 10/s with an api_key) and retries a 429
    with backoff. Raises on a final error so callers' fail-soft handler returns a clean message."""
    interval = 0.11 if config.NCBI_API_KEY else 0.35
    r = None
    for attempt in range(3):
        _pause(interval - (time.monotonic() - _last_call[0]))
        r = httpx.get(url, params=params, timeout=config.EVIDENCE_TIMEOUT)
        _last_call[0] = time.monotonic()
        if r.status_code == 429:
            _pause(1.0 * (attempt + 1))
            continue
        break
    r.raise_for_status()
    return r


def _authors_apa(authors):
    """authors = esummary author dicts ({'name': 'Smith JA', 'authtype': 'Author'}). APA author string."""
    names = []
    for a in authors:
        if a.get("authtype") not in (None, "Author"):
            continue
        nm = (a.get("name") or "").strip()
        if not nm:
            continue
        parts = nm.split()
        if len(parts) >= 2 and parts[-1].isalpha() and parts[-1].isupper():
            last = " ".join(parts[:-1])
            initials = ". ".join(list(parts[-1])) + "."
            names.append(f"{last}, {initials}")
        else:
            names.append(nm)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", ... " + names[-1]  # APA: >20 -> first 19, ellipsis, last


def _year(pubdate):
    return (pubdate or "").split(" ")[0][:4]


def _doi(item):
    for aid in item.get("articleids", []):
        if aid.get("idtype") == "doi":
            return aid.get("value", "")
    return ""


def _pmcid(item):
    for aid in item.get("articleids", []):
        if aid.get("idtype") == "pmc":
            return aid.get("value", "")
    return ""


def _apa_reference(item):
    """Build an APA 7th journal-article reference from real esummary metadata."""
    authors = _authors_apa(item.get("authors", []))
    year = _year(item.get("pubdate", ""))
    title = (item.get("title") or "").rstrip(".")
    journal = item.get("fulljournalname") or item.get("source") or ""
    vol, issue, pages, doi = (item.get("volume") or ""), (item.get("issue") or ""), \
        (item.get("pages") or ""), _doi(item)
    ref = ""
    if authors:
        ref += f"{authors} "
    if year:
        ref += f"({year}). "
    if title:
        ref += f"{title}. "
    if journal:
        ref += journal
        if vol:
            ref += f", {vol}"
            if issue:
                ref += f"({issue})"
        if pages:
            ref += f", {pages}"
        ref += ". "
    if doi:
        ref += f"https://doi.org/{doi}"
    return ref.strip()


def _esearch_ids(term, max_results):
    r = _get(f"{_eutils()}/esearch.fcgi",
             {"db": "pubmed", "term": term, "retmode": "json",
              "retmax": max_results, "sort": "relevance", **_key_params()})
    return r.json().get("esearchresult", {}).get("idlist", [])


def _fetch_abstracts(ids):
    """efetch the abstracts so the model synthesizes from real study content, not memory. {pmid: text};
    fail-soft -> {} (callers then tell the model an abstract is unavailable and not to infer findings)."""
    try:
        r = _get(f"{_eutils()}/efetch.fcgi",
                 {"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml", **_key_params()})
        root = ET.fromstring(r.content)
    except Exception:
        return {}
    out = {}
    for art in root.iter():
        if art.tag not in ("PubmedArticle", "PubmedBookArticle"):
            continue
        pmid_el = art.find(".//PMID")
        if pmid_el is None or not (pmid_el.text or "").strip():
            continue
        parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            txt = "".join(ab.itertext()).strip()
            if txt:
                label = ab.get("Label")
                parts.append(f"{label}: {txt}" if label else txt)
        if parts:
            out[pmid_el.text.strip()] = " ".join(parts)
    return out


def search(query, max_results=None, years=None, free_full_text=True):
    """Search PubMed for recent peer-reviewed articles; return a compact, citable summary.

    Prefers free-full-text within the recency window, but if that returns nothing it RELAXES the filters
    (drop free-full-text, then widen the year window) so an over-specified query still finds real sources.
    Each result is labeled with its full-text availability so the user knows where to get it (free PMC vs.
    CINAHL/library)."""
    query = (query or "").strip()
    if not query:
        return "search_evidence needs a query."
    max_results = int(max_results or config.EVIDENCE_MAX_RESULTS)
    years = int(years or config.EVIDENCE_RECENCY_YEARS)
    base = f'{query} AND ("last {years} years"[PDat])'
    wider = years + 3
    # Relaxation ladder: (search term, note shown if THIS tier is what produced hits).
    attempts = []
    if free_full_text:
        attempts.append((base + " AND free full text[Filter]", ""))
    attempts.append((base, "filters relaxed: includes articles whose full text is not free (get via CINAHL/library)"))
    attempts.append((f'{query} AND ("last {wider} years"[PDat])',
                     f"filters relaxed: widened to the last {wider} years and full text not required"))
    attempts.append((query,
                     f"WARNING: no articles within the last {wider} years were found — the results below are "
                     f"OLDER than the requested {years}-year window. Their publication year is in each APA "
                     "reference; tell the user these exceed the recency requirement and should only be used "
                     "if no current source exists."))
    try:
        ids, note = [], ""
        for term, tier_note in attempts:
            ids = _esearch_ids(term, max_results)
            if ids:
                note = tier_note
                break
        if not ids:
            return (f"No peer-reviewed PubMed articles for '{query}' at all (even with no date limit). "
                    "Check spelling or use broader/fewer key terms — do NOT assert a fact without a real "
                    "source or invent a citation.")
        su = _get(f"{_eutils()}/esummary.fcgi",
                  {"db": "pubmed", "id": ",".join(ids), "retmode": "json", **_key_params()})
        result = su.json().get("result", {})
        abstracts = _fetch_abstracts(ids)
        head = f"PubMed evidence for '{query}'"
        head += f" [{note}]" if note else " (peer-reviewed, recent, free full text)"
        lines = [head + ". Cite ONLY these (copy the APA reference verbatim); base any statement about a "
                 "study's findings ONLY on its Abstract below — do not state results not in the abstract, "
                 "and never fabricate a source:"]
        cap = config.EVIDENCE_ABSTRACT_CHARS
        for i, pmid in enumerate(ids, 1):
            item = result.get(pmid, {})
            if not item:
                continue
            pmcid = _pmcid(item)
            link = (f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/" if pmcid
                    else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            ft = f"free full text {pmcid}" if pmcid else "full text via CINAHL/library (no free PMC)"
            lines.append(f"\n{i}. {(item.get('title') or '(no title)').rstrip('.')}.")
            lines.append(f"   APA: {_apa_reference(item)}")
            lines.append(f"   {ft} | PMID {pmid} | {link}")
            ab = abstracts.get(pmid, "")
            if ab:
                lines.append(f"   Abstract: {ab[:cap]}{' […]' if len(ab) > cap else ''}")
            else:
                lines.append("   Abstract: (unavailable — do not state this study's findings unless you can verify them)")
        return "\n".join(lines)
    except Exception as exc:
        return f"Evidence search failed: {exc}"
