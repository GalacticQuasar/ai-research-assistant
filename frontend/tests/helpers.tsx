import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import type { ChatMessage, Paper, SearchResult } from '../src/api'
import LibraryPage from '../src/pages/LibraryPage'
import PaperDetailPage from '../src/pages/PaperDetailPage'
import SearchPage from '../src/pages/SearchPage'

export function jsonResponse(body: unknown, status = 200): Response {
  if (status === 204) return new Response(null, { status })
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function errorResponse(detail: string, status: number): Response {
  return jsonResponse({ detail }, status)
}

type Reply = Response | ((init: RequestInit | undefined) => Response | Promise<Response>)

/**
 * Stub global fetch with a route table keyed by "METHOD /url" (the exact URL including the query).
 * A value is a Response, a function returning one, or an array of those used one per call
 * (the last one repeats). Unmatched requests get a 599 so tests fail with a clear message.
 */
export function mockFetch(routes: Record<string, Reply | Reply[]>) {
  const counts: Record<string, number> = {}
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const entry = routes[key]
    if (entry === undefined) return errorResponse(`Unmocked request: ${key}`, 599)
    const replies = Array.isArray(entry) ? entry : [entry]
    const n = counts[key] ?? 0
    counts[key] = n + 1
    const reply = replies[Math.min(n, replies.length - 1)]
    // A Response body can be read only once, so clone stored responses
    return typeof reply === 'function' ? reply(init) : reply.clone()
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Calls made to the fetch mock for a given "METHOD /url" key. */
export function callsTo(fetchMock: ReturnType<typeof mockFetch>, key: string) {
  return fetchMock.mock.calls.filter(([input, init]) => `${init?.method ?? 'GET'} ${String(input)}` === key)
}

/** A promise you resolve by hand, to observe loading states. */
export function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

export function makePaper(overrides: Partial<Paper> = {}): Paper {
  return {
    id: 1,
    source: 'arxiv',
    external_id: '2401.01234v2',
    title: 'Sparse Attention for Long Documents',
    authors: ['Ada Lovelace', 'Alan Turing'],
    year: 2024,
    abstract: 'We study sparse attention.',
    url: 'http://arxiv.org/abs/2401.01234v2',
    pdf_url: 'http://arxiv.org/pdf/2401.01234v2',
    has_pdf: true,
    text_status: 'ready',
    summary: null,
    created_at: '2026-01-01T00:00:00',
    ...overrides,
  }
}

export function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    external_id: '2401.01234v2',
    title: 'Sparse Attention for Long Documents',
    authors: ['Ada Lovelace'],
    year: 2024,
    abstract: 'We study sparse attention.',
    url: 'http://arxiv.org/abs/2401.01234v2',
    pdf_url: 'http://arxiv.org/pdf/2401.01234v2',
    saved_id: null,
    ...overrides,
  }
}

export function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 1,
    paper_id: 1,
    role: 'user',
    content: 'What problem does this paper address?',
    created_at: '2026-01-01T00:00:00',
    ...overrides,
  }
}

function LocationDisplay() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

/** Render the app's pages under a MemoryRouter starting at `path`. */
export function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<SearchPage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/papers/:id" element={<PaperDetailPage />} />
      </Routes>
      <LocationDisplay />
    </MemoryRouter>,
  )
}
