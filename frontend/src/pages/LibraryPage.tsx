import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { Paper } from '../api'
import PaperBadges from '../components/PaperBadges'
import PaperCard from '../components/PaperCard'

const POLL_MS = 2000

export default function LibraryPage() {
  const [papers, setPapers] = useState<Paper[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.listPapers().then(setPapers).catch((e: Error) => setError(e.message))
  }, [])

  // While any paper's PDF is still being downloaded/extracted, refresh periodically
  const anyPending = papers?.some((p) => p.text_status === 'pending') ?? false
  useEffect(() => {
    if (!anyPending) return
    const timer = setInterval(() => {
      api.listPapers().then(setPapers).catch(() => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [anyPending])

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-uploading the same file
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const paper = await api.uploadPaper(file)
      navigate(`/papers/${paper.id}`)
    } catch (err) {
      setError(`Upload failed: ${(err as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  async function onDelete(paper: Paper) {
    if (!confirm(`Remove "${paper.title}" from your library?`)) return
    try {
      await api.deletePaper(paper.id)
      setPapers((ps) => ps?.filter((p) => p.id !== paper.id) ?? null)
    } catch (err) {
      setError(`Could not remove the paper: ${(err as Error).message}`)
    }
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Library</h1>
          {papers && papers.length > 0 && (
            <p className="muted small">
              {papers.length} {papers.length === 1 ? 'paper' : 'papers'}
            </p>
          )}
        </div>
        <button onClick={() => fileInput.current?.click()} disabled={uploading}>
          {uploading ? 'Uploading…' : 'Upload PDF'}
        </button>
        <input ref={fileInput} type="file" accept="application/pdf,.pdf" onChange={onUpload} hidden />
      </div>

      <div className="notice">
        {uploading && <p className="muted">Extracting text and metadata from the PDF. This takes about 10 seconds…</p>}
        {error && <p className="error">{error}</p>}
        {papers === null && !error && <p className="muted">Loading…</p>}
        {papers?.length === 0 && (
          <p className="muted">
            Your library is empty. <Link to="/">Search arXiv</Link> and save a paper, or upload a PDF.
          </p>
        )}
      </div>

      <div className="entries">
        {papers?.map((p) => (
          <PaperCard
            key={p.id}
            title={p.title}
            authors={p.authors}
            year={p.year}
            abstract={p.abstract}
            url={p.url}
            detailHref={`/papers/${p.id}`}
            badges={<PaperBadges paper={p} />}
            actions={
              <>
                <Link className="button" to={`/papers/${p.id}`}>
                  Open paper
                </Link>
                <button className="danger" onClick={() => onDelete(p)}>
                  Remove
                </button>
              </>
            }
          />
        ))}
      </div>
    </section>
  )
}
