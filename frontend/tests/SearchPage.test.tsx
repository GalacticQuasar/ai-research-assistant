import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { callsTo, deferred, errorResponse, jsonResponse, makePaper, makeResult, mockFetch, renderAt } from './helpers'

const RESULTS = [
  makeResult({ external_id: 'a1', title: 'Diffusion Models Beat GANs' }),
  makeResult({ external_id: 'a2', title: 'Denoising Diffusion', saved_id: 7 }),
]
const SEARCH_URL = 'GET /api/search?q=diffusion%20models&limit=10'

const searchBox = () => screen.getByRole('searchbox', { name: 'Keywords or a research topic' })
const card = (title: string) => screen.getByRole('heading', { name: title }).closest('article') as HTMLElement

describe('SearchPage', () => {
  it('starts empty without searching', () => {
    const fetchMock = mockFetch({})
    renderAt('/')
    expect(screen.getByRole('heading', { name: 'Search arXiv for papers' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('disables Search for whitespace-only input', async () => {
    const user = userEvent.setup()
    mockFetch({})
    renderAt('/')
    await user.type(searchBox(), '   ')
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled()
  })

  it('searches, puts the query in the URL, and shows results', async () => {
    const user = userEvent.setup()
    const reply = deferred<Response>()
    mockFetch({ [SEARCH_URL]: () => reply.promise })
    renderAt('/')

    await user.type(searchBox(), '  diffusion models ')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(screen.getByTestId('location')).toHaveTextContent('/?q=diffusion+models')
    expect(screen.getByText('Searching arXiv…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Searching…' })).toBeDisabled()

    reply.resolve(jsonResponse(RESULTS))

    expect(await screen.findByRole('heading', { name: 'Diffusion Models Beat GANs' })).toBeInTheDocument()
    expect(screen.queryByText('Searching arXiv…')).not.toBeInTheDocument()
    expect(within(card('Diffusion Models Beat GANs')).getByRole('button', { name: 'Save to library' })).toBeEnabled()
  })

  it('runs the search from the URL on load', async () => {
    mockFetch({ [SEARCH_URL]: jsonResponse(RESULTS) })
    renderAt('/?q=diffusion%20models')
    expect(searchBox()).toHaveValue('diffusion models')
    expect(await screen.findByRole('heading', { name: 'Denoising Diffusion' })).toBeInTheDocument()
  })

  it('marks results already in the library', async () => {
    mockFetch({ [SEARCH_URL]: jsonResponse(RESULTS) })
    renderAt('/?q=diffusion%20models')

    await screen.findByRole('heading', { name: 'Denoising Diffusion' })
    const saved = card('Denoising Diffusion')
    expect(within(saved).getByText('Saved')).toBeInTheDocument()
    expect(within(saved).getByRole('link', { name: 'Open paper' })).toHaveAttribute('href', '/papers/7')
    expect(within(saved).queryByRole('button', { name: 'Save to library' })).not.toBeInTheDocument()
  })

  it('says when nothing was found', async () => {
    mockFetch({ 'GET /api/search?q=zzzz&limit=10': jsonResponse([]) })
    renderAt('/?q=zzzz')
    expect(await screen.findByText('No papers found. Try different keywords.')).toBeInTheDocument()
  })

  it('shows the error and re-runs the same query on resubmit', async () => {
    const user = userEvent.setup()
    const fetchMock = mockFetch({
      [SEARCH_URL]: [
        errorResponse('arXiv is rate-limiting requests. Please try again in a minute.', 503),
        jsonResponse(RESULTS),
      ],
    })
    renderAt('/?q=diffusion%20models')

    expect(await screen.findByText(/rate-limiting requests/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByRole('heading', { name: 'Diffusion Models Beat GANs' })).toBeInTheDocument()
    expect(screen.queryByText(/rate-limiting requests/)).not.toBeInTheDocument()
    expect(callsTo(fetchMock, SEARCH_URL)).toHaveLength(2)
  })

  it('saves a result and switches it to Saved', async () => {
    const user = userEvent.setup()
    const reply = deferred<Response>()
    const fetchMock = mockFetch({ [SEARCH_URL]: jsonResponse(RESULTS), 'POST /api/papers': () => reply.promise })
    renderAt('/?q=diffusion%20models')
    await screen.findByRole('heading', { name: 'Diffusion Models Beat GANs' })

    await user.click(within(card('Diffusion Models Beat GANs')).getByRole('button', { name: 'Save to library' }))

    expect(within(card('Diffusion Models Beat GANs')).getByRole('button', { name: 'Saving…' })).toBeDisabled()
    const [, init] = callsTo(fetchMock, 'POST /api/papers')[0]
    expect(JSON.parse(init!.body as string)).toMatchObject({ external_id: 'a1', title: 'Diffusion Models Beat GANs' })

    reply.resolve(jsonResponse(makePaper({ id: 42 }), 201))

    const link = await within(card('Diffusion Models Beat GANs')).findByRole('link', { name: 'Open paper' })
    expect(link).toHaveAttribute('href', '/papers/42')
    expect(within(card('Diffusion Models Beat GANs')).getByText('Saved')).toBeInTheDocument()
  })

  it('shows an error when saving fails, and a new search clears it', async () => {
    const user = userEvent.setup()
    mockFetch({
      [SEARCH_URL]: jsonResponse(RESULTS),
      'GET /api/search?q=transformers&limit=10': jsonResponse([]),
      'POST /api/papers': errorResponse('Request failed (HTTP 500)', 500),
    })
    renderAt('/?q=diffusion%20models')
    await screen.findByRole('heading', { name: 'Diffusion Models Beat GANs' })

    await user.click(within(card('Diffusion Models Beat GANs')).getByRole('button', { name: 'Save to library' }))

    expect(
      await screen.findByText('Could not save "Diffusion Models Beat GANs": Request failed (HTTP 500)'),
    ).toBeInTheDocument()
    expect(within(card('Diffusion Models Beat GANs')).getByRole('button', { name: 'Save to library' })).toBeEnabled()

    await user.clear(searchBox())
    await user.type(searchBox(), 'transformers{Enter}')

    expect(await screen.findByText('No papers found. Try different keywords.')).toBeInTheDocument()
    expect(screen.queryByText(/Could not save/)).not.toBeInTheDocument()
  })
})
