import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Paper } from '../src/api'
import SummaryPanel from '../src/components/SummaryPanel'
import { callsTo, deferred, errorResponse, jsonResponse, makePaper, mockFetch } from './helpers'

function renderPanel(paper: Paper) {
  const onUpdated = vi.fn()
  render(<SummaryPanel paper={paper} onUpdated={onUpdated} />)
  return onUpdated
}

describe('SummaryPanel', () => {
  it('invites generating a summary when the text is ready', () => {
    renderPanel(makePaper())
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeEnabled()
    expect(screen.getByText(/Generate a summary of the problem/)).toBeInTheDocument()
  })

  it.each(['pending', 'failed'] as const)('disables generation while text is %s', (status) => {
    renderPanel(makePaper({ text_status: status }))
    expect(screen.getByRole('button', { name: 'Generate summary' })).toBeDisabled()
    expect(screen.getByText('You can generate a summary once the full text has loaded.')).toBeInTheDocument()
  })

  it('generates a summary, showing progress, and reports the updated paper', async () => {
    const user = userEvent.setup()
    const reply = deferred<Response>()
    const updated = makePaper({ summary: '## TL;DR\nIt works.' })
    const fetchMock = mockFetch({ 'POST /api/papers/1/summary': () => reply.promise })
    const onUpdated = renderPanel(makePaper())

    await user.click(screen.getByRole('button', { name: 'Generate summary' }))

    expect(screen.getByRole('button', { name: 'Generating…' })).toBeDisabled()
    expect(screen.getByText(/Reading the full paper/)).toBeInTheDocument()

    reply.resolve(jsonResponse(updated))
    expect(await screen.findByRole('button', { name: 'Generate summary' })).toBeEnabled()
    expect(onUpdated).toHaveBeenCalledWith(updated)
    expect(callsTo(fetchMock, 'POST /api/papers/1/summary')).toHaveLength(1)
  })

  it('renders an existing summary as Markdown and offers regeneration', async () => {
    const user = userEvent.setup()
    const fetchMock = mockFetch({
      'POST /api/papers/1/summary?regenerate=true': jsonResponse(makePaper({ summary: 'New' })),
    })
    const onUpdated = renderPanel(makePaper({ summary: '## TL;DR\nA **bold** claim.' }))

    expect(screen.getByRole('heading', { name: 'TL;DR' })).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))

    await vi.waitFor(() => expect(onUpdated).toHaveBeenCalledWith(makePaper({ summary: 'New' })))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('hides the old summary while regenerating', async () => {
    const user = userEvent.setup()
    mockFetch({ 'POST /api/papers/1/summary?regenerate=true': () => new Promise<Response>(() => {}) })
    renderPanel(makePaper({ summary: 'Old summary' }))

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))

    expect(screen.getByRole('button', { name: 'Regenerating…' })).toBeDisabled()
    expect(screen.queryByText('Old summary')).not.toBeInTheDocument()
  })

  it('shows the error when generation fails and allows retrying', async () => {
    const user = userEvent.setup()
    mockFetch({
      'POST /api/papers/1/summary': [errorResponse('LLM request failed: overloaded', 502), jsonResponse(makePaper())],
    })
    renderPanel(makePaper())

    await user.click(screen.getByRole('button', { name: 'Generate summary' }))
    expect(await screen.findByText('LLM request failed: overloaded')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Generate summary' }))
    await vi.waitFor(() => expect(screen.queryByText('LLM request failed: overloaded')).not.toBeInTheDocument())
  })
})
