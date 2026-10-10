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
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.getChat(paperId).then(setMessages).catch((e: Error) => setError(e.message))
  }, [paperId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
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
    <section className="panel">
      <div className="section-header">
        <h3>Ask about this paper</h3>
        {messages.length > 0 && (
          <button className="secondary" onClick={clear} disabled={pendingQuestion !== null}>
            Clear chat
          </button>
        )}
      </div>

      {!ready && <p className="muted">Questions can be asked once the full text has loaded.</p>}

      <div className="chat-log">
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            {m.role === 'assistant' ? <Markdown>{m.content}</Markdown> : m.content}
          </div>
        ))}
        {pendingQuestion && (
          <>
            <div className="message user">{pendingQuestion}</div>
            <div className="message assistant muted">Thinking…</div>
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <p className="error">{error}</p>}

      {ready && messages.length === 0 && !pendingQuestion && (
        <div className="chips">
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
          placeholder={ready ? 'Ask a question about this paper…' : 'Waiting for full text…'}
          disabled={disabled}
          maxLength={2000}
        />
        <button type="submit" disabled={disabled || !input.trim()}>
          Ask
        </button>
      </form>
    </section>
  )
}
