import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import type { SearchResult } from '../api'
import PaperCard from '../components/PaperCard'

export default function SearchPage() {
  // Keep the query in the URL so results survive refreshes and back-navigation
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const [input, setInput] = useState(query)
  // Results (or the search error) are tagged with the query they belong to
  const [search, setSearch] = useState<{ q: string; results?: SearchResult[]; error?: string }>({ q: '' })
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState<Set<string>>(new Set())
  const [attempt, setAttempt] = useState(0) // bumped to re-run the same query
  const loading = query !== '' && search.q !== query
  const results = loading ? null : (search.results ?? null)
  const error = (loading ? null : search.error) ?? saveError

  useEffect(() => {
    if (!query) return
    let cancelled = false
    api
      .search(query)
      .then((r) => !cancelled && setSearch({ q: query, results: r }))
      .catch((e: Error) => !cancelled && setSearch({ q: query, error: e.message }))
    return () => {
      cancelled = true
    }
  }, [query, attempt])

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = input.trim()
    if (!q) return
    setSaveError(null)
    if (q === query) {
      // Same query (e.g. retrying after an error): the URL won't change, so re-run explicitly
      setSearch({ q: '' })
      setAttempt((a) => a + 1)
    } else {
      setParams({ q })
    }
  }

  async function save(result: SearchResult) {
    setSaving((s) => new Set(s).add(result.external_id))
    try {
      const paper = await api.savePaper(result)
      setSearch((st) => ({
        ...st,
        results: st.results?.map((r) => (r.external_id === result.external_id ? { ...r, saved_id: paper.id } : r)),
      }))
    } catch (e) {
      setSaveError(`Could not save "${result.title}": ${(e as Error).message}`)
    } finally {
      setSaving((s) => {
        const next = new Set(s)
        next.delete(result.external_id)
        return next
      })
    }
  }

  return (
    <section>
      <div className="search-hero">
        <h1>Search arXiv for papers</h1>
        <form className="search-form" onSubmit={onSubmit} role="search">
          <input
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="diffusion models, protein folding…"
            aria-label="Keywords or a research topic"
            autoFocus
          />
          <button type="submit" disabled={loading || !input.trim()}>
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>
        <p className="hint muted small">Enter keywords or a topic. Save a result to summarize it and ask questions.</p>
      </div>

      <div className="notice">
        {error && <p className="error">{error}</p>}
        {loading && <p className="muted">Searching arXiv…</p>}
        {!loading && results?.length === 0 && <p className="muted">No papers found. Try different keywords.</p>}
      </div>

      <div className="entries">
      {!loading &&
        results?.map((r) => (
          <PaperCard
            key={r.external_id}
            title={r.title}
            authors={r.authors}
            year={r.year}
            abstract={r.abstract}
            url={r.url}
            actions={
              r.saved_id ? (
                <>
                  <span className="saved">Saved</span>
                  <Link className="button secondary" to={`/papers/${r.saved_id}`}>
                    Open paper
                  </Link>
                </>
              ) : (
                <button onClick={() => save(r)} disabled={saving.has(r.external_id)}>
                  {saving.has(r.external_id) ? 'Saving…' : 'Save to library'}
                </button>
              )
            }
          />
        ))}
      </div>
    </section>
  )
}
