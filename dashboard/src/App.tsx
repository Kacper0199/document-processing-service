import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'

const apiUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'
const maxDocuments = 10

const presets = [
  { title: 'Our Solar System', topic: 'Space science', url: 'https://www.nasa.gov/wp-content/uploads/2015/01/Our_Solar_System_Lithograph.pdf' },
  { title: "Earthquakes and Earth's Plumbing", topic: 'Earth science', url: 'https://pubs.usgs.gov/fs/fs-096-03/pdf/fs-096-03.pdf' },
  { title: 'Protecting Your Small Business: Phishing', topic: 'Security', url: 'https://www.nist.gov/system/files/documents/2024/03/12/Phishing_SMB%20FactSheet_2024_Final.pdf' },
  { title: 'Green Remediation: Bioremediation', topic: 'Environment', url: 'https://www.epa.gov/system/files/documents/2022-04/gr_factsheet_bioremediation.pdf' },
  { title: 'Endocrine Disruptors and Your Health', topic: 'Health', url: 'https://www.niehs.nih.gov/sites/default/files/health/materials/endocrine_disruptors_508.pdf' },
  { title: 'Hurricane Safety', topic: 'Weather safety', url: 'https://www.weather.gov/media/owlie/HurricaneSafety-OnePager-07-03-18.pdf' },
] as const

type JobState = 'queued' | 'running' | 'succeeded' | 'failed'
type Entity = { name: string; type: string }
type Commitment = { action: string; owner: string | null; deadline: string | null; evidence: string }
type Analysis = { topic: string; document_type: string; language: string; summary: string; keywords: string[]; actionability: string; entities: Entity[]; commitments: Commitment[] }
type AnalysisRun = { analysis: Analysis; route: string; model: string; prompt_version: string; prompt_tokens: number; completion_tokens: number; duration_ms: number }
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
type BatchInput = { urls: string[]; errors: string[] }

const stateLabels: Record<JobState, string> = { queued: 'Queued', running: 'Processing', succeeded: 'Complete', failed: 'Needs attention' }

function humanize(value: string) { return value.replaceAll('_', ' ') }
function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—' }
function formatDuration(value: number | undefined) { return typeof value === 'number' ? `${Math.round(value)} ms` : '—' }
function isJobStatus(value: unknown): value is JobStatus { return typeof value === 'object' && value !== null && 'id' in value && 'state' in value }

function parseBatch(value: string): BatchInput {
  const seen = new Set<string>()
  const urls: string[] = []
  const errors: string[] = []
  for (const [index, line] of value.split('\n').entries()) {
    const entry = line.trim()
    if (!entry) continue
    try {
      const parsed = new URL(entry)
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('unsupported protocol')
      if (!seen.has(parsed.href)) {
        seen.add(parsed.href)
        urls.push(parsed.href)
      }
    } catch {
      errors.push(`Line ${index + 1} is not a valid HTTP(S) URL.`)
    }
  }
  if (urls.length > maxDocuments) errors.push(`Choose no more than ${maxDocuments} unique documents.`)
  return { urls, errors }
}

async function idempotencyKeyFor(url: string) {
  if (!crypto.subtle) throw new Error('Your browser cannot create secure idempotency keys. Use a current browser and try again.')
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(url))
  return `dashboard-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`
}

async function apiError(response: Response) {
  let detail = ''
  try {
    const payload: unknown = await response.json()
    if (typeof payload === 'object' && payload !== null && 'detail' in payload && typeof payload.detail === 'string') detail = payload.detail.replaceAll('_', ' ')
  } catch { /* A status code still gives the operator an actionable error. */ }
  return `The API rejected this request (${response.status}${detail ? `: ${detail}` : ''}). Check the document URL and API service.`
}

function networkError(reason: unknown) {
  if (reason instanceof TypeError) return `Cannot reach the Papertrail API at ${apiUrl}. Check that it is running, VITE_API_URL is correct, and CORS allows this dashboard origin.`
  return reason instanceof Error ? reason.message : 'The Papertrail API could not be reached.'
}

