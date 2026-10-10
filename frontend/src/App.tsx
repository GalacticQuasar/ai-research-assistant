import { useEffect, useState } from 'react'

function App() {
  const [status, setStatus] = useState('checking...')

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setStatus(d.status))
      .catch(() => setStatus('backend unreachable'))
  }, [])

  return (
    <main>
      <h1>AI Research Assistant</h1>
      <p>Backend status: {status}</p>
    </main>
  )
}

export default App
