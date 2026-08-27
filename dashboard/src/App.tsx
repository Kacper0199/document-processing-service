import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

const apiUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

type Job = { job_id: string; state: string; reused?: boolean; attempt_count?: number }

function App() {
  const [url, setUrl] = useState('')
  const [key, setKey] = useState<string>(crypto.randomUUID())
  const [job, setJob] = useState<Job | null>(null)
  const [message, setMessage] = useState('Ready to submit a document.')

  useEffect(() => {
    if (!job || ['succeeded', 'failed'].includes(job.state)) return
    const timer = window.setInterval(async () => {
      const response = await fetch(`${apiUrl}/jobs/${job.job_id}`)
      if (response.ok) setJob(await response.json())
    }, 1200)
    return () => window.clearInterval(timer)
  }, [job])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setMessage('Submitting job...')
    const response = await fetch(`${apiUrl}/jobs`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
      body: JSON.stringify({ document_url: url, operation: 'extract_markdown' }),
    })
    if (!response.ok) return setMessage(`Request failed: ${response.status}`)
    const accepted = await response.json()
    setJob(accepted)
    setMessage(accepted.reused ? 'Existing job reused.' : 'Job accepted by the queue.')
  }

  return <main>
    <header><div><p className="eyebrow">DOCUMENT OPERATIONS</p><h1>Processing console</h1></div><a href={`${apiUrl}/metrics`} target="_blank">Metrics</a></header>
    <section className="intro"><div><p className="eyebrow">ASYNC MINERU PIPELINE</p><h2>Submit a document. Track the work.</h2><p>The service retrieves the document, verifies its source and processes it outside the request path.</p></div><div className="service"><span className="dot" />Worker ready<br /><small>Retry limit: 3 attempts</small></div></section>
    <section className="grid"><form onSubmit={submit}><label htmlFor="url">Document URL</label><input id="url" type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.org/report.pdf" /><label htmlFor="key">Idempotency key</label><input id="key" required value={key} onChange={event => setKey(event.target.value)} /><button>Submit extraction</button><p className="message">{message}</p></form>
      <aside><p className="eyebrow">JOB STATUS</p>{job ? <><strong>{job.state}</strong><p className="mono">{job.job_id}</p><dl><div><dt>Attempts</dt><dd>{job.attempt_count ?? 0} / 3</dd></div><div><dt>Request</dt><dd>{job.reused ? 'Reused' : 'New'}</dd></div></dl></> : <p className="empty">No job selected yet.</p>}</aside></section>
    <section className="flow"><span>Request</span><b>→</b><span>Queue</span><b>→</b><span>MinerU</span><b>→</b><span>Result</span></section>
  </main>
}

export default App
