from datetime import datetime, timezone

from pydantic import BaseModel
from sqlmodel import JSON, Column, Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Paper(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str  # "arxiv" or "upload"
    external_id: str | None = Field(default=None, index=True)  # arXiv id for search results
    title: str
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    year: int | None = None
    abstract: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None  # remote PDF, if any
    pdf_path: str | None = None  # local copy under data/pdfs
    full_text: str | None = None
    summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    role: str  # "user" or "assistant"
    content: str
    created_at: datetime = Field(default_factory=utcnow)


class SearchResult(BaseModel):
    """A paper returned by the external search API (not yet saved)."""

    external_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    url: str
    pdf_url: str | None
    saved_id: int | None = None  # set if this paper is already in the library
