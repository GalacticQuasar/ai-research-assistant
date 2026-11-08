# AI Research Assistant

Search arXiv, save papers to a local library, upload PDFs, and summarize or ask questions about a paper's full text with an LLM. See `README.md` for features, setup, and the API reference.

## Commands

```bash
# Backend (from backend/), on :8000
uv run uvicorn app.main:app --reload

# Frontend (from frontend/), on :5173; proxies /api to :8000
npm run dev
npm run build   # type-check (tsc -b) and build
npm run lint    # oxlint
```

```bash
# Backend (from backend/)
uv run pytest                                            # tests
uv run pytest --cov --cov-report=term --cov-report=html  # coverage; HTML in htmlcov/
rm -rf mutants && uv run mutmut run                      # mutation testing; then `uv run mutmut results`
uv run python -m tests.export_openapi                    # regenerate contract/openapi.json after API changes

# Frontend (from frontend/)
npm test           # tests
npm run coverage   # coverage
```

Seven tests fail on purpose because they detect known app bugs. See "Testing" in `README.md` for the list, what each test file covers, and expected results.

## Layout

- `backend/app/main.py`: all routes, plus the background full-text task
- `backend/app/models.py`: SQLModel tables and API schemas
- `backend/app/search.py`: arXiv client and stop word filtering
- `backend/app/pdf.py`: PDF download, text extraction, metadata heuristics
- `backend/app/llm.py`: GenAI Studio/OpenRouter client (summaries, Q&A, metadata extraction)
- `frontend/src/api.ts`: typed API client; `pages/` and `components/` hold the UI

## Things to know

- **Schema changes:** there are no migrations and `create_all` won't alter existing tables. After changing a table, delete `backend/data/library.db`, which also deletes the local library, so ask first if it has data.
- **Full text:** `Paper.text_status` goes `pending` → `ready` or `failed`. Summary and chat routes return 409 unless it's `ready`. Saves load text in a background task; uploads do everything in one request.
- **arXiv:** the search API rate-limits with 429s, sometimes temporarily for a whole network. Space out live requests. Stop words must stay out of queries, because arXiv doesn't index them and requiring one returns almost nothing.
- **LLM:** `LLM_PROVIDER` in `backend/.env` selects `genai` (default) or `openrouter`, and `GENAI_MODEL` or `OPENROUTER_MODEL` overrides that provider's default in `llm.py`. Keep real calls few: OpenRouter calls spend the user's credits, and GenAI Studio is a shared service limited to 20 requests per minute, which may answer with a JSON `null` instead of an HTTP error when exceeded. Error messages must not pass through a provider's raw body, because OpenRouter's contains the account ID.
- **Secrets:** never print or commit `backend/.env`.
- **Stopping dev servers:** `pkill -f` can match the shell running it. Find the real PIDs with `ps` and kill those.

## Workflow

Work in stages. After each one, stop and summarize what was done and what to test. The user reviews, commits, and pushes.
