"""PDF download, text extraction, and metadata heuristics."""

import re
from pathlib import Path

import httpx
from pypdf import PdfReader

from .search import USER_AGENT

MAX_PDF_BYTES = 50 * 1024 * 1024


def download_pdf(url: str, dest: Path) -> None:
    with httpx.stream(
        "GET",
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=60,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        size = 0
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("PDF is larger than 50 MB")
                f.write(chunk)


def extract_text(path: Path) -> str:
    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    # NUL characters occasionally appear in extracted text and SQLite/JSON dislike them
    return "\n\n".join(pages).replace("\x00", "").strip()


def clean_line_breaks(text: str) -> str:
    """Undo PDF line wrapping: rejoin words hyphenated across lines and collapse whitespace."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def guess_metadata(path: Path, text: str, filename: str) -> dict:
    """Best-effort metadata without an LLM: embedded PDF info plus the abstract section."""
    info = PdfReader(path).metadata or {}
    title = (info.get("/Title") or "").strip()
    if not title:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        title = first_line[:200] or Path(filename).stem
    author_field = (info.get("/Author") or "").strip()
    authors = [a.strip() for a in re.split(r",|;| and ", author_field) if a.strip()]
    year_match = re.search(r"D:(\d{4})", info.get("/CreationDate") or "")
    # Abstract: text after an "Abstract" heading, up to the introduction
    abstract_match = re.search(
        r"\babstract\b[\s.:\-—]*(.+?)(?=\n\s*(?:\d+\.?|I\.?)?\s*introduction\b|\Z)",
        text[:15000],
        re.IGNORECASE | re.DOTALL,
    )
    abstract = clean_line_breaks(abstract_match.group(1))[:3000] if abstract_match else None
    return {
        "title": title,
        "authors": authors,
        "year": int(year_match.group(1)) if year_match else None,
        "abstract": abstract,
    }
