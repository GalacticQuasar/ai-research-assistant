from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, col, select

from .db import get_session, init_db
from .models import Paper, SearchResult
from .search import RateLimitedError, search_arxiv


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
