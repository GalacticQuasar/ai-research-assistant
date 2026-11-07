import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import type { ChatMessage } from '../api'

const EXAMPLE_QUESTIONS = [
  'What problem does this paper address?',
  'What is the main idea of the proposed approach?',
  'What datasets are used?',
  'What are the major limitations?',
  'How does this method compare with the baselines?',
]

interface ChatPanelProps {
  paperId: number
  ready: boolean // full text available
}

export default function ChatPanel({ paperId, ready }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.getChat(paperId).then(setMessages).catch((e: Error) => setError(e.message))
  }, [paperId])

  useEffect(() => {
    // Scroll the log itself, not the page, so opening a paper doesn't jump to the chat
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
  }, [messages, pendingQuestion])

  async function ask(question: string) {
    question = question.trim()
    if (!question || pendingQuestion) return
    setPendingQuestion(question)
    setInput('')
    setError(null)
    try {
      const newMessages = await api.ask(paperId, question)
      setMessages((ms) => [...ms, ...newMessages])
    } catch (e) {
      setError((e as Error).message)
      setInput(question) // let the user retry without retyping
    } finally {
      setPendingQuestion(null)
    }
  }

  async function clear() {
    if (!confirm('Clear the conversation for this paper?')) return
    try {
      await api.clearChat(paperId)
      setMessages([])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    ask(input)
  }

  const disabled = !ready || pendingQuestion !== null

  return (
    <aside className="margin" aria-label="Questions about this paper">
      <div className="section-header">
        <h2>Ask about this paper</h2>
        {messages.length > 0 && (
          <button className="secondary" onClick={clear} disabled={pendingQuestion !== null}>
            Clear
          </button>
        )}
      </div>

      {!ready && <p className="muted">You can ask questions once the full text has loaded.</p>}

      <div className="chat-log" ref={logRef}>
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            {m.role === 'assistant' ? <Markdown>{m.content}</Markdown> : <mark>{m.content}</mark>}
          </div>
        ))}
        {pendingQuestion && (
          <>
            <div className="message user">
              <mark>{pendingQuestion}</mark>
            </div>
            <div className="message thinking" role="status">
              Reading the paper…
            </div>
          </>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {ready && messages.length === 0 && !pendingQuestion && (
        <div className="chips">
          <p className="muted small">Try one of these, or write your own:</p>
          {EXAMPLE_QUESTIONS.map((q) => (
            <button key={q} className="chip" onClick={() => ask(q)}>
              {q}
            </button>
          ))}
        </div>
      )}

      <form className="chat-form" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={ready ? 'Ask a question…' : 'Waiting for full text…'}
          aria-label="Your question"
          disabled={disabled}
          maxLength={2000}
        />
        <button type="submit" disabled={disabled || !input.trim()}>
          Ask
        </button>
      </form>
    </aside>
  )
}
