import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlmodel import Session, col, delete, select

from .db import PDF_DIR, engine, get_session, init_db
from .llm import LLMError, answer_question, extract_metadata, summarize
from .models import ChatMessage, Paper, PaperCreate, PaperRead, QuestionIn, SearchResult
from .pdf import MAX_PDF_BYTES, clean_line_breaks, download_pdf, extract_text, guess_metadata
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


@app.post("/api/papers/upload", response_model=PaperRead, status_code=201)
def upload_paper(file: UploadFile = File(...), session: Session = Depends(get_session)):
    filename = file.filename or "upload.pdf"
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF is larger than 50 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "The uploaded file is not a PDF")

    dest = PDF_DIR / f"upload-{uuid.uuid4().hex}.pdf"
    dest.write_bytes(data)
    try:
        text = extract_text(dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read this PDF. It may be corrupted or encrypted.") from e

    # Prefer LLM-extracted metadata; fall back to heuristics so uploads still work without the LLM
    metadata = guess_metadata(dest, text, filename)
    if text:
        try:
            metadata = extract_metadata(text)
        except LLMError:
            log.exception("LLM metadata extraction failed for %s; using heuristics", filename)
    metadata["title"] = clean_line_breaks(metadata["title"])
    if metadata["abstract"]:
        metadata["abstract"] = clean_line_breaks(metadata["abstract"])

    paper = Paper(
        source="upload",
        **metadata,
        pdf_path=str(dest),
        full_text=text or None,
        # Scanned PDFs have no text layer, so there's nothing to summarize
        text_status="ready" if text else "failed",
    )
    session.add(paper)
    session.commit()
    session.refresh(paper)
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


def require_full_text(paper: Paper) -> None:
    if paper.text_status == "pending":
        raise HTTPException(409, "The paper's full text is still being processed. Try again shortly.")
    if paper.text_status != "ready" or not paper.full_text:
        raise HTTPException(409, "The paper's full text is not available, so it can't be analyzed.")


@app.post("/api/papers/{paper_id}/summary", response_model=PaperRead)
def generate_summary(
    paper_id: int,
    regenerate: bool = False,
    session: Session = Depends(get_session),
):
    paper = get_paper_or_404(session, paper_id)
    if paper.summary and not regenerate:
        return PaperRead.from_paper(paper)
    require_full_text(paper)
    try:
        paper.summary = summarize(paper)
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    session.add(paper)
    session.commit()
    session.refresh(paper)
    return PaperRead.from_paper(paper)


def get_chat_history(session: Session, paper_id: int) -> list[ChatMessage]:
    return list(
        session.exec(
            select(ChatMessage)
            .where(ChatMessage.paper_id == paper_id)
            .order_by(col(ChatMessage.created_at), col(ChatMessage.id))
        ).all()
    )


@app.get("/api/papers/{paper_id}/chat", response_model=list[ChatMessage])
def list_chat(paper_id: int, session: Session = Depends(get_session)):
    get_paper_or_404(session, paper_id)
    return get_chat_history(session, paper_id)


@app.post("/api/papers/{paper_id}/chat", response_model=list[ChatMessage])
def ask_question(paper_id: int, data: QuestionIn, session: Session = Depends(get_session)):
    """Ask a question about a paper; returns the new user and assistant messages."""
    paper = get_paper_or_404(session, paper_id)
    require_full_text(paper)
    history = get_chat_history(session, paper_id)
    try:
        reply = answer_question(paper, history, data.question)
    except LLMError as e:
        raise HTTPException(502, str(e)) from e

    # Only store the exchange once the LLM has answered, so failed calls leave no orphan question
    user_msg = ChatMessage(paper_id=paper_id, role="user", content=data.question)
    assistant_msg = ChatMessage(paper_id=paper_id, role="assistant", content=reply)
    session.add(user_msg)
    session.add(assistant_msg)
    session.commit()
    session.refresh(user_msg)
    session.refresh(assistant_msg)
    return [user_msg, assistant_msg]


@app.delete("/api/papers/{paper_id}/chat", status_code=204)
def clear_chat(paper_id: int, session: Session = Depends(get_session)):
    get_paper_or_404(session, paper_id)
    session.exec(delete(ChatMessage).where(col(ChatMessage.paper_id) == paper_id))
    session.commit()
