import { describe, expect, it } from 'vitest'
import { formatAuthors } from '../src/format'

describe('formatAuthors', () => {
  it('handles no authors', () => {
    expect(formatAuthors([])).toBe('Unknown authors')
  })

  it('shows a single author', () => {
    expect(formatAuthors(['Ada Lovelace'])).toBe('Ada Lovelace')
  })

  it('shows up to six authors in full', () => {
    const six = ['A', 'B', 'C', 'D', 'E', 'F']
    expect(formatAuthors(six)).toBe('A, B, C, D, E, F')
  })

  it('abbreviates more than six authors with et al.', () => {
    expect(formatAuthors(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])).toBe('A, B, C, D, E, F, et al.')
  })
})
