import { describe, expect, it } from 'vitest'
import { formatAuthors } from '../src/format'

describe('formatAuthors', () => {
  it('handles no authors', () => {
    expect(formatAuthors([])).toBe('Unknown authors')
  })

  it('shows a typical author list in full, in order', () => {
    expect(formatAuthors(['Ada Lovelace'])).toBe('Ada Lovelace')
    expect(formatAuthors(['Ada Lovelace', 'Alan Turing', 'Grace Hopper'])).toBe('Ada Lovelace, Alan Turing, Grace Hopper')
  })

  it('abbreviates very long author lists with et al., keeping the first author', () => {
    const authors = Array.from({ length: 40 }, (_, i) => `Author ${i + 1}`)
    const shown = formatAuthors(authors)
    expect(shown.startsWith('Author 1, ')).toBe(true)
    expect(shown.endsWith(', et al.')).toBe(true)
    expect(shown).not.toContain('Author 40')
  })
})
