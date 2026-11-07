import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { callsTo, deferred, errorResponse, jsonResponse, makePaper, mockFetch, renderAt } from './helpers'

const PAPERS = [
  makePaper({ id: 1, title: 'First Paper', summary: 'Done' }),
  makePaper({ id: 2, title: 'Uploaded Paper', source: 'upload', url: null, text_status: 'failed' }),
]

const card = (title: string) => screen.getByRole('heading', { name: title }).closest('article') as HTMLElement
const fileInput = (container: HTMLElement) => container.querySelector('input[type="file"]') as HTMLInputElement
const pdfFile = () => new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' })

describe('LibraryPage', () => {
  it('shows a loading message, then the papers with badges and links', async () => {
    const reply = deferred<Response>()
    mockFetch({ 'GET /api/papers': () => reply.promise })
    renderAt('/library')

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    reply.resolve(jsonResponse(PAPERS))

    expect(await screen.findByText('2 papers')).toBeInTheDocument()
    const first = card('First Paper')
    expect(within(first).getByText('From arXiv')).toBeInTheDocument()
    expect(within(first).getByText('Summarized')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: 'First Paper' })).toHaveAttribute('href', '/papers/1')
    expect(within(first).getByRole('link', { name: 'Open paper' })).toHaveAttribute('href', '/papers/1')
    const upload = card('Uploaded Paper')
    expect(within(upload).getByText('Uploaded PDF')).toBeInTheDocument()
    expect(within(upload).getByText('Full text unavailable')).toBeInTheDocument()
    expect(within(upload).queryByRole('link', { name: 'arXiv page' })).not.toBeInTheDocument()
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
  })

  it('uses the singular for one paper', async () => {
    mockFetch({ 'GET /api/papers': jsonResponse([PAPERS[0]]) })
    renderAt('/library')
    expect(await screen.findByText('1 paper')).toBeInTheDocument()
  })

  it('explains an empty library', async () => {
    mockFetch({ 'GET /api/papers': jsonResponse([]) })
    renderAt('/library')
    expect(await screen.findByText(/Your library is empty/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Search arXiv' })).toHaveAttribute('href', '/')
  })

  it('shows an error when the library cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    renderAt('/library')
    expect(await screen.findByText('Could not reach the backend. Is it running on port 8000?')).toBeInTheDocument()
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
  })

  it('polls while a paper is pending and stops once it is ready', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const pending = makePaper({ id: 1, title: 'First Paper', text_status: 'pending' })
    const ready = { ...pending, text_status: 'ready' as const }
    const fetchMock = mockFetch({
      'GET /api/papers': [jsonResponse([pending]), jsonResponse([pending]), jsonResponse([ready])],
    })
    renderAt('/library')
    expect(await screen.findByText('Loading full text…')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(callsTo(fetchMock, 'GET /api/papers')).toHaveLength(2)
    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(callsTo(fetchMock, 'GET /api/papers')).toHaveLength(3)
    expect(screen.queryByText('Loading full text…')).not.toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(10000))
    expect(callsTo(fetchMock, 'GET /api/papers')).toHaveLength(3)
  })

  it('does not poll when nothing is pending', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = mockFetch({ 'GET /api/papers': jsonResponse(PAPERS) })
    renderAt('/library')
    await screen.findByText('2 papers')
    await act(() => vi.advanceTimersByTimeAsync(6000))
    expect(callsTo(fetchMock, 'GET /api/papers')).toHaveLength(1)
  })

  it('removes a paper after confirmation', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = mockFetch({ 'GET /api/papers': jsonResponse(PAPERS), 'DELETE /api/papers/1': jsonResponse(null, 204) })
    renderAt('/library')

    await screen.findByText('2 papers')
    await user.click(within(card('First Paper')).getByRole('button', { name: 'Remove' }))

    expect(confirm).toHaveBeenCalledWith('Remove "First Paper" from your library?')
    await vi.waitFor(() => expect(screen.queryByText('First Paper')).not.toBeInTheDocument())
    expect(screen.getByText('1 paper')).toBeInTheDocument()
    expect(callsTo(fetchMock, 'DELETE /api/papers/1')).toHaveLength(1)
  })

  it('keeps the paper when removal is cancelled', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetchMock = mockFetch({ 'GET /api/papers': jsonResponse(PAPERS) })
    renderAt('/library')

    await screen.findByText('2 papers')
    await user.click(within(card('First Paper')).getByRole('button', { name: 'Remove' }))

    expect(screen.getByText('First Paper')).toBeInTheDocument()
    expect(callsTo(fetchMock, 'DELETE /api/papers/1')).toHaveLength(0)
  })

  it('shows an error when removal fails', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch({ 'GET /api/papers': jsonResponse(PAPERS), 'DELETE /api/papers/1': errorResponse('Paper not found', 404) })
    renderAt('/library')

    await screen.findByText('2 papers')
    await user.click(within(card('First Paper')).getByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('Could not remove the paper: Paper not found')).toBeInTheDocument()
    expect(screen.getByText('First Paper')).toBeInTheDocument()
  })

  it('opens the file picker from the Upload button', async () => {
    const user = userEvent.setup()
    mockFetch({ 'GET /api/papers': jsonResponse([]) })
    const { container } = renderAt('/library')
    const click = vi.spyOn(fileInput(container), 'click')

    await user.click(screen.getByRole('button', { name: 'Upload PDF' }))

    expect(click).toHaveBeenCalled()
    expect(fileInput(container)).toHaveAttribute('accept', 'application/pdf,.pdf')
  })

  it('uploads a PDF and opens the new paper', async () => {
    const user = userEvent.setup()
    const reply = deferred<Response>()
    const fetchMock = mockFetch({ 'GET /api/papers': jsonResponse([]), 'POST /api/papers/upload': () => reply.promise })
    const { container } = renderAt('/library')
    await screen.findByText(/Your library is empty/)

    await user.upload(fileInput(container), pdfFile())

    expect(screen.getByRole('button', { name: 'Uploading…' })).toBeDisabled()
    expect(screen.getByText(/Extracting text and metadata/)).toBeInTheDocument()
    const body = callsTo(fetchMock, 'POST /api/papers/upload')[0][1]!.body as FormData
    expect((body.get('file') as File).name).toBe('paper.pdf')

    reply.resolve(jsonResponse(makePaper({ id: 77, source: 'upload' }), 201))

    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/papers/77'))
  })

  it('shows an error when the upload fails and re-enables the button', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/papers': jsonResponse([]),
      'POST /api/papers/upload': errorResponse('The uploaded file is not a PDF', 400),
    })
    const { container } = renderAt('/library')
    await screen.findByText(/Your library is empty/)

    await user.upload(fileInput(container), pdfFile())

    expect(await screen.findByText('Upload failed: The uploaded file is not a PDF')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload PDF' })).toBeEnabled()
    expect(screen.getByTestId('location')).toHaveTextContent('/library')
    // The input is reset so the same file can be picked again
    expect(fileInput(container).value).toBe('')
  })
})
