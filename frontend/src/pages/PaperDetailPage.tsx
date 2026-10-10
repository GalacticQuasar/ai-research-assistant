import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import type { Paper } from '../api'
import ChatPanel from '../components/ChatPanel'
import PaperBadges from '../components/PaperBadges'
import SummaryPanel from '../components/SummaryPanel'

const POLL_MS = 2000

export default function PaperDetailPage() {
  const paperId = Number(useParams().id)
  const [paper, setPaper] = useState<Paper | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getPaper(paperId).then(setPaper).catch((e: Error) => setError(e.message))
  }, [paperId])

  // Poll until the background PDF download/extraction finishes
  const pending = paper?.text_status === 'pending'
  useEffect(() => {
    if (!pending) return
    const timer = setInterval(() => {
      api.getPaper(paperId).then(setPaper).catch(() => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [pending, paperId])

  async function retry() {
    try {
      setPaper(await api.retryFetchText(paperId))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (error && !paper) {
    return (
      <section>
        <p className="error">{error}</p>
        <Link to="/library">← Back to library</Link>
      </section>
    )
  }
  if (!paper) return <p className="muted">Loading…</p>

  const pdfHref = paper.has_pdf ? api.pdfUrl(paper.id) : paper.pdf_url

  return (
    <section>
      <Link to="/library" className="small">
        ← Back to library
      </Link>
      <h2 className="paper-title">{paper.title}</h2>
      <p className="meta">
        {paper.authors.length ? paper.authors.join(', ') : 'Unknown authors'}
        {paper.year && <> · {paper.year}</>}
      </p>
      <div className="badges">
        <PaperBadges paper={paper} />
      </div>
      <p className="links">
        {paper.url && (
          <a href={paper.url} target="_blank" rel="noreferrer">
            Paper page ↗
          </a>
        )}
        {pdfHref && (
          <a href={pdfHref} target="_blank" rel="noreferrer">
            View PDF ↗
          </a>
        )}
      </p>

      {paper.text_status === 'failed' && (
        <p className="warning">
          The full text of this paper couldn't be loaded, so it can't be summarized or questioned.{' '}
          {paper.pdf_url && (
            <button className="link-button" onClick={retry}>
              Try again
            </button>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}

      <section className="panel">
        <h3>Abstract</h3>
        <p className="abstract">{paper.abstract || <span className="muted">No abstract available.</span>}</p>
      </section>

      <SummaryPanel paper={paper} onUpdated={setPaper} />
      <ChatPanel paperId={paper.id} ready={paper.text_status === 'ready'} />
    </section>
  )
}
