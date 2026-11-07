"""PDF download (httpx.stream mocked), text extraction, line-break cleanup, and metadata heuristics."""

import contextlib

import httpx
import pytest

from app import pdf
from app.pdf import clean_line_breaks, download_pdf, extract_text, guess_metadata

from .pdfs import SAMPLE_PAPER_PAGES, write_pdf


class FakeStreamResponse:
    """Serves the body in the given chunks so the size limit is checked chunk by chunk."""

    def __init__(self, method, url, chunks, status):
        self._real = httpx.Response(status, request=httpx.Request(method, url))
        self._chunks = chunks

    def raise_for_status(self):
        self._real.raise_for_status()

    def iter_bytes(self):
        yield from self._chunks


def mock_stream(monkeypatch, chunks=(), status=200):
    calls = []

    @contextlib.contextmanager
    def fake_stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        yield FakeStreamResponse(method, url, list(chunks), status)

    monkeypatch.setattr(pdf.httpx, "stream", fake_stream)
    return calls


# --- download_pdf ---------------------------------------------------------------------------


def test_download_writes_streamed_bytes(monkeypatch, tmp_path):
    calls = mock_stream(monkeypatch, [b"%PDF-1.4 ", b"rest"])
    dest = tmp_path / "paper.pdf"

    download_pdf("https://arxiv.org/pdf/1234", dest)

    assert dest.read_bytes() == b"%PDF-1.4 rest"
    method, url, kwargs = calls[0]
    assert (method, url) == ("GET", "https://arxiv.org/pdf/1234")
    assert kwargs["follow_redirects"] is True
    assert kwargs["headers"]["User-Agent"]


def test_download_rejects_files_over_the_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 10)
    mock_stream(monkeypatch, [b"123456", b"789012"])

    with pytest.raises(ValueError, match="larger than 50 MB"):
        download_pdf("https://example.org/big.pdf", tmp_path / "big.pdf")


def test_download_accepts_file_exactly_at_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 10)
    mock_stream(monkeypatch, [b"12345", b"67890"])
    dest = tmp_path / "ok.pdf"

    download_pdf("https://example.org/ok.pdf", dest)

    assert dest.read_bytes() == b"1234567890"


def test_download_raises_on_http_error(monkeypatch, tmp_path):
    mock_stream(monkeypatch, [b"not found"], status=404)
    dest = tmp_path / "missing.pdf"

    with pytest.raises(httpx.HTTPStatusError):
        download_pdf("https://example.org/missing.pdf", dest)
    assert not dest.exists()


# --- extract_text ---------------------------------------------------------------------------


def test_extract_text_joins_pages(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [["Page one"], ["Page two"]])
    assert extract_text(path) == "Page one\n\nPage two"


def test_extract_text_of_pdf_without_text_layer_is_empty(tmp_path):
    path = write_pdf(tmp_path / "scan.pdf", [[], []])
    assert extract_text(path) == ""


def test_extract_text_removes_nul_characters(tmp_path, monkeypatch):
    path = write_pdf(tmp_path / "p.pdf", [["x"]])
    monkeypatch.setattr(pdf.PdfReader, "pages", property(lambda self: [FakePage("a\x00b"), FakePage(None)]))
    assert extract_text(path) == "ab"


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


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


def test_guess_metadata_truncates_long_first_line(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])
    meta = guess_metadata(path, "\n\n" + "x" * 500 + "\nmore", "file.pdf")
    assert meta["title"] == "x" * 200


def test_guess_metadata_falls_back_to_filename_without_text(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])

    meta = guess_metadata(path, "", "my-great-paper.pdf")

    assert meta == {"title": "my-great-paper", "authors": [], "year": None, "abstract": None}


def test_guess_metadata_abstract_runs_to_end_without_introduction(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])
    meta = guess_metadata(path, "Title\nABSTRACT: Short summary\nof the work.", "f.pdf")
    assert meta["abstract"] == "Short summary of the work."


def test_guess_metadata_limits_abstract_length(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [[]])
    meta = guess_metadata(path, "Title\nAbstract\n" + "word " * 2000, "f.pdf")
    assert len(meta["abstract"]) == 3000


def test_guess_metadata_ignores_malformed_creation_date(tmp_path):
    path = write_pdf(tmp_path / "p.pdf", [["Title"]], {"CreationDate": "yesterday"})
    assert guess_metadata(path, "Title", "f.pdf")["year"] is None
