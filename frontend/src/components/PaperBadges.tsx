import type { Paper } from '../api'

export default function PaperBadges({ paper }: { paper: Paper }) {
  return (
    <>
      <span className="status">{paper.source === 'upload' ? 'Uploaded PDF' : 'From arXiv'}</span>
      {paper.text_status === 'pending' && <span className="status pending">Loading full text…</span>}
      {paper.text_status === 'failed' && <span className="status failed">Full text unavailable</span>}
      {paper.summary && <span className="status ok">Summarized</span>}
    </>
  )
}
