# AI Research Assistant

A web app for finding, organizing, and understanding research papers. Search arXiv, save papers to a local library, upload your own PDFs, and use an LLM to summarize papers and answer questions about them, all grounded in each paper's full text.

## Features

1. **Search for papers.** Enter keywords or a topic to search [arXiv](https://arxiv.org). Results show the title, authors, year, abstract, and a link to the paper.
2. **Save papers to a local library.** Saved papers are stored in a SQLite database and persist across restarts and page refreshes. When you save a paper, its PDF is downloaded and its full text extracted in the background.
3. **Upload PDF papers.** Upload any research paper PDF. The app extracts the full text, and an LLM extracts the title, authors, year, and abstract, so uploads display just like search results. If the LLM is unavailable, simple heuristics fill in the metadata instead.
4. **Summarize papers with an LLM.** Generate a structured summary (TL;DR, problem, approach, key results, limitations) based on the paper's **full text**, not just its abstract. Summaries are cached.
5. **Ask questions about a paper.** Chat with an LLM about a selected paper. Example questions are one click away, follow-up questions use the conversation history, and the chat history is saved per paper. The model answers from the paper's content and says so when the paper doesn't cover something.

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, React Router, react-markdown; Hanken Grotesk and Newsreader fonts from Google Fonts (system fonts are used offline) |
| Backend | Python 3.13, FastAPI, SQLModel |
| Database | SQLite (`backend/data/library.db`) |
| Paper search | [arXiv API](https://info.arxiv.org/help/api/) (no API key needed) |
| PDF processing | pypdf |
| LLM | [Purdue GenAI Studio](https://docs.rcac.purdue.edu/services/genai/) (default model: `gpt-oss:120b`) or [OpenRouter](https://openrouter.ai) (default model: `qwen/qwen3-235b-a22b-2507`), both via the `openai` SDK |

## Project structure

```
ai-research-assistant/
├── backend/
│   ├── app/
│   │   ├── main.py       # FastAPI app and all API routes
│   │   ├── models.py     # Database tables (Paper, ChatMessage) and API schemas
│   │   ├── db.py         # SQLite engine and data directory setup
│   │   ├── search.py     # arXiv search client
│   │   ├── pdf.py        # PDF download, text extraction, metadata heuristics
│   │   └── llm.py        # GenAI Studio/OpenRouter client: summaries, Q&A, metadata extraction
│   ├── data/             # Created at runtime (gitignored): library.db and PDFs
│   ├── .env.example      # Template for the API key and model configuration
│   └── pyproject.toml    # Python dependencies (managed with uv)
└── frontend/
    ├── src/
    │   ├── api.ts        # Typed client for the backend API
    │   ├── App.tsx       # Navigation and routes
    │   ├── pages/        # SearchPage, LibraryPage, PaperDetailPage
    │   └── components/   # PaperCard, PaperBadges, SummaryPanel, ChatPanel
    ├── vite.config.ts    # Dev server; proxies /api to the backend
    └── package.json      # Node dependencies
```

### How it works

- The **frontend** is a single-page React app. During development, Vite forwards every `/api/*` request to the FastAPI backend on port 8000.
- **Saving a search result** stores its metadata immediately. A background task then downloads the arXiv PDF and extracts the text, and the paper's `text_status` moves from `pending` to `ready` (or `failed`). The UI polls until the text is ready.
- **Uploading a PDF** validates the file, extracts its text, and asks the LLM for the metadata as JSON, all in one request (about 10 seconds).
- **Summaries and Q&A** put the paper's full text directly in the LLM prompt, up to about 43k tokens on GenAI Studio and 50k on OpenRouter. A paper fits in the model's context window, so no vector database or retrieval step is needed.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python 3.13 automatically if needed
- [Node.js](https://nodejs.org) 20.19+ or 22.12+, with npm
- An API key for one of the LLM services:
  - **Purdue GenAI Studio** (default): requires a Purdue account. [Sign in](https://genai.rcac.purdue.edu/), then create a key under your avatar > **Settings > Account > API Keys**.
  - **OpenRouter**: create an [API key](https://openrouter.ai/keys). A few dollars of credit is plenty: a summary costs well under one cent with the default model.

### 1. Configure the API key

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env`, choose a provider, and set its key. Only the selected provider's key is required:

```
LLM_PROVIDER=genai
GENAI_API_KEY=...
GENAI_MODEL=gpt-oss:120b

# Or, to use OpenRouter:
# LLM_PROVIDER=openrouter
# OPENROUTER_API_KEY=sk-or-...
# OPENROUTER_MODEL=qwen/qwen3-235b-a22b-2507
```

Restart the backend after changing `.env`. `LLM_PROVIDER` defaults to `genai`.

- `GENAI_MODEL` can be any model ID returned by `GET https://genai.rcac.purdue.edu/api/models`. See the [hosted models](https://docs.rcac.purdue.edu/services/genai/models/) page for context limits. Avoid models with a context below 65,536 tokens, such as `llama4:latest`, for long papers.
- `OPENROUTER_MODEL` can be any [OpenRouter model ID](https://openrouter.ai/models). Models with a context window of at least 128k tokens work best for long papers.

`.env` is gitignored, so never commit it.

### 2. Install dependencies

```bash
# Backend (from backend/)
uv sync

# Frontend (from frontend/)
npm install
```

## Running the app

Start the backend and frontend in two terminals:

```bash
# Terminal 1: backend API on http://localhost:8000
cd backend
uv run uvicorn app.main:app --reload
```

```bash
# Terminal 2: frontend on http://localhost:5173
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

The database and PDFs are created automatically in `backend/data/` on first run. To reset the library, stop the backend and delete that folder.

Interactive API documentation is available at http://localhost:8000/docs.

## Testing

The backend is tested with pytest (plus pytest-cov and mutmut), the frontend with Vitest and React Testing Library. A shared API contract ties the two together.

### Where the tests live

**`backend/tests/`**

| File | Covers |
| --- | --- |
| `test_api.py` | Every HTTP route: search, saving with the background PDF download, upload, library listing, PDF download, delete, summary, chat. Each test checks the response and the resulting database rows, chat history, and PDF files. |
| `test_llm.py` | Summaries, Q&A, and upload metadata extraction: provider and model choice, prompts built from the full text, truncation, retries, user-facing error messages. The fake LLM rejects requests the OpenAI chat API would reject. |
| `test_search.py` | arXiv search: the query and parameters actually sent, stop-word handling, result parsing, rate-limit retry, errors. |
| `test_pdf.py` | PDF download, text extraction, line-break cleanup, metadata heuristics for uploads. |
| `test_persistence.py` | Saved papers, uploads, summaries, and chats survive an app restart (fresh engine and app on the same database file). |
| `test_contract.py` | `contract/openapi.json` matches the API the backend actually serves. |
| `conftest.py`, `fakes.py`, `pdfs.py` | Shared fixtures (temporary database and data folder, network guard), an in-process fake for arXiv and PDF hosts, and a builder for small test PDFs. |
| `export_openapi.py` | Script that regenerates `contract/openapi.json` (not a test). |

**`frontend/tests/`**

| File | Covers |
| --- | --- |
| `api.test.ts` | The API client: URLs, request bodies, error messages. |
| `contract.test.ts` | Every `api.ts` call and every response fixture against `contract/openapi.json`. |
| `SearchPage.test.tsx` | Searching, the query in the URL, loading and error states, saving results. |
| `LibraryPage.test.tsx` | Listing, polling while text loads, removing papers, uploading PDFs. |
| `PaperDetailPage.test.tsx` | Paper details, links, retrying a failed download, polling, generating a summary. |
| `SummaryPanel.test.tsx` | Generating and regenerating summaries. |
| `ChatPanel.test.tsx` | Asking questions, example questions, history, clearing the chat. |
| `PaperCard.test.tsx`, `PaperBadges.test.tsx`, `format.test.ts` | Paper cards, status badges, author formatting. |
| `App.test.tsx` | Routing, navigation, the entry point. |
| `setup.ts`, `helpers.tsx`, `openapi.ts` | Test setup, fetch mocking and fixtures, and the OpenAPI loader and validator. |

**`contract/openapi.json`**: the backend's OpenAPI schema, generated from the FastAPI app. The backend test checks that it is current; the frontend test checks the frontend against it.

### Install

```bash
# Backend (from backend/); uv sync includes the dev dependency group
uv sync

# Frontend (from frontend/)
npm install
```

The tests need no API key, no network access, and no running server. arXiv, PDF downloads, and the LLM are faked, any real network connection fails the test, and `backend/.env` is never loaded. The backend tests use a temporary database and data folder and never touch `backend/data/`.

### Run

```bash
# Backend (from backend/)
uv run pytest

# Frontend (from frontend/)
npm test
```

### Coverage

```bash
# Backend (from backend/): line and branch coverage of app/
uv run pytest --cov --cov-report=term --cov-report=html

# Frontend (from frontend/): coverage of src/
npm run coverage
```

The backend prints a per-module table and writes an HTML report to `backend/htmlcov/index.html`. The frontend prints a per-file table and writes `frontend/coverage/coverage-summary.json`. Both report folders are gitignored.

### Mutation testing

```bash
# Backend (from backend/)
rm -rf mutants
uv run mutmut run
uv run mutmut results
```

mutmut makes small changes ("mutants") to `app/` and runs the tests against each one. A mutant is **killed** when a test fails and **survived** when every test still passes. `uv run mutmut run` shows live counts (🎉 killed, ⏰ timed out, which also counts as killed, 🙁 survived), `uv run mutmut results` lists the surviving and timed-out mutants, and `uv run mutmut show <mutant name>` shows a mutant's diff. Results are cached in `backend/mutants/` (gitignored), so delete that folder first for a clean run.

mutmut needs a passing baseline, so `[tool.mutmut]` in `backend/pyproject.toml` deselects the 6 backend tests that fail on known bugs (see below). Expect 598 mutants: 531 killed (one of them by timeout) and 67 surviving. A run can occasionally differ by one mutant (for example 66 surviving), because one equivalent mutant in `guess_metadata` was once reported as killed during a full parallel run. The survivors were reviewed one by one. They are equivalent to the original code (for example a different letter case in an HTTP header name) or not worth testing (log messages, prompt wording, timeout tuning).

### Expected results

**Seven tests fail on purpose.** Each one detects a real bug in the app, left unfixed for this assignment. Expect `6 failed, 162 passed` from the backend and `1 failed, 112 passed` from the frontend, so both test commands exit with a non-zero status. Any other failure is a real problem.

| Failing test | Bug it detects |
| --- | --- |
| `backend/tests/test_llm.py::test_extract_metadata_non_object_json_raises_llm_error[null]` | `extract_metadata` crashes with `AttributeError` instead of raising `LLMError` when the LLM replies with JSON `null`. |
| `backend/tests/test_llm.py::test_extract_metadata_non_object_json_raises_llm_error[["Title", "Ada"]]` | The same crash when the LLM replies with a JSON array. |
| `backend/tests/test_llm.py::test_extract_metadata_non_object_json_raises_llm_error["just a string"]` | The same crash when the LLM replies with a JSON string. |
| `backend/tests/test_api.py::test_upload_falls_back_when_llm_returns_non_object_json` | Because of that crash, an upload returns HTTP 500 instead of falling back to the metadata heuristics. |
| `backend/tests/test_llm.py::test_extract_metadata_authors_string_is_not_split_into_characters` | An `authors` value returned as one string is stored as a list of single letters. |
| `backend/tests/test_api.py::test_get_pdf_when_file_was_removed_from_disk` | Requesting a paper's PDF after the file was deleted from disk returns HTTP 500 instead of 404. |
| `frontend/tests/PaperDetailPage.test.tsx` > "clears the retry error once a later retry succeeds" | On the paper page, the error from a failed "Try again" stays on screen after a later retry succeeds. |

### API contract

Regenerate `contract/openapi.json` whenever you change an API route, a request or response model, or a field name:

```bash
# Backend (from backend/)
uv run python -m tests.export_openapi
```

If you forget, `backend/tests/test_contract.py::test_contract_snapshot_matches_the_backend` fails. After regenerating, `frontend/tests/contract.test.ts` reports any frontend type, fixture, or request that no longer matches the backend.

## API reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check; returns `{"status": "ok"}` |
| `GET` | `/api/search?q=&limit=` | Search arXiv. Results already saved include `saved_id`. |
| `GET` | `/api/papers` | List saved papers, newest first |
| `POST` | `/api/papers` | Save a search result. Full text loads in the background. |
| `POST` | `/api/papers/upload` | Upload a PDF (multipart `file`) |
| `GET` | `/api/papers/{id}` | Get one paper |
| `DELETE` | `/api/papers/{id}` | Delete a paper, its PDF, and its chat history |
| `GET` | `/api/papers/{id}/pdf` | Download the locally stored PDF |
| `POST` | `/api/papers/{id}/fetch-text` | Retry downloading the full text after a failure |
| `POST` | `/api/papers/{id}/summary?regenerate=` | Generate (or return the cached) summary |
| `GET` | `/api/papers/{id}/chat` | Get the chat history |
| `POST` | `/api/papers/{id}/chat` | Ask a question (`{"question": "..."}`) |
| `DELETE` | `/api/papers/{id}/chat` | Clear the chat history |

## Known limitations

- **arXiv rate limits.** arXiv's API sometimes rejects requests with HTTP 429. The backend retries once, then shows "arXiv is rate-limiting requests. Please try again in a minute."
- **Search ranking.** arXiv's relevance ranking is basic, and common stop words are removed from queries because arXiv doesn't index them. Searching for a paper's distinctive title words works best.
- **Scanned PDFs.** PDFs without a text layer (scanned images) can be uploaded and viewed, but can't be summarized or questioned. There is no OCR.
- **Metadata fallback.** If the LLM call fails during upload, the heuristic metadata can be wrong.
- **Very long papers.** Text beyond about 43k tokens on GenAI Studio, or 50k on OpenRouter, is truncated before it's sent to the LLM.
- **GenAI Studio rate limit.** The service allows 20 requests per minute per user and may return `null` instead of an HTTP error when that's exceeded. The backend retries with backoff, then shows an error asking you to try again in a minute.
- **Single user, local only.** There is no authentication, and the app is meant to run on your own machine.