function App() {
  const [urlText, setUrlText] = useState('')
  const [jobs, setJobs] = useState<JobStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const latestLoadRef = useRef(0)
  const isMountedRef = useRef(false)
  const [message, setMessage] = useState('Add one URL or a small batch to demonstrate the intake queue.')
  const [query, setQuery] = useState('')
  const [topic, setTopic] = useState('all')
  const [route, setRoute] = useState('all')
  const [state, setState] = useState('all')
  const batch = useMemo(() => parseBatch(urlText), [urlText])

  const loadJobs = useCallback(async (initial = false) => {
    const request = ++latestLoadRef.current
    if (initial && isMountedRef.current) setLoading(true)
    try {
      const response = await fetch(`${apiUrl}/jobs`)
      if (!response.ok) throw new Error(await apiError(response))
      const payload: unknown = await response.json()
      if (!Array.isArray(payload) || !payload.every(isJobStatus)) throw new Error('The API returned an invalid jobs list. Confirm this dashboard is connected to Papertrail.')
      if (isMountedRef.current && request === latestLoadRef.current) {
        setJobs(payload)
        setError(null)
      }
    } catch (reason) {
      if (isMountedRef.current && request === latestLoadRef.current) setError(networkError(reason))
    } finally {
      if (initial && isMountedRef.current && request === latestLoadRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    isMountedRef.current = true
    return () => { isMountedRef.current = false }
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
      const searchText = [job.id, job.input_lineage?.document_url, analysis?.analysis.summary, analysis?.analysis.topic, analysis?.route].filter(Boolean).join(' ').toLowerCase()
      return (state === 'all' || job.state === state) && (topic === 'all' || analysis?.analysis.topic === topic) && (route === 'all' || analysis?.route === route) && (!normalizedQuery || searchText.includes(normalizedQuery))
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
  const counts = useMemo(() => {
    const result: Record<JobState, number> = { queued: 0, running: 0, succeeded: 0, failed: 0 }
    for (const job of jobs) result[job.state] += 1
    return result
  }, [jobs])

  function insertPreset(url: string) {
    const current = parseBatch(urlText).urls
    if (current.includes(url)) {
      setMessage('That preset is already in this batch.')
      return
    }
    if (current.length >= maxDocuments) {
      setMessage(`A batch can contain at most ${maxDocuments} unique documents.`)
      return
    }
    setUrlText(current.length ? `${urlText.trim()}\n${url}` : url)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (batch.errors.length || batch.urls.length === 0) {
      setMessage(batch.errors[0] ?? 'Add at least one valid HTTP(S) URL before submitting.')
      return
    }
    setSubmitting(true)
    setMessage(`Submitting ${batch.urls.length} document${batch.urls.length === 1 ? '' : 's'} to the intake queue…`)
    const results = await Promise.all(batch.urls.map(async url => {
      try {
        const response = await fetch(`${apiUrl}/jobs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': await idempotencyKeyFor(url) },
          body: JSON.stringify({ document_url: url, operation: 'extract_markdown' }),
        })
        if (!response.ok) throw new Error(await apiError(response))
        return { accepted: await response.json() as JobAccepted, error: null }
      } catch (reason) {
        return { accepted: null, error: networkError(reason) }
      }
    }))
    const accepted = results.filter(result => result.accepted !== null)
    const failures = results.filter(result => result.error !== null)
    if (accepted.length) await loadJobs()
    setMessage(failures.length ? `${accepted.length} accepted; ${failures.length} failed. ${failures[0].error}` : `${accepted.length} document${accepted.length === 1 ? '' : 's'} accepted by the queue.${accepted.every(result => result.accepted?.reused) ? ' Existing intakes were selected.' : ''}`)
    setSubmitting(false)
  }

  return <main>
    <header><div><p className="eyebrow">DOCUMENT INTELLIGENCE</p><h1>Intake desk</h1></div><a href={`${apiUrl}/metrics`} target="_blank" rel="noreferrer">Service metrics</a></header>
    <section className="intro" aria-labelledby="desk-title">
      <div><p className="eyebrow">BATCH DOCUMENT INTAKE</p><h2 id="desk-title">Queue documents. Review the evidence.</h2><p>Submit up to ten source documents at once, then inspect extraction, analysis, and lineage from one operational desk.</p></div>
      <div className="service"><span className="dot" />Intake service online<br /><small>{jobs.length} retained jobs · newest first</small></div>
    </section>
    <section className="queue-summary" aria-label="Queue status summary">
      {Object.entries(stateLabels).map(([value, label]) => <div key={value}><strong>{counts[value as JobState]}</strong><span>{label}</span></div>)}
      <p>One worker processes one running document; the remaining documents demonstrate the queue.</p>
    </section>
    <section className="submission" aria-labelledby="submit-title">
      <div><p className="eyebrow">NEW BATCH</p><h3 id="submit-title">Queue documents</h3><p className="muted">One HTTP(S) URL per line. Up to 10 unique documents.</p></div>
      <form onSubmit={submit}>
        <label htmlFor="urls">Document URLs</label>
        <textarea id="urls" required value={urlText} onChange={event => setUrlText(event.target.value)} placeholder={'https://example.org/report.pdf\nhttps://example.org/brief.pdf'} rows={5} aria-describedby="batch-help" />
        <div className="form-footer"><small id="batch-help">{batch.urls.length} of {maxDocuments} unique valid URLs. Every document receives a deterministic idempotency key.</small><button type="submit" disabled={submitting || batch.urls.length === 0 || batch.errors.length > 0}>{submitting ? 'Submitting…' : `Submit ${batch.urls.length || ''} extraction${batch.urls.length === 1 ? '' : 's'}`}</button></div>
        {batch.errors.length > 0 && <p className="validation" role="alert">{batch.errors.join(' ')}</p>}
      </form>
      <div className="presets" aria-label="Curated preset documents"><p className="eyebrow">CURATED PRESETS</p><div>{presets.map(preset => {
        const selected = batch.urls.includes(preset.url)
        return <button key={preset.url} className={`preset${selected ? ' is-selected' : ''}`} type="button" aria-pressed={selected} onClick={() => insertPreset(preset.url)}><strong>{preset.title}</strong><small>{preset.topic}</small></button>
      })}</div></div>
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
    {loading ? <section className="notice" aria-live="polite">Loading intake corpus…</section> : !error && jobs.length === 0 ? <section className="notice">No documents have been submitted. Queue a document or preset batch above.</section> : !error && filteredJobs.length === 0 ? <section className="notice">No documents match these filters.</section> : !error && <section className="corpus" aria-label="Intake corpus"><p className="eyebrow">{filteredJobs.length} MATCHING DOCUMENTS</p>{groups.map(([group, groupJobs]) => <section className="route-group" key={group}><h3>{humanize(group)}</h3><div className="job-list">{groupJobs.map(job => <JobCard key={job.id} job={job} />)}</div></section>)}</section>}
  </main>
}

function JobCard({ job }: { job: JobStatus }) {
  const analysis = job.result?.metadata.analysis
  const topic = humanize(analysis?.analysis.topic ?? 'unclassified')
  const preview = analysis?.analysis.summary ?? job.input_lineage?.document_url ?? 'Awaiting document details'
  return <details className="job-card">
    <summary aria-label={`View ${stateLabels[job.state]} job details for ${topic}`}><span className={`state ${job.state}`}><span className="sr-only">Status: </span>{stateLabels[job.state]}</span><strong><span className="sr-only">Topic: </span>{topic}</strong><span><span className="sr-only">Document: </span>{preview}</span><small><span className="sr-only">Submitted: </span>{formatDate(job.created_at)} · attempt {job.attempt_count}</small></summary>
    <div className="job-detail"><JobDetails job={job} /></div>
  </details>
}

function JobDetails({ job }: { job: JobStatus }) {
  const analysis = job.result?.metadata.analysis
  const extraction = job.result?.metadata.extraction
  return <>
    <p className="summary">{analysis?.analysis.summary ?? job.failure?.message ?? 'Extraction and analysis will appear here when this job completes.'}</p>
    <dl className="facts"><div><dt>Actionability</dt><dd>{humanize(analysis?.analysis.actionability ?? 'pending')}</dd></div><div><dt>Route</dt><dd>{humanize(analysis?.route ?? 'in intake')}</dd></div><div><dt>Document type</dt><dd>{humanize(analysis?.analysis.document_type ?? job.operation)}</dd></div><div><dt>Attempts</dt><dd>{job.attempt_count} / 3</dd></div></dl>
    <DetailSection title="Keywords"><div className="tags">{analysis?.analysis.keywords.map(keyword => <span key={keyword}>{keyword}</span>) ?? <em>Not available</em>}</div></DetailSection>
    <DetailSection title="Entities">{analysis?.analysis.entities.length ? <ul>{analysis.analysis.entities.map(entity => <li key={`${entity.type}-${entity.name}`}><strong>{entity.name}</strong> <span>{humanize(entity.type)}</span></li>)}</ul> : <p className="muted">No entities extracted.</p>}</DetailSection>
    <DetailSection title="Commitments">{analysis?.analysis.commitments.length ? <ul>{analysis.analysis.commitments.map(commitment => <li key={`${commitment.action}-${commitment.evidence}`}><strong>{commitment.action}</strong><span>{[commitment.owner, commitment.deadline].filter(Boolean).join(' · ') || 'Owner and date not identified'}</span><small>{commitment.evidence}</small></li>)}</ul> : <p className="muted">No commitments identified.</p>}</DetailSection>
    <DetailSection title="Extraction preview"><pre>{extraction?.preview ?? 'No extracted content is available yet.'}</pre></DetailSection>
    <DetailSection title="Input lineage"><dl className="metadata"><div><dt>Source</dt><dd>{job.input_lineage?.document_url ?? 'Pending retrieval'}</dd></div><div><dt>Content</dt><dd>{job.input_lineage ? `${job.input_lineage.content_type} · ${job.input_lineage.size_bytes.toLocaleString()} bytes` : '—'}</dd></div><div><dt>SHA-256</dt><dd>{job.input_lineage?.sha256 ?? '—'}</dd></div></dl></DetailSection>
    <DetailSection title="Model and timing"><dl className="metadata"><div><dt>Model</dt><dd>{analysis?.model ?? '—'}</dd></div><div><dt>Prompt</dt><dd>{analysis?.prompt_version ?? '—'}</dd></div><div><dt>Tokens</dt><dd>{analysis ? `${analysis.prompt_tokens} prompt · ${analysis.completion_tokens} completion` : '—'}</dd></div><div><dt>Analysis</dt><dd>{formatDuration(analysis?.duration_ms)}</dd></div><div><dt>Finished</dt><dd>{formatDate(job.finished_at)}</dd></div></dl></DetailSection>
  </>
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) { return <section className="detail-section"><h4>{title}</h4>{children}</section> }

export default App
