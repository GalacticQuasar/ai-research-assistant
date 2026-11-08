"""PDF download (HTTP faked), text extraction, line-break cleanup, and metadata heuristics.

Supports requirement 3 (uploaded PDFs get title/authors/year/abstract) and the full text that
requirements 4 and 5 depend on.
"""

import httpx
import pytest

from app import pdf, search
from app.pdf import clean_line_breaks, download_pdf, extract_text, guess_metadata

from .fakes import FakeHTTP, redirect
from .pdfs import SAMPLE_PAPER_PAGES, make_pdf, write_pdf

# --- download_pdf ---------------------------------------------------------------------------


def test_download_saves_the_pdf(monkeypatch, tmp_path):
    data = make_pdf(SAMPLE_PAPER_PAGES)
    fake = FakeHTTP(httpx.Response(200, content=data)).install(monkeypatch)
    dest = tmp_path / "paper.pdf"

    download_pdf("https://arxiv.org/pdf/2401.01234v2", dest)

    assert dest.read_bytes() == data
    request = fake.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://arxiv.org/pdf/2401.01234v2"
    # arXiv asks clients to identify themselves; a stalled download must not hang forever
    assert request.headers["User-Agent"] == search.USER_AGENT
    assert all(value is not None for value in request.extensions["timeout"].values())


def test_download_follows_redirects(monkeypatch, tmp_path):
    # arXiv answers http:// and versionless PDF links with a redirect
    fake = FakeHTTP(redirect("https://arxiv.org/pdf/2401.01234v2"), httpx.Response(200, content=b"%PDF-1.4 x"))
    fake.install(monkeypatch)
    dest = tmp_path / "paper.pdf"

    download_pdf("http://arxiv.org/pdf/2401.01234", dest)

    assert dest.read_bytes() == b"%PDF-1.4 x"
    assert len(fake.requests) == 2


def test_download_rejects_files_over_the_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 10)
    FakeHTTP(httpx.Response(200, content=iter([b"123456", b"789012"]))).install(monkeypatch)

    with pytest.raises(ValueError, match="larger than 50 MB"):
        download_pdf("https://example.org/big.pdf", tmp_path / "big.pdf")


def test_download_accepts_file_exactly_at_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 10)
    FakeHTTP(httpx.Response(200, content=iter([b"12345", b"67890"]))).install(monkeypatch)
    dest = tmp_path / "ok.pdf"

    download_pdf("https://example.org/ok.pdf", dest)

    assert dest.read_bytes() == b"1234567890"


def test_download_raises_on_http_error_without_creating_a_file(monkeypatch, tmp_path):
    FakeHTTP((404, "not found")).install(monkeypatch)
    dest = tmp_path / "missing.pdf"

    with pytest.raises(httpx.HTTPStatusError):
        download_pdf("https://example.org/missing.pdf", dest)
    assert not dest.exists()


# --- extract_text ---------------------------------------------------------------------------


def test_extract_text_includes_every_page(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [["Page one"], ["Page two"]])
    text = extract_text(path)
    assert "Page one" in text and "Page two" in text
    assert text.index("Page one") < text.index("Page two")


def test_extract_text_of_pdf_without_text_layer_is_empty(tmp_path):
    path = write_pdf(tmp_path / "scan.pdf", [[], []])
    assert extract_text(path) == ""


def test_extract_text_removes_nul_characters(tmp_path):
    # SQLite and JSON can't store NUL, and some PDFs produce it
    path = write_pdf(tmp_path / "p.pdf", [["Deep\x00Learning"]])
    assert extract_text(path) == "DeepLearning"


def test_extract_text_raises_for_corrupt_pdf(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(Exception):
        extract_text(path)


# --- clean_line_breaks ------------------------------------------------------------------------


def test_clean_line_breaks_rejoins_hyphenated_words():
    assert clean_line_breaks("trans-\nformer and atten-  \n  tion") == "transformer and attention"


def test_clean_line_breaks_collapses_whitespace():
    assert clean_line_breaks("  one\ntwo\n\n three\t ") == "one two three"


def test_clean_line_breaks_keeps_hyphens_within_a_line():
    assert clean_line_breaks("self-supervised learning") == "self-supervised learning"


# --- guess_metadata ---------------------------------------------------------------------------


def test_guess_metadata_prefers_embedded_info(tmp_path):
    info = {"Title": "  Embedded Title  ", "Author": "Ada Lovelace; Alan Turing and Grace Hopper, Edsger Dijkstra",
            "CreationDate": "D:20210305120000Z"}
    path = write_pdf(tmp_path / "p.pdf", SAMPLE_PAPER_PAGES, info)

    meta = guess_metadata(path, extract_text(path), "file.pdf")

    assert meta["title"] == "Embedded Title"
    assert meta["authors"] == ["Ada Lovelace", "Alan Turing", "Grace Hopper", "Edsger Dijkstra"]
    assert meta["year"] == 2021


def test_guess_metadata_extracts_abstract_up_to_introduction(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", SAMPLE_PAPER_PAGES)

    meta = guess_metadata(path, extract_text(path), "file.pdf")

    assert meta["abstract"] == "We study sparse attention for long documents and show it matches dense attention."


def test_guess_metadata_falls_back_to_first_text_line(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", SAMPLE_PAPER_PAGES)

    meta = guess_metadata(path, extract_text(path), "file.pdf")

    assert meta["title"] == "Attention Is Mostly What You Need"
    assert meta["authors"] == []
    assert meta["year"] is None


def test_guess_metadata_falls_back_to_filename_without_text(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])

    meta = guess_metadata(path, "", "my-great-paper.pdf")

    assert meta == {"title": "my-great-paper", "authors": [], "year": None, "abstract": None}


def test_guess_metadata_abstract_runs_to_end_without_introduction(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])
    meta = guess_metadata(path, "Title\nABSTRACT: Short summary\nof the work.", "f.pdf")
    assert meta["abstract"] == "Short summary of the work."


def test_guess_metadata_ignores_malformed_creation_date(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [["Title"]], {"CreationDate": "yesterday"})
    assert guess_metadata(path, "Title", "f.pdf")["year"] is None
