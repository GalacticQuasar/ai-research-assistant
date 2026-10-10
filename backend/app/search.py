"""Paper search via the public arXiv API (https://info.arxiv.org/help/api/)."""

import re
import time

import feedparser
import httpx

from .models import SearchResult

ARXIV_API = "https://export.arxiv.org/api/query"
USER_AGENT = "ai-research-assistant/0.1 (course project)"


class RateLimitedError(Exception):
    pass


def _clean(text: str) -> str:
    # arXiv titles/abstracts contain hard line breaks and runs of spaces
    return re.sub(r"\s+", " ", text).strip()


# arXiv doesn't index stop words, so requiring one (e.g. "all:is") matches almost nothing
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "how", "in", "into", "is", "it", "of", "on", "or", "the", "to", "via", "we", "what",
    "when", "which", "with", "you", "your",
}


def _build_query(q: str) -> str:
    # Require every keyword to appear somewhere in the paper (title, abstract, etc.)
    terms = re.findall(r"[\w\-]+", q)
    keywords = [t for t in terms if t.lower() not in STOP_WORDS] or terms
    return " AND ".join(f"all:{t}" for t in keywords)


def search_arxiv(q: str, limit: int = 10) -> list[SearchResult]:
    query = _build_query(q)
    if not query:
        return []

    params = {
        "search_query": query,
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
    }
    # arXiv rate-limits aggressively (HTTP 429); wait and retry once before giving up
    for attempt in range(2):
        resp = httpx.get(
            ARXIV_API,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        if resp.status_code not in (429, 503) or attempt == 1:
            break
        time.sleep(3)
    if resp.status_code in (429, 503):
        raise RateLimitedError("arXiv is rate-limiting requests. Please try again in a minute.")
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    results = []
    for entry in feed.entries:
        # entry.id looks like "http://arxiv.org/abs/2401.01234v2"
        external_id = entry.id.rsplit("/abs/", 1)[-1]
        pdf_url = next(
            (link.href for link in entry.get("links", []) if link.get("type") == "application/pdf"),
            None,
        )
        published = entry.get("published_parsed")
        results.append(
            SearchResult(
                external_id=external_id,
                title=_clean(entry.title),
                authors=[a.name for a in entry.get("authors", [])],
                year=published.tm_year if published else None,
                abstract=_clean(entry.get("summary", "")),
                url=entry.link,
                pdf_url=pdf_url,
            )
        )
    return results
