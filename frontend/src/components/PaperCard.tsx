import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { formatAuthors } from '../format'

interface PaperCardProps {
  title: string
  authors: string[]
  year: number | null
  abstract: string | null
  url?: string | null // external link (e.g. arXiv page)
  detailHref?: string // internal link to the paper detail page
  badges?: ReactNode
  actions?: ReactNode
}

const ABSTRACT_PREVIEW_CHARS = 320

export default function PaperCard({ title, authors, year, abstract, url, detailHref, badges, actions }: PaperCardProps) {
  const [expanded, setExpanded] = useState(false)
  const long = (abstract?.length ?? 0) > ABSTRACT_PREVIEW_CHARS
  const shownAbstract = long && !expanded ? `${abstract!.slice(0, ABSTRACT_PREVIEW_CHARS).trimEnd()}…` : abstract

  return (
    <article className="entry">
      <div className="entry-year">{year ?? <span className="small">n.d.</span>}</div>
      <div>
        <h3 className="entry-title">
          {detailHref ? (
            <Link to={detailHref}>{title}</Link>
          ) : url ? (
            <a href={url} target="_blank" rel="noreferrer">
              {title}
            </a>
          ) : (
            title
          )}
        </h3>
        <p className="authors">{formatAuthors(authors)}</p>
        {badges && <div className="statuses">{badges}</div>}
        {shownAbstract ? (
          <p className="abstract">
            {shownAbstract}
            {long && (
              <button className="link-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
                {expanded ? 'Show less' : 'Read full abstract'}
              </button>
            )}
          </p>
        ) : (
          <p className="abstract muted">No abstract available.</p>
        )}
        {(actions || url) && (
          <div className="actions">
            {actions}
            {url && (
              <a href={url} target="_blank" rel="noreferrer">
                arXiv page
              </a>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
