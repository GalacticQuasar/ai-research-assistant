import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlmodel import Session, col, delete, select

from .db import PDF_DIR, engine, get_session, init_db
from .models import ChatMessage, Paper, PaperCreate, PaperRead, SearchResult
from .pdf import download_pdf, extract_text
from .search import RateLimitedError, search_arxiv

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Research Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/search", response_model=list[SearchResult])
def search(
    q: str = Query(min_length=1),
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
):
    try:
        results = search_arxiv(q, limit)
    except RateLimitedError as e:
        raise HTTPException(503, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"arXiv search failed: {e}") from e

    # Mark results that are already in the library so the UI can show "Saved"
    ids = [r.external_id for r in results]
    saved = session.exec(
        select(Paper.external_id, Paper.id).where(col(Paper.external_id).in_(ids))
    ).all()
    saved_map = dict(saved)
    for r in results:
        r.saved_id = saved_map.get(r.external_id)
    return results


def fetch_full_text(paper_id: int) -> None:
    """Background task: download a saved paper's PDF and extract its text."""
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if paper is None or paper.pdf_url is None:
            return
        dest = PDF_DIR / f"{paper_id}.pdf"
        try:
            download_pdf(paper.pdf_url, dest)
            paper.full_text = extract_text(dest)
            paper.pdf_path = str(dest)
            paper.text_status = "ready" if paper.full_text else "failed"
        except Exception:
            log.exception("Failed to fetch full text for paper %s", paper_id)
            dest.unlink(missing_ok=True)
            paper.text_status = "failed"
        session.add(paper)
        session.commit()


def get_paper_or_404(session: Session, paper_id: int) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "Paper not found")
    return paper


@app.post("/api/papers", response_model=PaperRead, status_code=201)
def save_paper(
    data: PaperCreate,
    background: BackgroundTasks,
    response: Response,
    session: Session = Depends(get_session),
):
    existing = session.exec(select(Paper).where(Paper.external_id == data.external_id)).first()
    if existing:
        response.status_code = 200
        return PaperRead.from_paper(existing)

    paper = Paper(
        source="arxiv",
        **data.model_dump(),
        text_status="pending" if data.pdf_url else "failed",
    )
    session.add(paper)
    session.commit()
    session.refresh(paper)
    if data.pdf_url:
        background.add_task(fetch_full_text, paper.id)
    return PaperRead.from_paper(paper)


@app.get("/api/papers", response_model=list[PaperRead])
def list_papers(session: Session = Depends(get_session)):
    papers = session.exec(select(Paper).order_by(col(Paper.created_at).desc())).all()
    return [PaperRead.from_paper(p) for p in papers]


@app.get("/api/papers/{paper_id}", response_model=PaperRead)
def get_paper(paper_id: int, session: Session = Depends(get_session)):
    return PaperRead.from_paper(get_paper_or_404(session, paper_id))


@app.post("/api/papers/{paper_id}/fetch-text", response_model=PaperRead)
def retry_fetch_text(
    paper_id: int,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Retry the PDF download/extraction for a paper whose text failed to load."""
    paper = get_paper_or_404(session, paper_id)
    if paper.pdf_url is None:
        raise HTTPException(400, "This paper has no PDF link")
    paper.text_status = "pending"
    session.add(paper)
    session.commit()
    session.refresh(paper)
    background.add_task(fetch_full_text, paper.id)
    return PaperRead.from_paper(paper)


@app.get("/api/papers/{paper_id}/pdf")
def get_paper_pdf(paper_id: int, session: Session = Depends(get_session)):
    paper = get_paper_or_404(session, paper_id)
    if paper.pdf_path is None:
        raise HTTPException(404, "No local PDF for this paper")
    return FileResponse(paper.pdf_path, media_type="application/pdf")


@app.delete("/api/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: int, session: Session = Depends(get_session)):
    paper = get_paper_or_404(session, paper_id)
    session.exec(delete(ChatMessage).where(col(ChatMessage.paper_id) == paper_id))
    if paper.pdf_path:
        Path(paper.pdf_path).unlink(missing_ok=True)
    session.delete(paper)
    session.commit()
