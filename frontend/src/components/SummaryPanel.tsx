import { useState } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import type { Paper } from '../api'

interface SummaryPanelProps {
  paper: Paper
  onUpdated: (paper: Paper) => void
}

export default function SummaryPanel({ paper, onUpdated }: SummaryPanelProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ready = paper.text_status === 'ready'

  async function generate(regenerate: boolean) {
    setLoading(true)
    setError(null)
    try {
      onUpdated(await api.summarize(paper.id, regenerate))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="panel">
      <div className="section-header">
        <h3>AI summary</h3>
        {paper.summary ? (
          <button className="secondary" onClick={() => generate(true)} disabled={loading || !ready}>
            {loading ? 'Regenerating…' : 'Regenerate'}
          </button>
        ) : (
          <button onClick={() => generate(false)} disabled={loading || !ready}>
            {loading ? 'Generating…' : 'Generate summary'}
          </button>
        )}
      </div>
      {loading && <p className="muted">Reading the full paper and writing a summary. This can take up to 30 seconds…</p>}
      {error && <p className="error">{error}</p>}
      {paper.summary && !loading && (
        <div className="markdown">
          <Markdown>{paper.summary}</Markdown>
        </div>
      )}
      {!paper.summary && !loading && (
        <p className="muted">
          {ready
            ? 'Generate a summary based on the full text of the paper.'
            : 'The summary will be available once the full text has loaded.'}
        </p>
      )}
    </section>
  )
}
