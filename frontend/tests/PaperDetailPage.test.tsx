import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { callsTo, errorResponse, jsonResponse, makePaper, mockFetch, renderAt } from './helpers'

const CHAT = { 'GET /api/papers/1/chat': jsonResponse([]) }

describe('PaperDetailPage', () => {
  it('shows the paper with its links, abstract, summary, and chat', async () => {
    mockFetch({ 'GET /api/papers/1': jsonResponse(makePaper()), ...CHAT })
    renderAt('/papers/1')

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Sparse Attention for Long Documents' })).toBeInTheDocument()
    expect(screen.getByText('Ada Lovelace, Alan Turing')).toBeInTheDocument()
    expect(screen.getByText('2024')).toBeInTheDocument()
    expect(screen.getByText('We study sparse attention.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'arXiv page' })).toHaveAttribute('href', 'http://arxiv.org/abs/2401.01234v2')
    // A local copy is preferred over the remote PDF
    expect(screen.getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', '/api/papers/1/pdf')
    expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute('href', '/library')
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeEnabled()
    expect(await screen.findByText(/Try one of these/)).toBeInTheDocument()
  })

  it('links the remote PDF when there is no local copy', async () => {
    mockFetch({ 'GET /api/papers/1': jsonResponse(makePaper({ has_pdf: false })), ...CHAT })
    renderAt('/papers/1')
    expect(await screen.findByRole('link', { name: 'View PDF' })).toHaveAttribute(
      'href',
      'http://arxiv.org/pdf/2401.01234v2',
    )
  })

  it('handles an upload with missing metadata', async () => {
    const paper = makePaper({ source: 'upload', authors: [], year: null, url: null, pdf_url: null, has_pdf: false,
      abstract: null })
    mockFetch({ 'GET /api/papers/1': jsonResponse(paper), ...CHAT })
    renderAt('/papers/1')

    expect(await screen.findByText('Unknown authors')).toBeInTheDocument()
    expect(screen.getByText('No abstract available.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'arXiv page' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'View PDF' })).not.toBeInTheDocument()
  })

  it('shows an error and a way back when the paper does not exist', async () => {
    mockFetch({ 'GET /api/papers/404': errorResponse('Paper not found', 404), 'GET /api/papers/404/chat': errorResponse('Paper not found', 404) })
    renderAt('/papers/404')

    expect(await screen.findByText('Paper not found')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to library' })).toHaveAttribute('href', '/library')
  })

  it('warns when the full text failed and retries the download', async () => {
    const user = userEvent.setup()
    const fetchMock = mockFetch({
      'GET /api/papers/1': jsonResponse(makePaper({ text_status: 'failed', has_pdf: false })),
      'POST /api/papers/1/fetch-text': jsonResponse(makePaper({ text_status: 'pending', has_pdf: false })),
      ...CHAT,
    })
    renderAt('/papers/1')

    expect(await screen.findByText(/couldn't be loaded/)).toBeInTheDocument()
    expect(screen.getByText('Full text unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Loading full text…')).toBeInTheDocument()
    expect(screen.queryByText(/couldn't be loaded/)).not.toBeInTheDocument()
    expect(callsTo(fetchMock, 'POST /api/papers/1/fetch-text')).toHaveLength(1)
  })

  it('offers no retry for a failed paper without a PDF link', async () => {
    mockFetch({ 'GET /api/papers/1': jsonResponse(makePaper({ text_status: 'failed', pdf_url: null })), ...CHAT })
    renderAt('/papers/1')
    expect(await screen.findByText(/couldn't be loaded/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('shows an error when the retry fails, without losing the paper', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/papers/1': jsonResponse(makePaper({ text_status: 'failed' })),
      'POST /api/papers/1/fetch-text': errorResponse('This paper has no PDF link', 400),
      ...CHAT,
    })
    renderAt('/papers/1')

    await user.click(await screen.findByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('This paper has no PDF link')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sparse Attention for Long Documents' })).toBeInTheDocument()
  })

  it('clears the retry error once a later retry succeeds', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/papers/1': jsonResponse(makePaper({ text_status: 'failed' })),
      'POST /api/papers/1/fetch-text': [
        errorResponse('Could not reach the backend. Is it running on port 8000?', 503),
        jsonResponse(makePaper({ text_status: 'pending' })),
      ],
      ...CHAT,
    })
    renderAt('/papers/1')

    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/Could not reach the backend/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Loading full text…')).toBeInTheDocument()

    expect(screen.queryByText(/Could not reach the backend/)).not.toBeInTheDocument()
  })

  it('polls while the text is pending, then enables summary and chat', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = mockFetch({
      'GET /api/papers/1': [
        jsonResponse(makePaper({ text_status: 'pending', has_pdf: false })),
        jsonResponse(makePaper({ text_status: 'ready' })),
      ],
      ...CHAT,
    })
    renderAt('/papers/1')

    expect(await screen.findByText('Loading full text…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeDisabled()

    await act(() => vi.advanceTimersByTimeAsync(2000))

    expect(screen.queryByText('Loading full text…')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeEnabled()
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBeEnabled()

    await act(() => vi.advanceTimersByTimeAsync(10000))
    expect(callsTo(fetchMock, 'GET /api/papers/1')).toHaveLength(2)
  })

  it('updates the page after generating a summary', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/papers/1': jsonResponse(makePaper()),
      'POST /api/papers/1/summary': jsonResponse(makePaper({ summary: '## TL;DR\nGreat paper.' })),
      ...CHAT,
    })
    renderAt('/papers/1')

    await user.click(await screen.findByRole('button', { name: 'Generate summary' }))

    expect(await screen.findByText('Great paper.')).toBeInTheDocument()
    expect(screen.getByText('Summarized')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument()
  })
})
