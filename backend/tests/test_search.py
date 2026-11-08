"""arXiv search (requirement 1), tested through `search_arxiv` with arXiv's HTTP API faked.

Assertions are about what the user gets (results with title, authors, year, abstract, link) and
about the query that actually reaches arXiv, not about private helpers.
"""

import re

import httpx
import pytest

from app import search
from app.search import RateLimitedError, search_arxiv

from .fakes import FakeHTTP, redirect

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


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(search.time, "sleep", calls.append)
    return calls


def arxiv(monkeypatch, *replies) -> FakeHTTP:
    return FakeHTTP(*replies).install(monkeypatch)


def sent_query(request: httpx.Request) -> tuple[set[str], set[str]]:
    """The keywords and boolean operators in the arXiv `search_query` that was sent.

    arXiv's query syntax is `field:term` clauses joined by AND / OR / ANDNOT
    (https://info.arxiv.org/help/api/user-manual.html#query_details).
    """
    query = request.url.params["search_query"]
    terms = {t.lower() for t in re.findall(r"\b(?:all|ti|abs|au|co|jr|cat|rn):(\S+)", query)}
    operators = set(re.findall(r"\b(AND|OR|ANDNOT)\b", query))
    return terms, operators


# --- results shown to the user ------------------------------------------------------------


def test_results_have_the_fields_the_ui_displays(monkeypatch, sleeps):
    arxiv(monkeypatch, (200, ATOM_FEED))

    first = search_arxiv("sparse attention")[0]

    assert first.title == "Sparse Attention for Long Documents"  # no hard line breaks from the feed
    assert first.authors == ["Ada Lovelace", "Alan Turing"]
    assert first.year == 2024
    assert first.abstract == "We study sparse attention. It works."
    assert first.url == "http://arxiv.org/abs/2401.01234v2"
    assert first.pdf_url == "http://arxiv.org/pdf/2401.01234v2"
    assert first.external_id == "2401.01234v2"
    assert first.saved_id is None


def test_results_with_missing_optional_fields(monkeypatch, sleeps):
    arxiv(monkeypatch, (200, ATOM_FEED))

    old = search_arxiv("anything")[1]

    assert old.external_id == "hep-th/9901001v1"
    assert old.title == "Old Style Identifier"
    assert old.url == "http://arxiv.org/abs/hep-th/9901001v1"
    assert old.pdf_url is None
    assert old.year is None
    assert old.authors == []
    assert old.abstract == ""


def test_search_with_no_results(monkeypatch, sleeps):
    arxiv(monkeypatch, (200, EMPTY_FEED))
    assert search_arxiv("zzzz qqqq") == []


# --- the query sent to arXiv --------------------------------------------------------------


def test_every_keyword_is_required_and_the_limit_is_honored(monkeypatch, sleeps):
    fake = arxiv(monkeypatch, (200, EMPTY_FEED))

    search_arxiv("diffusion models", limit=5)

    request = fake.requests[0]
    assert request.url.host == "export.arxiv.org"
    terms, operators = sent_query(request)
    assert terms == {"diffusion", "models"}
    assert operators == {"AND"}
    assert request.url.params["max_results"] == "5"


def test_topic_phrases_leave_out_stop_words(monkeypatch, sleeps):
    # arXiv doesn't index stop words, so requiring one would match almost nothing
    fake = arxiv(monkeypatch, (200, EMPTY_FEED))

    search_arxiv("The Role of Attention in Transformers")

    terms, _ = sent_query(fake.requests[0])
    assert terms == {"role", "attention", "transformers"}


def test_hyphenated_keywords_stay_whole_and_punctuation_is_ignored(monkeypatch, sleeps):
    fake = arxiv(monkeypatch, (200, EMPTY_FEED))

    search_arxiv("self-supervised, learning!")

    terms, _ = sent_query(fake.requests[0])
    assert terms == {"self-supervised", "learning"}


def test_query_of_only_common_words_still_searches(monkeypatch, sleeps):
    fake = arxiv(monkeypatch, (200, EMPTY_FEED))

    search_arxiv("what is it")

    terms, _ = sent_query(fake.requests[0])
    assert terms and terms <= {"what", "is", "it"}


def test_input_without_any_words_does_not_call_arxiv(monkeypatch):
    fake = arxiv(monkeypatch)
    assert search_arxiv("?!  ...") == []
    assert fake.requests == []


# --- errors and rate limits -----------------------------------------------------------------


def test_redirects_are_followed(monkeypatch, sleeps):
    fake = arxiv(monkeypatch, redirect("https://export.arxiv.org/api/query?moved=1"), (200, ATOM_FEED))
    assert len(search_arxiv("attention")) == 2
    assert len(fake.requests) == 2


@pytest.mark.parametrize("status", [429, 503])
def test_waits_and_retries_once_when_rate_limited(monkeypatch, sleeps, status):
    fake = arxiv(monkeypatch, (status, ""), (200, ATOM_FEED))

    results = search_arxiv("attention")

    assert len(results) == 2
    assert len(fake.requests) == 2
    assert len(sleeps) == 1 and sleeps[0] > 0


@pytest.mark.parametrize("status", [429, 503])
def test_reports_rate_limit_when_retry_also_fails(monkeypatch, sleeps, status):
    fake = arxiv(monkeypatch, (status, ""), (429, ""))

    with pytest.raises(RateLimitedError, match="try again in a minute"):
        search_arxiv("attention")
    assert len(fake.requests) == 2


def test_server_error_is_raised_without_retry(monkeypatch, sleeps):
    fake = arxiv(monkeypatch, (500, "boom"))

    with pytest.raises(httpx.HTTPStatusError):
        search_arxiv("attention")
    assert len(fake.requests) == 1
    assert sleeps == []


def test_connection_errors_propagate(monkeypatch):
    arxiv(monkeypatch, httpx.ConnectError("no route"))
    with pytest.raises(httpx.ConnectError):
        search_arxiv("attention")
