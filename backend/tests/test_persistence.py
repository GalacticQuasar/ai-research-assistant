"""Requirement 2: saved papers remain after the app restarts.

The first app instance saves an arXiv paper, uploads a PDF, generates a summary, and holds a
conversation. Then it shuts down: its lifespan ends and its engine is disposed. A fresh engine is
built on the same SQLite file, `app.main` is reloaded to get a brand-new FastAPI app, and
everything is checked again through the new app.
"""

import importlib

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

import app.db as db
import app.main as main
from app.models import ChatMessage, Paper

from .pdfs import SAMPLE_PAPER_PAGES, make_pdf

SAVE_BODY = {
    "external_id": "2401.01234v2",
    "title": "Sparse Attention",
    "authors": ["Ada Lovelace", "Alan Turing"],
    "year": 2024,
    "abstract": "We study sparse attention.",
    "url": "http://arxiv.org/abs/2401.01234v2",
    "pdf_url": "http://arxiv.org/pdf/2401.01234v2",
}
UPLOAD_METADATA = {"title": "Uploaded Paper", "authors": ["Grace Hopper"], "year": 2019, "abstract": "Uploaded abs."}


def test_library_survives_an_app_restart(data_dir, fake_download, monkeypatch):
    monkeypatch.setattr(main, "extract_metadata", lambda text: dict(UPLOAD_METADATA))
    monkeypatch.setattr(main, "summarize", lambda paper: "## TL;DR\nSparse attention works.")
    monkeypatch.setattr(main, "answer_question", lambda paper, history, question: "It uses ImageNet.")
    pdf_bytes = make_pdf(SAMPLE_PAPER_PAGES)
    first_app = main.app

    # --- first run ------------------------------------------------------------------------
    with TestClient(first_app) as client:
        saved = client.post("/api/papers", json=SAVE_BODY).json()
        uploaded = client.post(
            "/api/papers/upload", files={"file": ("mine.pdf", pdf_bytes, "application/pdf")}
        ).json()
        assert client.post(f"/api/papers/{saved['id']}/summary").status_code == 200
        assert client.post(f"/api/papers/{uploaded['id']}/chat", json={"question": "What datasets?"}).status_code == 200

        papers_before = client.get("/api/papers").json()
        chat_before = client.get(f"/api/papers/{uploaded['id']}/chat").json()
        saved_pdf_before = client.get(f"/api/papers/{saved['id']}/pdf").content
        assert len(papers_before) == 2
        assert {p["text_status"] for p in papers_before} == {"ready"}
        assert len(chat_before) == 2

    # --- shut down and start again ----------------------------------------------------------
    db_file = main.engine.url.database
    main.engine.dispose()
    fresh_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", fresh_engine)
    try:
        importlib.reload(main)  # a new FastAPI app, bound to the fresh engine
        assert main.app is not first_app
        assert main.engine is fresh_engine

        # Read the file directly, independent of the app
        with Session(fresh_engine) as s:
            rows = {p.id: p for p in s.exec(select(Paper)).all()}
            assert set(rows) == {saved["id"], uploaded["id"]}
            assert "We propose a new sparse kernel." in rows[saved["id"]].full_text
            assert "We propose a new sparse kernel." in rows[uploaded["id"]].full_text
            assert len(s.exec(select(ChatMessage)).all()) == 2

        with TestClient(main.app) as client:
            # Browse the library: same papers, same metadata, statuses, and summary
            assert client.get("/api/papers").json() == papers_before
            arxiv_paper = client.get(f"/api/papers/{saved['id']}").json()
            assert arxiv_paper["title"] == "Sparse Attention"
            assert arxiv_paper["authors"] == ["Ada Lovelace", "Alan Turing"]
            assert arxiv_paper["summary"] == "## TL;DR\nSparse attention works."
            assert arxiv_paper["text_status"] == "ready"
            upload_paper = client.get(f"/api/papers/{uploaded['id']}").json()
            assert upload_paper["source"] == "upload"
            assert upload_paper["title"] == "Uploaded Paper"
            assert upload_paper["year"] == 2019

            # Search results still know which papers are saved
            monkeypatch.setattr(main, "search_arxiv", lambda q, limit: [main.SearchResult(**SAVE_BODY)])
            assert client.get("/api/search", params={"q": "sparse"}).json()[0]["saved_id"] == saved["id"]

            # Both PDFs are still served
            assert client.get(f"/api/papers/{saved['id']}/pdf").content == saved_pdf_before
            assert client.get(f"/api/papers/{uploaded['id']}/pdf").content == pdf_bytes

            # The conversation is still there, and a follow-up question builds on it
            assert client.get(f"/api/papers/{uploaded['id']}/chat").json() == chat_before
            seen = {}

            def answer(paper, history, question):
                seen["history"] = [(m.role, m.content) for m in history]
                seen["has_text"] = bool(paper.full_text)
                return "Also CIFAR-10."

            monkeypatch.setattr(main, "answer_question", answer)
            assert client.post(f"/api/papers/{uploaded['id']}/chat", json={"question": "Any others?"}).status_code == 200
            assert seen == {"history": [("user", "What datasets?"), ("assistant", "It uses ImageNet.")], "has_text": True}

            # The cached summary is returned without calling the LLM again
            monkeypatch.setattr(main, "summarize", lambda paper: (_ for _ in ()).throw(AssertionError("LLM called")))
            assert client.post(f"/api/papers/{saved['id']}/summary").json()["summary"] == "## TL;DR\nSparse attention works."
    finally:
        fresh_engine.dispose()
