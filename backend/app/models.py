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
    text_status: str = "pending"  # "pending", "ready", or "failed"
    summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    role: str  # "user" or "assistant"
    content: str
    created_at: datetime = Field(default_factory=utcnow)


class PaperCreate(BaseModel):
    """Metadata for a search result the user wants to save."""

    external_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    url: str
    pdf_url: str | None


class SearchResult(PaperCreate):
    """A paper returned by the external search API (not yet saved)."""

    saved_id: int | None = None  # set if this paper is already in the library


class PaperRead(BaseModel):
    """A saved paper as returned by the API (full text omitted, it can be huge)."""

    id: int
    source: str
    external_id: str | None
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    url: str | None
    pdf_url: str | None
    has_pdf: bool
    text_status: str
    summary: str | None
    created_at: datetime

    @classmethod
    def from_paper(cls, paper: Paper) -> "PaperRead":
        return cls(
            **paper.model_dump(exclude={"full_text", "pdf_path"}),
            has_pdf=paper.pdf_path is not None,
        )
