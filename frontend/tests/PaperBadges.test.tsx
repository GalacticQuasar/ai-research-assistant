import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PaperBadges from '../src/components/PaperBadges'
import { makePaper } from './helpers'

describe('PaperBadges', () => {
  it('labels arXiv papers with ready text and no summary', () => {
    render(<PaperBadges paper={makePaper()} />)
    expect(screen.getByText('From arXiv')).toBeInTheDocument()
    expect(screen.queryByText('Loading full text…')).not.toBeInTheDocument()
    expect(screen.queryByText('Full text unavailable')).not.toBeInTheDocument()
    expect(screen.queryByText('Summarized')).not.toBeInTheDocument()
  })

  it('labels uploads', () => {
    render(<PaperBadges paper={makePaper({ source: 'upload' })} />)
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument()
  })

  it('shows pending and failed text states', () => {
    const { rerender } = render(<PaperBadges paper={makePaper({ text_status: 'pending' })} />)
    expect(screen.getByText('Loading full text…')).toBeInTheDocument()
    rerender(<PaperBadges paper={makePaper({ text_status: 'failed' })} />)
    expect(screen.getByText('Full text unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Loading full text…')).not.toBeInTheDocument()
  })

  it('shows when a summary exists', () => {
    render(<PaperBadges paper={makePaper({ summary: '## TL;DR' })} />)
    expect(screen.getByText('Summarized')).toBeInTheDocument()
  })
})
