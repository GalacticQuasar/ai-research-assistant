"""PDF download and text extraction."""

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
