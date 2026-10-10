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
| Frontend | React 19, TypeScript, Vite, React Router, react-markdown |
| Backend | Python 3.13, FastAPI, SQLModel |
| Database | SQLite (`backend/data/library.db`) |
| Paper search | [arXiv API](https://info.arxiv.org/help/api/) (no API key needed) |
| PDF processing | pypdf |
| LLM | [OpenRouter](https://openrouter.ai) via the `openai` SDK (default model: `qwen/qwen3-235b-a22b-2507`) |

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
│   │   └── llm.py        # OpenRouter client: summaries, Q&A, metadata extraction
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
- **Summaries and Q&A** put the paper's full text (up to about 50k tokens) directly in the LLM prompt. A paper fits comfortably in the model's context window, so no vector database or retrieval step is needed.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python 3.13 automatically if needed
- [Node.js](https://nodejs.org) 20.19+ or 22.12+, with npm
- An [OpenRouter API key](https://openrouter.ai/keys). A few dollars of credit is plenty: a summary costs well under one cent with the default model.

### 1. Configure the API key

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env` and set your key:

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=qwen/qwen3-235b-a22b-2507
```

`OPENROUTER_MODEL` can be any [OpenRouter model ID](https://openrouter.ai/models). Models with a context window of at least 128k tokens work best for long papers. `.env` is gitignored, so never commit it.

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

## API reference

| Method | Endpoint | Description |
| --- | --- | --- |
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
- **Very long papers.** Text beyond about 50k tokens is truncated before it's sent to the LLM.
- **Single user, local only.** There is no authentication, and the app is meant to run on your own machine.
