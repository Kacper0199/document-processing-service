import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'

const apiUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

type JobState = 'queued' | 'running' | 'succeeded' | 'failed'
type Entity = { name: string; type: string }
type Commitment = { action: string; owner: string | null; deadline: string | null; evidence: string }
type Analysis = {
  topic: string
  document_type: string
  language: string
  summary: string
  keywords: string[]
  actionability: string
  entities: Entity[]
  commitments: Commitment[]
}
type AnalysisRun = {
  analysis: Analysis
  route: string
  model: string
  prompt_version: string
  prompt_tokens: number
  completion_tokens: number
  duration_ms: number
}
type Extraction = { sha256: string; characters: number; preview: string }
type JobStatus = {
  id: string
  state: JobState
  operation: string
  attempt_count: number
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  input_lineage: { document_url: string; sha256: string; content_type: string; size_bytes: number } | null
  result: { processor: string; processor_version: string; artifacts: Record<string, string>; metadata: { analysis?: AnalysisRun; extraction?: Extraction } } | null
  failure: { code: string; message: string; retryable: boolean } | null
}
type JobAccepted = { job_id: string; state: JobState; reused: boolean }

const stateLabels: Record<JobState, string> = {
  queued: 'Queued',
  running: 'Processing',
  succeeded: 'Complete',
  failed: 'Needs attention',
}

function humanize(value: string) {
  return value.replaceAll('_', ' ')
}

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

function formatDuration(value: number | undefined) {
  return typeof value === 'number' ? `${Math.round(value)} ms` : '—'
}

function isJobStatus(value: unknown): value is JobStatus {
  return typeof value === 'object' && value !== null && 'id' in value && 'state' in value
}

