"""arXiv client: query building, Atom parsing, and rate-limit/error handling (httpx.get is mocked)."""

import httpx
import pytest

from app import search
from app.search import RateLimitedError, _build_query, _clean, search_arxiv

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>arXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <published>2024-01-03T18:00:00Z</published>
    <title>Sparse   Attention
      for Long Documents</title>
    <summary>  We study sparse
      attention.   It works.  </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/2401.01234v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.01234v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <title>Old Style Identifier</title>
    <link href="http://arxiv.org/abs/hep-th/9901001v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>arXiv Query</title></feed>
"""


class FakeGet:
    """Stand-in for httpx.get that returns queued responses and records each call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status, text = self.responses.pop(0)
        return httpx.Response(status, text=text, request=httpx.Request("GET", url))


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(search.time, "sleep", calls.append)
    return calls


def mock_get(monkeypatch, *responses) -> FakeGet:
    fake = FakeGet(*responses)
    monkeypatch.setattr(search.httpx, "get", fake)
    return fake


# --- _clean -------------------------------------------------------------------------------


def test_clean_collapses_whitespace_and_line_breaks():
    assert _clean("  Deep\n   learning\tfor\r\n everyone  ") == "Deep learning for everyone"


def test_clean_empty_string():
    assert _clean("   \n ") == ""


# --- _build_query -------------------------------------------------------------------------


def test_build_query_requires_every_keyword():
    assert _build_query("diffusion models") == "all:diffusion AND all:models"


def test_build_query_drops_stop_words_case_insensitively():
    assert _build_query("The Role of Attention in Transformers") == "all:Role AND all:Attention AND all:Transformers"


def test_build_query_keeps_hyphenated_terms_and_drops_punctuation():
    assert _build_query("self-supervised, learning!") == "all:self-supervised AND all:learning"


def test_build_query_falls_back_to_all_terms_when_only_stop_words():
    # Better to send something than nothing at all
    assert _build_query("what is it") == "all:what AND all:is AND all:it"


def test_build_query_empty_when_no_word_characters():
    assert _build_query("?!  ...") == ""


# --- search_arxiv -------------------------------------------------------------------------


def test_search_parses_entries(monkeypatch, sleeps):
    fake = mock_get(monkeypatch, (200, ATOM_FEED))

    results = search_arxiv("sparse attention", limit=5)

    assert len(results) == 2
    first = results[0]
    assert first.external_id == "2401.01234v2"
    assert first.title == "Sparse Attention for Long Documents"
    assert first.abstract == "We study sparse attention. It works."
    assert first.authors == ["Ada Lovelace", "Alan Turing"]
    assert first.year == 2024
    assert first.url == "http://arxiv.org/abs/2401.01234v2"
    assert first.pdf_url == "http://arxiv.org/pdf/2401.01234v2"
    assert first.saved_id is None
    assert sleeps == []

    url, kwargs = fake.calls[0]
    assert url == search.ARXIV_API
    assert kwargs["params"]["search_query"] == "all:sparse AND all:attention"
    assert kwargs["params"]["max_results"] == 5
    assert kwargs["params"]["sortBy"] == "relevance"
    assert kwargs["headers"]["User-Agent"] == search.USER_AGENT


def test_search_handles_missing_optional_fields(monkeypatch, sleeps):
    mock_get(monkeypatch, (200, ATOM_FEED))

    old = search_arxiv("anything")[1]

    assert old.external_id == "hep-th/9901001v1"
    assert old.pdf_url is None
    assert old.year is None
    assert old.authors == []
    assert old.abstract == ""


def test_search_with_no_results(monkeypatch, sleeps):
    mock_get(monkeypatch, (200, EMPTY_FEED))
    assert search_arxiv("zzzz qqqq") == []


def test_search_without_keywords_skips_request(monkeypatch):
    fake = mock_get(monkeypatch)
    assert search_arxiv("???") == []
    assert fake.calls == []


@pytest.mark.parametrize("status", [429, 503])
def test_search_retries_once_after_rate_limit(monkeypatch, sleeps, status):
    fake = mock_get(monkeypatch, (status, ""), (200, ATOM_FEED))

    results = search_arxiv("attention")

    assert len(results) == 2
    assert len(fake.calls) == 2
    assert sleeps == [3]


@pytest.mark.parametrize("status", [429, 503])
def test_search_raises_rate_limited_after_two_failures(monkeypatch, sleeps, status):
    fake = mock_get(monkeypatch, (status, ""), (429, ""))

    with pytest.raises(RateLimitedError, match="rate-limiting"):
        search_arxiv("attention")
    assert len(fake.calls) == 2
    assert sleeps == [3]


def test_search_raises_http_error_on_server_error_without_retry(monkeypatch, sleeps):
    fake = mock_get(monkeypatch, (500, "boom"))

    with pytest.raises(httpx.HTTPStatusError):
        search_arxiv("attention")
    assert len(fake.calls) == 1
    assert sleeps == []


def test_search_propagates_connection_errors(monkeypatch):
    def fail(url, **kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("GET", url))

    monkeypatch.setattr(search.httpx, "get", fail)
    with pytest.raises(httpx.ConnectError):
        search_arxiv("attention")
