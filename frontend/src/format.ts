const MAX_AUTHORS_SHOWN = 6

export function formatAuthors(authors: string[]) {
  if (authors.length === 0) return 'Unknown authors'
  if (authors.length <= MAX_AUTHORS_SHOWN) return authors.join(', ')
  return `${authors.slice(0, MAX_AUTHORS_SHOWN).join(', ')}, et al.`
}
