import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import PaperCard from '../src/components/PaperCard'

type Props = ComponentProps<typeof PaperCard>

function renderCard(props: Partial<Props> = {}) {
  const defaults: Props = { title: 'A Paper', authors: ['Ada Lovelace'], year: 2024, abstract: 'Short abstract.' }
  return render(
    <MemoryRouter>
      <PaperCard {...defaults} {...props} />
    </MemoryRouter>,
  )
}

describe('PaperCard', () => {
  it('shows title, authors, year, and abstract', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: 'A Paper' })).toBeInTheDocument()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('2024')).toBeInTheDocument()
    expect(screen.getByText('Short abstract.')).toBeInTheDocument()
  })

  it('shows n.d. when the year is unknown and handles missing authors', () => {
    renderCard({ year: null, authors: [] })
    expect(screen.getByText('n.d.')).toBeInTheDocument()
    expect(screen.getByText('Unknown authors')).toBeInTheDocument()
  })

  it('shows a placeholder when there is no abstract', () => {
    renderCard({ abstract: null })
    expect(screen.getByText('No abstract available.')).toBeInTheDocument()
  })

  it('links the title to the detail page when detailHref is given', () => {
    renderCard({ detailHref: '/papers/3', url: 'https://arxiv.org/abs/1' })
    expect(screen.getByRole('link', { name: 'A Paper' })).toHaveAttribute('href', '/papers/3')
  })

  it('links the title to the external page otherwise', () => {
    renderCard({ url: 'https://arxiv.org/abs/1' })
    const title = screen.getByRole('link', { name: 'A Paper' })
    expect(title).toHaveAttribute('href', 'https://arxiv.org/abs/1')
    expect(title).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: 'arXiv page' })).toHaveAttribute('href', 'https://arxiv.org/abs/1')
  })

  it('renders a plain title without any link', () => {
    renderCard()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders badges and actions', () => {
    renderCard({ badges: <span>Badge!</span>, actions: <button>Do it</button> })
    expect(screen.getByText('Badge!')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Do it' })).toBeInTheDocument()
  })

  it('truncates a long abstract and expands/collapses it', async () => {
    const user = userEvent.setup()
    const abstract = `${'word '.repeat(200)}THE END` // a long abstract, ~1,000 characters
    renderCard({ abstract })

    const toggle = screen.getByRole('button', { name: 'Read full abstract' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/THE END/)).not.toBeInTheDocument()
    expect(screen.getByText(/word…/)).toBeInTheDocument()

    await user.click(toggle)
    expect(screen.getByText(/THE END/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true')

    await user.click(screen.getByRole('button', { name: 'Show less' }))
    expect(screen.queryByText(/THE END/)).not.toBeInTheDocument()
  })
})
