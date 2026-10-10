// Typed wrappers around the FastAPI backend (proxied at /api by Vite in development)

export interface SearchResult {
  external_id: string
  title: string
  authors: string[]
  year: number | null
  abstract: string
  url: string
  pdf_url: string | null
  saved_id: number | null
}

export type TextStatus = 'pending' | 'ready' | 'failed'

export interface Paper {
  id: number
  source: 'arxiv' | 'upload'
  external_id: string | null
  title: string
  authors: string[]
  year: number | null
  abstract: string | null
  url: string | null
  pdf_url: string | null
  has_pdf: boolean
  text_status: TextStatus
  summary: string | null
  created_at: string
}

export interface ChatMessage {
  id: number
  paper_id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`/api${path}`, init)
  } catch {
    throw new Error('Could not reach the backend. Is it running on port 8000?')
  }
  if (!resp.ok) {
    // FastAPI errors look like {"detail": "..."} or {"detail": [{"msg": "..."}]}
    let message = `Request failed (HTTP ${resp.status})`
    try {
      const body = await resp.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
    } catch {
      // keep the generic message
    }
    throw new Error(message)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

const jsonBody = (data: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})

export const api = {
  search: (q: string, limit = 10) =>
    request<SearchResult[]>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  listPapers: () => request<Paper[]>('/papers'),
  getPaper: (id: number) => request<Paper>(`/papers/${id}`),
  // The backend ignores the extra saved_id field
  savePaper: (result: SearchResult) => request<Paper>('/papers', jsonBody(result)),
  deletePaper: (id: number) => request<void>(`/papers/${id}`, { method: 'DELETE' }),
  retryFetchText: (id: number) => request<Paper>(`/papers/${id}/fetch-text`, { method: 'POST' }),
  pdfUrl: (id: number) => `/api/papers/${id}/pdf`,

  uploadPaper: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Paper>('/papers/upload', { method: 'POST', body: form })
  },

  summarize: (id: number, regenerate = false) =>
    request<Paper>(`/papers/${id}/summary${regenerate ? '?regenerate=true' : ''}`, { method: 'POST' }),

  getChat: (id: number) => request<ChatMessage[]>(`/papers/${id}/chat`),
  ask: (id: number, question: string) =>
    request<ChatMessage[]>(`/papers/${id}/chat`, jsonBody({ question })),
  clearChat: (id: number) => request<void>(`/papers/${id}/chat`, { method: 'DELETE' }),
}