function App() {
  const [url, setUrl] = useState('')
  const [key, setKey] = useState<string>(() => crypto.randomUUID())
  const [jobs, setJobs] = useState<JobStatus[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('Ready to accept a document.')
  const [query, setQuery] = useState('')
  const [topic, setTopic] = useState('all')
  const [route, setRoute] = useState('all')
  const [state, setState] = useState('all')

  const loadJobs = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    try {
      const response = await fetch(`${apiUrl}/jobs`)
      if (!response.ok) throw new Error(`The desk could not load jobs (${response.status}).`)
      const payload: unknown = await response.json()
      if (!Array.isArray(payload) || !payload.every(isJobStatus)) throw new Error('The desk received an invalid jobs response.')
      setJobs(payload)
      setSelectedId(current => current ?? payload[0]?.id ?? null)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The desk could not load jobs.')
    } finally {
      if (initial) setLoading(false)
    }
  }, [])

  useEffect(() => { void loadJobs(true) }, [loadJobs])

  useEffect(() => {
    if (!jobs.some(job => job.state === 'queued' || job.state === 'running')) return
    const timer = window.setInterval(() => { void loadJobs() }, 3000)
    return () => window.clearInterval(timer)
  }, [jobs, loadJobs])

  const filterOptions = useMemo(() => ({
    topics: [...new Set(jobs.flatMap(job => job.result?.metadata.analysis?.analysis.topic ?? []))].sort(),
    routes: [...new Set(jobs.flatMap(job => job.result?.metadata.analysis?.route ?? []))].sort(),
  }), [jobs])

  const filteredJobs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return jobs.filter(job => {
      const analysis = job.result?.metadata.analysis
      const searchText = [job.id, job.input_lineage?.document_url, analysis?.analysis.summary, analysis?.analysis.topic, analysis?.route]
        .filter(Boolean).join(' ').toLowerCase()
      return (state === 'all' || job.state === state)
        && (topic === 'all' || analysis?.analysis.topic === topic)
        && (route === 'all' || analysis?.route === route)
        && (!normalizedQuery || searchText.includes(normalizedQuery))
    })
  }, [jobs, query, route, state, topic])

  const groups = useMemo(() => {
    const grouped = new Map<string, JobStatus[]>()
    for (const job of filteredJobs) {
      const group = job.result?.metadata.analysis?.route ?? 'in intake'
      grouped.set(group, [...(grouped.get(group) ?? []), job])
    }
    return [...grouped.entries()]
  }, [filteredJobs])

  const selected = jobs.find(job => job.id === selectedId) ?? null
  const selectedAnalysis = selected?.result?.metadata.analysis
  const selectedExtraction = selected?.result?.metadata.extraction

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setMessage('Submitting document to the intake queue…')
    try {
      const response = await fetch(`${apiUrl}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify({ document_url: url, operation: 'extract_markdown' }),
      })
      if (!response.ok) throw new Error(`Request failed (${response.status}).`)
      const accepted = await response.json() as JobAccepted
      setSelectedId(accepted.job_id)
      setMessage(accepted.reused ? 'Existing intake selected.' : 'Document accepted by the queue.')
      setKey(crypto.randomUUID())
      await loadJobs()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'The document could not be submitted.')
    } finally {
      setSubmitting(false)
    }
  }

  return <main>
    <header>
      <div><p className="eyebrow">DOCUMENT INTELLIGENCE</p><h1>Intake desk</h1></div>
      <a href={`${apiUrl}/metrics`} target="_blank" rel="noreferrer">Service metrics</a>
    </header>

    <section className="intro" aria-labelledby="desk-title">
      <div><p className="eyebrow">CORPUS REVIEW</p><h2 id="desk-title">Turn incoming documents into decisions.</h2><p>Review extraction evidence, routed analysis, and commitments from one operational desk.</p></div>
      <div className="service"><span className="dot" />Intake service online<br /><small>{jobs.length} retained jobs · newest first</small></div>
    </section>

    <section className="submission" aria-labelledby="submit-title">
      <div><p className="eyebrow">NEW INTAKE</p><h3 id="submit-title">Queue a document</h3></div>
      <form onSubmit={submit}>
        <label htmlFor="url">Document URL</label>
        <input id="url" type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.org/report.pdf" />
        <label htmlFor="key">Idempotency key</label>
        <input id="key" required value={key} onChange={event => setKey(event.target.value)} />
        <button disabled={submitting}>{submitting ? 'Submitting…' : 'Submit extraction'}</button>
      </form>
      <p className="message" role="status">{message}</p>
    </section>

    <section className="toolbar" aria-label="Filter intake corpus">
      <div><label htmlFor="search">Search</label><input id="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Summary, source, or ID" /></div>
      <div><label htmlFor="topic">Topic</label><select id="topic" value={topic} onChange={event => setTopic(event.target.value)}><option value="all">All topics</option>{filterOptions.topics.map(value => <option key={value} value={value}>{humanize(value)}</option>)}</select></div>
      <div><label htmlFor="route">Route</label><select id="route" value={route} onChange={event => setRoute(event.target.value)}><option value="all">All routes</option>{filterOptions.routes.map(value => <option key={value} value={value}>{humanize(value)}</option>)}</select></div>
      <div><label htmlFor="state">State</label><select id="state" value={state} onChange={event => setState(event.target.value)}><option value="all">All states</option>{Object.entries(stateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
      <button className="quiet" type="button" onClick={() => { setQuery(''); setTopic('all'); setRoute('all'); setState('all') }}>Clear filters</button>
    </section>

    {error && <section className="notice error" role="alert"><p>{error}</p><button type="button" onClick={() => { void loadJobs(true) }}>Try again</button></section>}
    {loading ? <section className="notice" aria-live="polite">Loading intake corpus…</section> : !error && jobs.length === 0 ? <section className="notice">No documents have been submitted. Queue the first intake above.</section> : !error && filteredJobs.length === 0 ? <section className="notice">No documents match these filters.</section> : !error && <section className="desk">
      <div className="corpus" aria-label="Intake corpus"><p className="eyebrow">{filteredJobs.length} MATCHING DOCUMENTS</p>{groups.map(([group, groupJobs]) => <section className="route-group" key={group}><h3>{humanize(group)}</h3><div className="job-list">{groupJobs.map(job => <button className={`job-card ${selectedId === job.id ? 'selected' : ''}`} key={job.id} type="button" onClick={() => setSelectedId(job.id)} aria-pressed={selectedId === job.id}><span className={`state ${job.state}`}>{stateLabels[job.state]}</span><strong>{humanize(job.result?.metadata.analysis?.analysis.topic ?? 'unclassified')}</strong><span>{job.result?.metadata.analysis?.analysis.summary ?? job.input_lineage?.document_url ?? 'Awaiting document details'}</span><small>{formatDate(job.created_at)}</small></button>)}</div></section>)}</div>
      <aside className="detail" aria-live="polite">{selected ? <>
        <div className="detail-heading"><div><p className="eyebrow">DOCUMENT BRIEF</p><h3>{humanize(selectedAnalysis?.analysis.topic ?? 'Awaiting analysis')}</h3></div><span className={`state ${selected.state}`}>{stateLabels[selected.state]}</span></div>
        <p className="summary">{selectedAnalysis?.analysis.summary ?? selected.failure?.message ?? 'Extraction and analysis will appear here when this job completes.'}</p>
        <dl className="facts"><div><dt>Actionability</dt><dd>{humanize(selectedAnalysis?.analysis.actionability ?? 'pending')}</dd></div><div><dt>Route</dt><dd>{humanize(selectedAnalysis?.route ?? 'in intake')}</dd></div><div><dt>Document type</dt><dd>{humanize(selectedAnalysis?.analysis.document_type ?? selected.operation)}</dd></div><div><dt>Attempts</dt><dd>{selected.attempt_count} / 3</dd></div></dl>
        <DetailSection title="Keywords"><div className="tags">{selectedAnalysis?.analysis.keywords.map(keyword => <span key={keyword}>{keyword}</span>) ?? <em>Not available</em>}</div></DetailSection>
        <DetailSection title="Entities">{selectedAnalysis?.analysis.entities.length ? <ul>{selectedAnalysis.analysis.entities.map(entity => <li key={`${entity.type}-${entity.name}`}><strong>{entity.name}</strong> <span>{humanize(entity.type)}</span></li>)}</ul> : <p className="muted">No entities extracted.</p>}</DetailSection>
        <DetailSection title="Commitments">{selectedAnalysis?.analysis.commitments.length ? <ul>{selectedAnalysis.analysis.commitments.map(commitment => <li key={`${commitment.action}-${commitment.evidence}`}><strong>{commitment.action}</strong><span>{[commitment.owner, commitment.deadline].filter(Boolean).join(' · ') || 'Owner and date not identified'}</span><small>{commitment.evidence}</small></li>)}</ul> : <p className="muted">No commitments identified.</p>}</DetailSection>
        <DetailSection title="Extraction preview"><pre>{selectedExtraction?.preview ?? 'No extracted content is available yet.'}</pre></DetailSection>
        <DetailSection title="Input lineage"><dl className="metadata"><div><dt>Source</dt><dd>{selected.input_lineage?.document_url ?? 'Pending retrieval'}</dd></div><div><dt>Content</dt><dd>{selected.input_lineage ? `${selected.input_lineage.content_type} · ${selected.input_lineage.size_bytes.toLocaleString()} bytes` : '—'}</dd></div><div><dt>SHA-256</dt><dd>{selected.input_lineage?.sha256 ?? '—'}</dd></div></dl></DetailSection>
        <DetailSection title="Model and timing"><dl className="metadata"><div><dt>Model</dt><dd>{selectedAnalysis?.model ?? '—'}</dd></div><div><dt>Prompt</dt><dd>{selectedAnalysis?.prompt_version ?? '—'}</dd></div><div><dt>Tokens</dt><dd>{selectedAnalysis ? `${selectedAnalysis.prompt_tokens} prompt · ${selectedAnalysis.completion_tokens} completion` : '—'}</dd></div><div><dt>Analysis</dt><dd>{formatDuration(selectedAnalysis?.duration_ms)}</dd></div><div><dt>Finished</dt><dd>{formatDate(selected.finished_at)}</dd></div></dl></DetailSection>
      </> : <p className="muted">Select a document to inspect its intake record.</p>}</aside>
    </section>}
  </main>
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="detail-section"><h4>{title}</h4>{children}</section>
}

export default App
