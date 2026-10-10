import type { Paper } from '../api'

export default function PaperBadges({ paper }: { paper: Paper }) {
  return (
    <>
      <span className="badge">{paper.source === 'upload' ? 'Uploaded PDF' : 'arXiv'}</span>
      {paper.text_status === 'pending' && <span className="badge pending">Loading full text…</span>}
      {paper.text_status === 'failed' && <span className="badge failed">Full text unavailable</span>}
      {paper.summary && <span className="badge ok">Summarized</span>}
    </>
  )
}
