"""Shared fixtures. Every test runs against a temporary database and data directory, with no network.

`app.llm` calls `load_dotenv()` at import time, which would load the developer's real
`backend/.env` (API keys, provider choice). Disable it before anything imports the app.
"""

import socket

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.db as db  # noqa: E402
import app.main as main  # noqa: E402
from app.models import Paper  # noqa: E402

from .pdfs import SAMPLE_PAPER_PAGES, write_pdf  # noqa: E402

LLM_ENV_VARS = (
    "LLM_PROVIDER",
    "GENAI_API_KEY",
    "GENAI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if anything tries to open a real network connection."""

    def guard(*args, **kwargs):
        raise RuntimeError("Tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    """Start every test with no LLM configuration in the environment."""
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the app's data directory, PDF directory, and database at a temp folder."""
    data = tmp_path / "data"
    pdf_dir = data / "pdfs"
    db_path = data / "library.db"
    data.mkdir()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    for module in (db, main):
        monkeypatch.setattr(module, "PDF_DIR", pdf_dir)
        monkeypatch.setattr(module, "engine", engine)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    yield data
    engine.dispose()


@pytest.fixture
def engine(data_dir):
    return main.engine


@pytest.fixture
def pdf_dir(data_dir):
    return data_dir / "pdfs"


@pytest.fixture
def client(engine):
    def session_override():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[db.get_session] = session_override
    # The context manager runs the lifespan, i.e. init_db() against the temp directory
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def session(engine, client):
    # Depends on `client` so init_db() has created the tables
    with Session(engine) as s:
        yield s


@pytest.fixture
def add_paper(session):
    """Insert a paper directly into the test database."""

    def _add(**fields) -> Paper:
        defaults = dict(
            source="arxiv",
            external_id="2401.00001v1",
            title="A Test Paper",
            authors=["Ada Lovelace"],
            year=2024,
            abstract="An abstract.",
            url="http://arxiv.org/abs/2401.00001v1",
            pdf_url="http://arxiv.org/pdf/2401.00001v1",
            full_text="Full text of the test paper.",
            text_status="ready",
        )
        paper = Paper(**{**defaults, **fields})
        session.add(paper)
        session.commit()
        session.refresh(paper)
        return paper

    return _add


@pytest.fixture
def fake_download(monkeypatch):
    """Replace the arXiv PDF download with one that writes a local sample PDF.

    Returns a dict to control behavior: set "pages" to change the PDF contents or
    "error" to an exception to raise. "calls" records the requested (url, dest) pairs.
    """
    state = {"pages": SAMPLE_PAPER_PAGES, "error": None, "calls": []}

    def _download(url, dest):
        state["calls"].append((url, dest))
        write_pdf(dest, state["pages"])
        if state["error"] is not None:
            raise state["error"]

    monkeypatch.setattr(main, "download_pdf", _download)
    return state
