import { describe, expect, it, vi } from 'vitest'
import { api } from '../src/api'
import { errorResponse, jsonResponse, makeMessage, makePaper, makeResult, mockFetch } from './helpers'

describe('api request URLs and methods', () => {
  it('encodes the search query and passes the limit', async () => {
    const fetchMock = mockFetch({ 'GET /api/search?q=graph%20neural%20nets%20%26%20C%2B%2B&limit=10': jsonResponse([]) })
    await expect(api.search('graph neural nets & C++')).resolves.toEqual([])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('uses a custom search limit', async () => {
    mockFetch({ 'GET /api/search?q=x&limit=3': jsonResponse([makeResult()]) })
    await expect(api.search('x', 3)).resolves.toHaveLength(1)
  })

  it('lists and gets papers', async () => {
    const paper = makePaper({ id: 4 })
    mockFetch({ 'GET /api/papers': jsonResponse([paper]), 'GET /api/papers/4': jsonResponse(paper) })
    await expect(api.listPapers()).resolves.toEqual([paper])
    await expect(api.getPaper(4)).resolves.toEqual(paper)
  })

  it('saves a search result as JSON', async () => {
    const result = makeResult()
    const fetchMock = mockFetch({ 'POST /api/papers': jsonResponse(makePaper(), 201) })

    await api.savePaper(result)

    const init = fetchMock.mock.calls[0][1]!
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(init.body as string)).toEqual(result)
  })

  it('returns undefined for 204 responses', async () => {
    mockFetch({
      'DELETE /api/papers/2': jsonResponse(null, 204),
      'DELETE /api/papers/2/chat': jsonResponse(null, 204),
    })
    await expect(api.deletePaper(2)).resolves.toBeUndefined()
    await expect(api.clearChat(2)).resolves.toBeUndefined()
  })

  it('retries text fetching with POST', async () => {
    mockFetch({ 'POST /api/papers/3/fetch-text': jsonResponse(makePaper({ id: 3, text_status: 'pending' })) })
    await expect(api.retryFetchText(3)).resolves.toMatchObject({ text_status: 'pending' })
  })

  it('builds the PDF URL without a request', () => {
    const fetchMock = mockFetch({})
    expect(api.pdfUrl(9)).toBe('/api/papers/9/pdf')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('uploads the file as multipart form data', async () => {
    const fetchMock = mockFetch({ 'POST /api/papers/upload': jsonResponse(makePaper({ source: 'upload' }), 201) })
    const file = new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' })

    await api.uploadPaper(file)

    const body = fetchMock.mock.calls[0][1]!.body
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('file')).toBeInstanceOf(File)
    expect(((body as FormData).get('file') as File).name).toBe('paper.pdf')
  })

  it('requests a summary, optionally regenerating it', async () => {
    const fetchMock = mockFetch({
      'POST /api/papers/1/summary': jsonResponse(makePaper({ summary: 'cached' })),
      'POST /api/papers/1/summary?regenerate=true': jsonResponse(makePaper({ summary: 'fresh' })),
    })
    await expect(api.summarize(1)).resolves.toMatchObject({ summary: 'cached' })
    await expect(api.summarize(1, true)).resolves.toMatchObject({ summary: 'fresh' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('gets the chat and asks a question', async () => {
    const reply = [makeMessage(), makeMessage({ id: 2, role: 'assistant', content: 'Answer' })]
    const fetchMock = mockFetch({
      'GET /api/papers/1/chat': jsonResponse([]),
      'POST /api/papers/1/chat': jsonResponse(reply),
    })
    await expect(api.getChat(1)).resolves.toEqual([])
    await expect(api.ask(1, 'Why?')).resolves.toEqual(reply)
    expect(JSON.parse(fetchMock.mock.calls[1][1]!.body as string)).toEqual({ question: 'Why?' })
  })
})

describe('api error handling', () => {
  it('uses a string detail from FastAPI', async () => {
    mockFetch({ 'GET /api/papers/5': errorResponse('Paper not found', 404) })
    await expect(api.getPaper(5)).rejects.toThrow('Paper not found')
  })

  it('uses the first validation message from a detail list', async () => {
    mockFetch({
      'POST /api/papers/1/chat': jsonResponse({ detail: [{ msg: 'String should have at least 1 character' }] }, 422),
    })
    await expect(api.ask(1, '')).rejects.toThrow('String should have at least 1 character')
  })

  it('falls back to a generic message for non-JSON errors', async () => {
    mockFetch({ 'GET /api/papers': new Response('<html>Bad gateway</html>', { status: 502 }) })
    await expect(api.listPapers()).rejects.toThrow('Request failed (HTTP 502)')
  })

  it('falls back to a generic message for JSON errors without detail', async () => {
    mockFetch({ 'GET /api/papers': jsonResponse({ error: 'nope' }, 500) })
    await expect(api.listPapers()).rejects.toThrow('Request failed (HTTP 500)')
  })

  it('falls back to a generic message for an empty detail list', async () => {
    mockFetch({ 'GET /api/papers': jsonResponse({ detail: [] }, 422) })
    await expect(api.listPapers()).rejects.toThrow('Request failed (HTTP 422)')
  })

  it('explains when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.listPapers()).rejects.toThrow('Could not reach the backend. Is it running on port 8000?')
  })
})
