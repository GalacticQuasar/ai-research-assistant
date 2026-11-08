"""HTTP API: every route in app/main.py, through FastAPI's TestClient.

Uses a temp SQLite DB and data dir (see conftest). arXiv, PDF downloads, and the LLM are faked.
Each test checks the response and the resulting state (database rows, chat history, PDF files).
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.db as db
import app.llm as llm
import app.main as main
from app.llm import LLMError
from app.models import ChatMessage, Paper, SearchResult
from app.search import RateLimitedError

from .fakes import FakeHTTP
from .pdfs import SAMPLE_PAPER_PAGES, make_pdf
from .test_search import ATOM_FEED

SAVE_BODY = {
    "external_id": "2401.01234v2",
    "title": "Sparse Attention",
    "authors": ["Ada Lovelace"],
    "year": 2024,
    "abstract": "We study sparse attention.",
    "url": "http://arxiv.org/abs/2401.01234v2",
    "pdf_url": "http://arxiv.org/pdf/2401.01234v2",
}

LLM_METADATA = {"title": "LLM Title", "authors": ["Ada Lovelace"], "year": 2017, "abstract": "An abstract."}


@pytest.fixture
def lenient_client(client):
    """Like `client`, but unhandled exceptions become HTTP 500 responses instead of raising."""
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def llm_calls(monkeypatch):
    """Mock summarize/answer_question/extract_metadata in main; queue replies (str/dict or exception)."""
    state = {"replies": [], "calls": []}

    def snapshot(arg):
        # Copy what the tests check now: ORM objects are detached once the request's session closes
        if isinstance(arg, Paper):
            return {"id": arg.id, "full_text": arg.full_text}
        if isinstance(arg, list):
            return [(m.role, m.content) for m in arg]
        return arg

    def make(name):
        def fake(*args):
            state["calls"].append((name, tuple(snapshot(a) for a in args)))
            reply = state["replies"].pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return reply

        return fake

    for name in ("summarize", "answer_question", "extract_metadata"):
        monkeypatch.setattr(main, name, make(name))
    return state


def upload(client, data: bytes, filename="paper.pdf"):
    return client.post("/api/papers/upload", files={"file": (filename, data, "application/pdf")})


def chat_pairs(messages):
    return [(m.role, m.content) for m in messages]


# --- isolation and health -------------------------------------------------------------------


def test_uses_temporary_data_dir(client, data_dir, pdf_dir):
    assert db.DB_PATH.parent == data_dir
    assert main.engine.url.database == str(data_dir / "library.db")
    # The lifespan's init_db() created the PDF folder and the database in the temp dir
    assert pdf_dir.is_dir()
    assert (data_dir / "library.db").exists()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- search (requirement 1) -------------------------------------------------------------------


def test_search_end_to_end_returns_display_fields(client, monkeypatch):
    fake = FakeHTTP((200, ATOM_FEED)).install(monkeypatch)

    resp = client.get("/api/search", params={"q": "sparse attention"})

    assert resp.status_code == 200
    first = resp.json()[0]
    assert first["title"] == "Sparse Attention for Long Documents"
    assert first["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert first["year"] == 2024
    assert first["abstract"] == "We study sparse attention. It works."
    assert first["url"] == "http://arxiv.org/abs/2401.01234v2"
    assert first["saved_id"] is None
    assert fake.requests[0].url.host == "export.arxiv.org"


def make_result(external_id, **fields):
    return SearchResult(**{**SAVE_BODY, "external_id": external_id, **fields})


def test_search_marks_results_already_saved(client, add_paper, monkeypatch):
    saved = add_paper(external_id="2401.00002v1")
    calls = []

    def fake_search(q, limit):
        calls.append((q, limit))
        return [make_result("2401.00001v1"), make_result("2401.00002v1")]

    monkeypatch.setattr(main, "search_arxiv", fake_search)

    resp = client.get("/api/search", params={"q": "sparse attention", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert [r["external_id"] for r in body] == ["2401.00001v1", "2401.00002v1"]
    assert body[0]["saved_id"] is None
    assert body[1]["saved_id"] == saved.id
    assert calls == [("sparse attention", 5)]


def test_search_default_limit(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "search_arxiv", lambda q, limit: calls.append(limit) or [])
    assert client.get("/api/search", params={"q": "x"}).json() == []
    assert calls == [10]


@pytest.mark.parametrize(("params", "field"), [({}, "q"), ({"q": ""}, "q"), ({"q": "x", "limit": 0}, "limit"),
                                               ({"q": "x", "limit": 51}, "limit"),
                                               ({"q": "x", "limit": "ten"}, "limit")])
def test_search_validates_parameters(client, params, field, monkeypatch):
    monkeypatch.setattr(main, "search_arxiv", lambda q, limit: pytest.fail("should not search"))
    resp = client.get("/api/search", params=params)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["query", field]


def test_search_rate_limited_returns_503(client, monkeypatch):
    def fake_search(q, limit):
        raise RateLimitedError("arXiv is rate-limiting requests. Please try again in a minute.")

    monkeypatch.setattr(main, "search_arxiv", fake_search)
    resp = client.get("/api/search", params={"q": "x"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "arXiv is rate-limiting requests. Please try again in a minute."


def test_search_upstream_error_returns_502(client, monkeypatch):
    def fake_search(q, limit):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(main, "search_arxiv", fake_search)
    resp = client.get("/api/search", params={"q": "x"})
    assert resp.status_code == 502
    assert resp.json()["detail"].startswith("arXiv search failed")


# --- save paper + background full text (requirement 2) ----------------------------------------


def test_save_paper_downloads_full_text_in_background(client, fake_download, stored):
    resp = client.post("/api/papers", json=SAVE_BODY)

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "arxiv"
    assert body["title"] == "Sparse Attention"
    assert body["text_status"] == "pending"  # the response is sent before the task runs
    assert body["has_pdf"] is False
    assert "full_text" not in body and "pdf_path" not in body

    # TestClient runs background tasks before returning, so the text is loaded now
    assert fake_download["calls"] == [(SAVE_BODY["pdf_url"], stored.pdf_dir / f"{body['id']}.pdf")]
    paper = stored.paper(body["id"])
    assert paper.text_status == "ready"
    assert "We propose a new sparse kernel." in paper.full_text
    assert stored.pdf_files() == [f"{body['id']}.pdf"]
    assert client.get(f"/api/papers/{body['id']}").json()["has_pdf"] is True


def test_save_paper_stores_the_search_metadata(client, fake_download, stored):
    paper_id = client.post("/api/papers", json=SAVE_BODY).json()["id"]

    paper = stored.paper(paper_id)
    assert paper.source == "arxiv"
    for field, value in SAVE_BODY.items():
        assert getattr(paper, field) == value, field


def test_save_paper_twice_returns_existing(client, fake_download, stored):
    first = client.post("/api/papers", json=SAVE_BODY)
    second = client.post("/api/papers", json={**SAVE_BODY, "title": "Different"})

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["title"] == "Sparse Attention"
    assert [p.title for p in stored.papers()] == ["Sparse Attention"]
    assert len(fake_download["calls"]) == 1


def test_save_paper_without_pdf_url_fails_immediately(client, fake_download, stored):
    resp = client.post("/api/papers", json={**SAVE_BODY, "pdf_url": None})

    assert resp.status_code == 201
    assert resp.json()["text_status"] == "failed"
    assert fake_download["calls"] == []
    paper = stored.paper(resp.json()["id"])
    assert paper.text_status == "failed"
    assert paper.full_text is None and paper.pdf_path is None
    assert stored.pdf_files() == []


def test_save_paper_ignores_extra_fields(client, fake_download, stored):
    # The frontend sends a SearchResult, which has an extra saved_id field
    resp = client.post("/api/papers", json={**SAVE_BODY, "saved_id": 99, "id": 1234, "summary": "injected"})

    assert resp.status_code == 201
    paper = stored.paper(resp.json()["id"])
    assert paper.id != 1234
    assert paper.title == SAVE_BODY["title"]
    assert paper.external_id == SAVE_BODY["external_id"]
    assert paper.summary is None


@pytest.mark.parametrize("missing", ["external_id", "title", "authors", "abstract", "url"])
def test_save_paper_requires_fields(client, fake_download, stored, missing):
    body = {k: v for k, v in SAVE_BODY.items() if k != missing}
    resp = client.post("/api/papers", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", missing]
    assert stored.papers() == []
    assert fake_download["calls"] == []


def test_failed_download_marks_failed_and_removes_partial_file(client, fake_download, stored):
    fake_download["error"] = httpx.ConnectError("network down")

    paper_id = client.post("/api/papers", json=SAVE_BODY).json()["id"]

    paper = stored.paper(paper_id)
    assert paper.text_status == "failed"
    assert paper.pdf_path is None and paper.full_text is None
    assert stored.pdf_files() == []
    assert client.get(f"/api/papers/{paper_id}").json()["has_pdf"] is False


def test_download_of_pdf_without_text_marks_failed(client, fake_download, stored):
    fake_download["pages"] = [[]]

    paper_id = client.post("/api/papers", json=SAVE_BODY).json()["id"]

    paper = stored.paper(paper_id)
    assert paper.text_status == "failed"
    assert not paper.full_text
    # The PDF itself is still kept so it can be viewed
    assert stored.pdf_files() == [f"{paper_id}.pdf"]


def test_fetch_full_text_ignores_missing_or_linkless_papers(client, add_paper, fake_download, stored):
    main.fetch_full_text(9999)
    paper = add_paper(pdf_url=None, text_status="failed", full_text=None)

    main.fetch_full_text(paper.id)

    assert fake_download["calls"] == []
    assert stored.paper(paper.id).text_status == "failed"
    assert stored.pdf_files() == []


# --- retry full text --------------------------------------------------------------------------


def test_retry_fetch_text_reloads_failed_paper(client, add_paper, fake_download, stored):
    paper = add_paper(text_status="failed", full_text=None)

    resp = client.post(f"/api/papers/{paper.id}/fetch-text")

    assert resp.status_code == 200
    assert resp.json()["text_status"] == "pending"
    assert len(fake_download["calls"]) == 1
    reloaded = stored.paper(paper.id)
    assert reloaded.text_status == "ready"
    assert "Attention Is Mostly What You Need" in reloaded.full_text


def test_retry_fetch_text_without_pdf_url(client, add_paper, stored):
    paper = add_paper(pdf_url=None, text_status="failed", full_text=None)
    resp = client.post(f"/api/papers/{paper.id}/fetch-text")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This paper has no PDF link"
    assert stored.paper(paper.id).text_status == "failed"


def test_retry_fetch_text_missing_paper(client, fake_download, stored):
    resp = client.post("/api/papers/999/fetch-text")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paper not found"
    assert fake_download["calls"] == []
    assert stored.papers() == []


# --- upload (requirement 3) -------------------------------------------------------------------


def test_upload_uses_llm_metadata(client, llm_calls, stored):
    llm_calls["replies"] = [{"title": "LLM\nTitle", "authors": ["Ada Lovelace"], "year": 2017,
                             "abstract": "An abs-\ntract here."}]

    resp = upload(client, make_pdf(SAMPLE_PAPER_PAGES))

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "upload"
    assert body["title"] == "LLM Title"  # line breaks cleaned
    assert body["abstract"] == "An abstract here."
    assert body["authors"] == ["Ada Lovelace"]
    assert body["year"] == 2017
    assert body["text_status"] == "ready"
    assert body["has_pdf"] is True
    assert body["external_id"] is None
    name, args = llm_calls["calls"][0]
    assert name == "extract_metadata" and "Attention Is Mostly What You Need" in args[0]
    paper = stored.paper(body["id"])
    assert "We propose a new sparse kernel." in paper.full_text
    files = stored.pdf_files()
    assert len(files) == 1 and files[0].startswith("upload-")
    assert client.get(f"/api/papers/{body['id']}/pdf").content == make_pdf(SAMPLE_PAPER_PAGES)


def test_upload_falls_back_to_heuristics_when_llm_fails(client, llm_calls, stored):
    llm_calls["replies"] = [LLMError("GENAI_API_KEY is not set")]

    resp = upload(client, make_pdf(SAMPLE_PAPER_PAGES))

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Attention Is Mostly What You Need"
    assert body["abstract"] == "We study sparse attention for long documents and show it matches dense attention."
    assert body["authors"] == []
    assert body["year"] is None
    assert body["text_status"] == "ready"
    assert stored.paper(body["id"]).title == "Attention Is Mostly What You Need"


def test_upload_falls_back_when_llm_returns_non_object_json(client, monkeypatch, stored):
    # End to end through the real extract_metadata: a JSON `null` reply must not break the upload
    monkeypatch.setattr(llm, "_complete", lambda messages: "null")
    lenient = TestClient(main.app, raise_server_exceptions=False)

    resp = upload(lenient, make_pdf(SAMPLE_PAPER_PAGES))

    assert resp.status_code == 201
    assert resp.json()["title"] == "Attention Is Mostly What You Need"
    assert len(stored.papers()) == 1


def test_upload_scanned_pdf_has_failed_status_and_skips_llm(client, llm_calls, stored):
    resp = upload(client, make_pdf([[]]), filename="scan-2020.pdf")

    assert resp.status_code == 201
    body = resp.json()
    assert body["text_status"] == "failed"
    assert body["title"] == "scan-2020"
    assert body["has_pdf"] is True  # it can still be viewed
    assert llm_calls["calls"] == []
    paper = stored.paper(body["id"])
    assert paper.full_text is None
    assert len(stored.pdf_files()) == 1


def test_upload_rejects_non_pdf(client, stored):
    resp = upload(client, b"hello, I am a text file", filename="notes.pdf")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "The uploaded file is not a PDF"
    assert stored.papers() == []
    assert stored.pdf_files() == []


def test_upload_rejects_empty_file(client, stored):
    resp = upload(client, b"")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "The uploaded file is not a PDF"
    assert stored.papers() == []
    assert stored.pdf_files() == []


def test_upload_rejects_oversized_file(client, monkeypatch, stored):
    data = make_pdf(SAMPLE_PAPER_PAGES)
    monkeypatch.setattr(main, "MAX_PDF_BYTES", len(data) - 1)
    resp = upload(client, data)
    assert resp.status_code == 413
    assert resp.json()["detail"] == "PDF is larger than 50 MB"
    assert stored.papers() == []
    assert stored.pdf_files() == []


def test_upload_accepts_file_exactly_at_size_limit(client, monkeypatch, llm_calls, stored):
    data = make_pdf(SAMPLE_PAPER_PAGES)
    monkeypatch.setattr(main, "MAX_PDF_BYTES", len(data))
    llm_calls["replies"] = [dict(LLM_METADATA)]

    resp = upload(client, data)

    assert resp.status_code == 201
    paper = stored.paper(resp.json()["id"])
    assert paper.title == "LLM Title"
    assert paper.text_status == "ready"
    # The whole file was stored, not cut off at the limit
    assert client.get(f"/api/papers/{paper.id}/pdf").content == data


def test_upload_rejects_corrupt_pdf_and_cleans_up(client, stored):
    resp = upload(client, b"%PDF-1.4\nthis is not really a pdf")
    assert resp.status_code == 400
    assert "Could not read this PDF" in resp.json()["detail"]
    assert stored.papers() == []
    assert stored.pdf_files() == []


def test_upload_requires_file(client, stored):
    resp = client.post("/api/papers/upload")
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "file"]
    assert stored.papers() == []


# --- list / get (requirement 2: browse saved papers) ------------------------------------------


def test_list_papers_newest_first(client, add_paper):
    now = datetime.now(timezone.utc)
    add_paper(external_id="old", title="Old", created_at=now - timedelta(days=2))
    add_paper(external_id="new", title="New", created_at=now)
    add_paper(external_id="mid", title="Mid", created_at=now - timedelta(days=1))

    titles = [p["title"] for p in client.get("/api/papers").json()]

    assert titles == ["New", "Mid", "Old"]


def test_list_papers_empty(client):
    assert client.get("/api/papers").json() == []


def test_get_paper(client, add_paper):
    paper = add_paper(pdf_path="/somewhere/1.pdf", summary="A summary")

    body = client.get(f"/api/papers/{paper.id}").json()

    assert body["id"] == paper.id
    assert body["title"] == "A Test Paper"
    assert body["authors"] == ["Ada Lovelace"]
    assert body["has_pdf"] is True
    assert body["summary"] == "A summary"
    assert "full_text" not in body and "pdf_path" not in body


def test_get_missing_paper(client):
    resp = client.get("/api/papers/12345")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paper not found"


def test_get_paper_with_non_integer_id(client):
    resp = client.get("/api/papers/abc")
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["path", "paper_id"]


# --- PDF ------------------------------------------------------------------------------------


def test_get_pdf_serves_local_file(client, add_paper, pdf_dir):
    data = make_pdf(SAMPLE_PAPER_PAGES)
    path = pdf_dir / "1.pdf"
    path.write_bytes(data)
    paper = add_paper(pdf_path=str(path))

    resp = client.get(f"/api/papers/{paper.id}/pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == data


def test_get_pdf_without_local_copy(client, add_paper):
    paper = add_paper(pdf_path=None)
    resp = client.get(f"/api/papers/{paper.id}/pdf")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No local PDF for this paper"


def test_get_pdf_missing_paper(client):
    resp = client.get("/api/papers/999/pdf")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paper not found"


def test_get_pdf_when_file_was_removed_from_disk(lenient_client, add_paper, pdf_dir):
    # e.g. the user cleared data/pdfs; the API should say the PDF is gone, not crash
    paper = add_paper(pdf_path=str(pdf_dir / "gone.pdf"))
    assert lenient_client.get(f"/api/papers/{paper.id}/pdf").status_code == 404


# --- delete ---------------------------------------------------------------------------------


def test_delete_paper_removes_pdf_and_chat(client, add_paper, session, stored, pdf_dir):
    path = pdf_dir / "1.pdf"
    path.write_bytes(b"%PDF-1.4")
    paper = add_paper(pdf_path=str(path))
    other = add_paper(external_id="other")
    session.add(ChatMessage(paper_id=paper.id, role="user", content="Q"))
    session.add(ChatMessage(paper_id=other.id, role="user", content="keep me"))
    session.commit()

    resp = client.delete(f"/api/papers/{paper.id}")

    assert resp.status_code == 204
    assert resp.content == b""
    assert stored.pdf_files() == []
    assert [p.id for p in stored.papers()] == [other.id]
    assert [m.content for m in stored.messages()] == ["keep me"]
    assert client.get(f"/api/papers/{paper.id}").status_code == 404


def test_delete_paper_whose_pdf_is_already_gone(client, add_paper, session, stored, pdf_dir):
    paper = add_paper(pdf_path=str(pdf_dir / "missing.pdf"))
    session.add(ChatMessage(paper_id=paper.id, role="user", content="Q"))
    session.commit()

    assert client.delete(f"/api/papers/{paper.id}").status_code == 204

    assert stored.paper(paper.id) is None
    assert stored.messages() == []


def test_delete_missing_paper(client, add_paper, stored):
    paper = add_paper()
    resp = client.delete("/api/papers/999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paper not found"
    assert [p.id for p in stored.papers()] == [paper.id]


def test_delete_twice(client, add_paper, stored):
    paper = add_paper()
    assert client.delete(f"/api/papers/{paper.id}").status_code == 204
    resp = client.delete(f"/api/papers/{paper.id}")
    assert resp.status_code == 404
    assert stored.papers() == []


# --- summary (requirement 4) ------------------------------------------------------------------


def test_generate_summary(client, add_paper, llm_calls, stored):
    paper = add_paper()
    llm_calls["replies"] = ["## TL;DR\nIt works."]

    resp = client.post(f"/api/papers/{paper.id}/summary")

    assert resp.status_code == 200
    assert resp.json()["summary"] == "## TL;DR\nIt works."
    # The LLM received the paper's full text, not just its metadata
    assert llm_calls["calls"][0][1][0]["full_text"] == "Full text of the test paper."
    assert stored.paper(paper.id).summary == "## TL;DR\nIt works."


def test_cached_summary_is_returned_without_llm_call(client, add_paper, llm_calls, stored):
    paper = add_paper(summary="Cached")
    resp = client.post(f"/api/papers/{paper.id}/summary")
    assert resp.status_code == 200
    assert resp.json()["summary"] == "Cached"
    assert llm_calls["calls"] == []
    assert stored.paper(paper.id).summary == "Cached"


def test_regenerate_summary(client, add_paper, llm_calls, stored):
    paper = add_paper(summary="Old")
    llm_calls["replies"] = ["New"]
    resp = client.post(f"/api/papers/{paper.id}/summary", params={"regenerate": "true"})
    assert resp.status_code == 200
    assert resp.json()["summary"] == "New"
    assert len(llm_calls["calls"]) == 1
    assert stored.paper(paper.id).summary == "New"


@pytest.mark.parametrize(
    ("status", "text", "message"),
    [
        ("pending", None, "still being processed"),
        ("failed", None, "not available"),
        ("ready", None, "not available"),
        ("ready", "", "not available"),
    ],
)
def test_summary_requires_full_text(client, add_paper, llm_calls, stored, status, text, message):
    paper = add_paper(text_status=status, full_text=text)
    resp = client.post(f"/api/papers/{paper.id}/summary")
    assert resp.status_code == 409
    assert message in resp.json()["detail"]
    assert llm_calls["calls"] == []
    assert stored.paper(paper.id).summary is None


def test_summary_llm_error_returns_502_and_keeps_old_summary(client, add_paper, llm_calls, stored):
    paper = add_paper(summary="Old")
    llm_calls["replies"] = [LLMError("LLM request failed: overloaded")]

    resp = client.post(f"/api/papers/{paper.id}/summary", params={"regenerate": "true"})

    assert resp.status_code == 502
    assert resp.json()["detail"] == "LLM request failed: overloaded"
    assert stored.paper(paper.id).summary == "Old"


def test_summary_missing_paper(client, llm_calls):
    resp = client.post("/api/papers/999/summary")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paper not found"
    assert llm_calls["calls"] == []


# --- chat (requirement 5) ---------------------------------------------------------------------


def test_chat_starts_empty(client, add_paper):
    paper = add_paper()
    resp = client.get(f"/api/papers/{paper.id}/chat")
    assert resp.status_code == 200
    assert resp.json() == []


def test_ask_question_stores_exchange(client, add_paper, llm_calls, stored):
    paper = add_paper()
    llm_calls["replies"] = ["It uses CIFAR-10."]

    resp = client.post(f"/api/papers/{paper.id}/chat", json={"question": "What dataset?"})

    assert resp.status_code == 200
    messages = resp.json()
    assert [(m["role"], m["content"]) for m in messages] == [("user", "What dataset?"), ("assistant", "It uses CIFAR-10.")]
    assert all(m["paper_id"] == paper.id and m["id"] for m in messages)
    assert chat_pairs(stored.messages(paper.id)) == [("user", "What dataset?"), ("assistant", "It uses CIFAR-10.")]
    assert client.get(f"/api/papers/{paper.id}/chat").json() == messages


def test_follow_up_question_receives_history(client, add_paper, llm_calls, stored):
    paper = add_paper()
    llm_calls["replies"] = ["A1", "A2"]

    client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q1"})
    client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q2"})

    _, (sent_paper, history, question) = llm_calls["calls"][1]
    assert sent_paper["id"] == paper.id
    assert sent_paper["full_text"] == "Full text of the test paper."
    assert history == [("user", "Q1"), ("assistant", "A1")]
    assert question == "Q2"
    assert [m.content for m in stored.messages(paper.id)] == ["Q1", "A1", "Q2", "A2"]


def test_chat_history_is_per_paper(client, add_paper, llm_calls, stored):
    a = add_paper(external_id="a")
    b = add_paper(external_id="b")
    llm_calls["replies"] = ["answer"]
    client.post(f"/api/papers/{a.id}/chat", json={"question": "Q"})
    assert client.get(f"/api/papers/{b.id}/chat").json() == []
    assert stored.messages(b.id) == []
    assert len(stored.messages(a.id)) == 2


def test_ask_question_llm_error_stores_nothing(client, add_paper, llm_calls, stored):
    paper = add_paper()
    llm_calls["replies"] = [LLMError("The LLM service is rate-limiting requests. Please try again in a minute.")]

    resp = client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q"})

    assert resp.status_code == 502
    assert "rate-limiting" in resp.json()["detail"]
    assert stored.messages() == []


@pytest.mark.parametrize(("status", "message"), [("pending", "still being processed"), ("failed", "not available")])
def test_ask_question_requires_full_text(client, add_paper, llm_calls, stored, status, message):
    paper = add_paper(text_status=status, full_text=None)
    resp = client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q"})
    assert resp.status_code == 409
    assert message in resp.json()["detail"]
    assert llm_calls["calls"] == []
    assert stored.messages() == []


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 2001}, {"question": 5}])
def test_ask_question_validates_input(client, add_paper, llm_calls, stored, body):
    paper = add_paper()
    resp = client.post(f"/api/papers/{paper.id}/chat", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"][:2] == ["body", "question"]
    assert llm_calls["calls"] == []
    assert stored.messages() == []


def test_ask_question_accepts_max_length(client, add_paper, llm_calls, stored):
    paper = add_paper()
    llm_calls["replies"] = ["ok"]
    question = "x" * 2000
    assert client.post(f"/api/papers/{paper.id}/chat", json={"question": question}).status_code == 200
    assert llm_calls["calls"][0][1][2] == question
    assert chat_pairs(stored.messages(paper.id)) == [("user", question), ("assistant", "ok")]


def test_chat_routes_missing_paper(client, llm_calls, stored):
    for resp in (
        client.get("/api/papers/999/chat"),
        client.post("/api/papers/999/chat", json={"question": "Q"}),
        client.delete("/api/papers/999/chat"),
    ):
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Paper not found"
    assert llm_calls["calls"] == []
    assert stored.messages() == []


def test_clear_chat(client, add_paper, llm_calls, stored):
    paper = add_paper()
    other = add_paper(external_id="other")
    llm_calls["replies"] = ["A", "B"]
    client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q"})
    client.post(f"/api/papers/{other.id}/chat", json={"question": "Q"})

    resp = client.delete(f"/api/papers/{paper.id}/chat")

    assert resp.status_code == 204
    assert stored.messages(paper.id) == []
    assert chat_pairs(stored.messages(other.id)) == [("user", "Q"), ("assistant", "B")]
    # Clearing the chat keeps the paper itself
    assert stored.paper(paper.id) is not None
