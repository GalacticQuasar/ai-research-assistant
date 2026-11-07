import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import App from '../src/App'
import { jsonResponse, makePaper, mockFetch } from './helpers'

function renderAppAt(path: string) {
  window.history.pushState({}, '', path)
  return render(<App />)
}

afterEach(() => {
  window.history.pushState({}, '', '/')
})

describe('App routing', () => {
  it('shows the search page at /', () => {
    mockFetch({})
    renderAppAt('/')
    expect(screen.getByRole('heading', { name: 'Search arXiv for papers' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Search' })).toHaveClass('active')
    expect(screen.getByRole('link', { name: 'Library' })).not.toHaveClass('active')
  })

  it('shows the library at /library', async () => {
    mockFetch({ 'GET /api/papers': jsonResponse([]) })
    renderAppAt('/library')
    expect(await screen.findByRole('heading', { name: 'Library' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Library' })).toHaveClass('active')
  })

  it('shows a paper at /papers/:id', async () => {
    mockFetch({ 'GET /api/papers/3': jsonResponse(makePaper({ id: 3 })), 'GET /api/papers/3/chat': jsonResponse([]) })
    renderAppAt('/papers/3')
    expect(await screen.findByRole('heading', { name: 'Sparse Attention for Long Documents' })).toBeInTheDocument()
  })

  it('shows not found for unknown routes', () => {
    mockFetch({})
    renderAppAt('/nope')
    expect(screen.getByText('Page not found.')).toBeInTheDocument()
  })

  it('navigates with the top bar', async () => {
    const user = userEvent.setup()
    mockFetch({ 'GET /api/papers': jsonResponse([]) })
    renderAppAt('/')

    await user.click(screen.getByRole('link', { name: 'Library' }))
    expect(await screen.findByText(/Your library is empty/)).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'Research Assistant' }))
    expect(screen.getByRole('heading', { name: 'Search arXiv for papers' })).toBeInTheDocument()
  })
})

describe('main entry point', () => {
  it('renders the app into #root', async () => {
    mockFetch({})
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)

    await import('../src/main.tsx')

    expect(await screen.findByRole('heading', { name: 'Search arXiv for papers' })).toBeInTheDocument()
    root.remove()
  })
})
