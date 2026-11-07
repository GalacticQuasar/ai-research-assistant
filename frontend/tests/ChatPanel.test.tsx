import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ChatPanel from '../src/components/ChatPanel'
import { callsTo, deferred, errorResponse, jsonResponse, makeMessage, mockFetch } from './helpers'

const HISTORY = [
  makeMessage({ id: 1, role: 'user', content: 'What is new?' }),
  makeMessage({ id: 2, role: 'assistant', content: 'A **sparse** kernel.' }),
]

function exchange(question: string, answer: string, firstId = 10) {
  return [
    makeMessage({ id: firstId, role: 'user', content: question }),
    makeMessage({ id: firstId + 1, role: 'assistant', content: answer }),
  ]
}

const questionBox = () => screen.getByRole('textbox', { name: 'Your question' })
const askButton = () => screen.getByRole('button', { name: 'Ask' })

describe('ChatPanel', () => {
  it('loads and renders the saved conversation', async () => {
    mockFetch({ 'GET /api/papers/1/chat': jsonResponse(HISTORY) })
    render(<ChatPanel paperId={1} ready />)

    expect(await screen.findByText('What is new?')).toBeInTheDocument()
    // Assistant replies are rendered as Markdown
    expect(screen.getByText('sparse').tagName).toBe('STRONG')
    expect(screen.getByRole('button', { name: 'Clear' })).toBeInTheDocument()
    // Example questions only appear for an empty conversation
    expect(screen.queryByText(/Try one of these/)).not.toBeInTheDocument()
  })

  it('shows example questions for an empty conversation', async () => {
    mockFetch({ 'GET /api/papers/1/chat': jsonResponse([]) })
    render(<ChatPanel paperId={1} ready />)

    expect(await screen.findByText(/Try one of these/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'What datasets are used?' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument()
  })

  it('is disabled until the full text is ready', async () => {
    mockFetch({ 'GET /api/papers/1/chat': jsonResponse([]) })
    render(<ChatPanel paperId={1} ready={false} />)

    expect(screen.getByText('You can ask questions once the full text has loaded.')).toBeInTheDocument()
    expect(questionBox()).toBeDisabled()
    expect(questionBox()).toHaveAttribute('placeholder', 'Waiting for full text…')
    expect(askButton()).toBeDisabled()
    expect(screen.queryByText(/Try one of these/)).not.toBeInTheDocument()
  })

  it('shows an error when the history cannot be loaded', async () => {
    mockFetch({ 'GET /api/papers/1/chat': errorResponse('Paper not found', 404) })
    render(<ChatPanel paperId={1} ready />)
    expect(await screen.findByText('Paper not found')).toBeInTheDocument()
  })

  it('disables Ask for blank input and limits the length', async () => {
    const user = userEvent.setup()
    mockFetch({ 'GET /api/papers/1/chat': jsonResponse([]) })
    render(<ChatPanel paperId={1} ready />)

    expect(askButton()).toBeDisabled()
    await user.type(questionBox(), '   ')
    expect(askButton()).toBeDisabled()
    expect(questionBox()).toHaveAttribute('maxLength', '2000')
  })

  it('asks a trimmed question and appends the exchange, with a pending state', async () => {
    const user = userEvent.setup()
    const reply = deferred<Response>()
    const fetchMock = mockFetch({
      'GET /api/papers/1/chat': jsonResponse(HISTORY),
      'POST /api/papers/1/chat': () => reply.promise,
    })
    render(<ChatPanel paperId={1} ready />)
    await screen.findByText('What is new?')

    await user.type(questionBox(), '  What datasets?  {Enter}')

    // While waiting: the question shows, input is cleared and locked
    expect(screen.getByRole('status')).toHaveTextContent('Reading the paper…')
    expect(screen.getByText('What datasets?')).toBeInTheDocument()
    expect(questionBox()).toHaveValue('')
    expect(questionBox()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Clear' })).toBeDisabled()
    const [, init] = callsTo(fetchMock, 'POST /api/papers/1/chat')[0]
    expect(JSON.parse(init!.body as string)).toEqual({ question: 'What datasets?' })

    reply.resolve(jsonResponse(exchange('What datasets?', 'ImageNet.')))

    expect(await screen.findByText('ImageNet.')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getAllByText('What datasets?')).toHaveLength(1)
    expect(screen.getByText('What is new?')).toBeInTheDocument()
    expect(questionBox()).toBeEnabled()
  })

  it('asks an example question with one click', async () => {
    const user = userEvent.setup()
    const question = 'What are the major limitations?'
    const fetchMock = mockFetch({
      'GET /api/papers/1/chat': jsonResponse([]),
      'POST /api/papers/1/chat': jsonResponse(exchange(question, 'Small datasets.')),
    })
    render(<ChatPanel paperId={1} ready />)

    await user.click(await screen.findByRole('button', { name: question }))

    expect(await screen.findByText('Small datasets.')).toBeInTheDocument()
    const [, init] = callsTo(fetchMock, 'POST /api/papers/1/chat')[0]
    expect(JSON.parse(init!.body as string)).toEqual({ question })
    expect(screen.queryByText(/Try one of these/)).not.toBeInTheDocument()
  })

  it('restores the question and shows the error when asking fails', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/papers/1/chat': jsonResponse([]),
      'POST /api/papers/1/chat': [
        errorResponse('The LLM service is rate-limiting requests. Please try again in a minute.', 502),
        jsonResponse(exchange('Why?', 'Because.')),
      ],
    })
    render(<ChatPanel paperId={1} ready />)
    await screen.findByText(/Try one of these/)

    await user.type(questionBox(), 'Why?')
    await user.click(askButton())

    expect(await screen.findByText(/rate-limiting requests/)).toBeInTheDocument()
    expect(questionBox()).toHaveValue('Why?')

    // Retrying clears the error
    await user.click(askButton())
    expect(await screen.findByText('Because.')).toBeInTheDocument()
    expect(screen.queryByText(/rate-limiting requests/)).not.toBeInTheDocument()
  })

  it('clears the conversation after confirmation', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = mockFetch({
      'GET /api/papers/1/chat': jsonResponse(HISTORY),
      'DELETE /api/papers/1/chat': jsonResponse(null, 204),
    })
    render(<ChatPanel paperId={1} ready />)

    await user.click(await screen.findByRole('button', { name: 'Clear' }))

    expect(confirm).toHaveBeenCalledWith('Clear the conversation for this paper?')
    await vi.waitFor(() => expect(screen.queryByText('What is new?')).not.toBeInTheDocument())
    expect(callsTo(fetchMock, 'DELETE /api/papers/1/chat')).toHaveLength(1)
    expect(screen.getByText(/Try one of these/)).toBeInTheDocument()
  })

  it('keeps the conversation when clearing is cancelled', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetchMock = mockFetch({ 'GET /api/papers/1/chat': jsonResponse(HISTORY) })
    render(<ChatPanel paperId={1} ready />)

    await user.click(await screen.findByRole('button', { name: 'Clear' }))

    expect(screen.getByText('What is new?')).toBeInTheDocument()
    expect(callsTo(fetchMock, 'DELETE /api/papers/1/chat')).toHaveLength(0)
  })

  it('shows an error when clearing fails and keeps the messages', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch({
      'GET /api/papers/1/chat': jsonResponse(HISTORY),
      'DELETE /api/papers/1/chat': errorResponse('Paper not found', 404),
    })
    render(<ChatPanel paperId={1} ready />)

    await user.click(await screen.findByRole('button', { name: 'Clear' }))

    expect(await screen.findByText('Paper not found')).toBeInTheDocument()
    expect(screen.getByText('What is new?')).toBeInTheDocument()
  })

  it('reloads the history when the paper changes', async () => {
    mockFetch({
      'GET /api/papers/1/chat': jsonResponse(HISTORY),
      'GET /api/papers/2/chat': jsonResponse([makeMessage({ id: 9, paper_id: 2, content: 'Paper two question' })]),
    })
    const { rerender } = render(<ChatPanel paperId={1} ready />)
    await screen.findByText('What is new?')

    rerender(<ChatPanel paperId={2} ready />)

    const log = await screen.findByText('Paper two question')
    expect(within(log.closest('.chat-log') as HTMLElement).queryByText('What is new?')).not.toBeInTheDocument()
  })
})
