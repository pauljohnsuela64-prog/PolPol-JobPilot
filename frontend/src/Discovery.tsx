import { JobAnalysis } from './AI';
import { useEffect, useState, type FormEvent } from 'react';
import { request } from './api';

export const providers = { greenhouse: 'Greenhouse', lever: 'Lever', 'lever-eu': 'Lever (EU)' };
export const matches = {
  match: 'Confirmed match',
  uncertain: 'Uncertain match',
  missing: 'Missing information',
  not_match: 'Outside preferences',
  unconfigured: 'No preferences set',
};
const criteria = { match: 'Matches', uncertain: 'Uncertain', missing: 'Missing', not_match: 'Outside preference' };
export const arrangements = { remote: 'Remote', hybrid: 'Hybrid', 'on-site': 'On-site' };
type Provider = keyof typeof providers;
type Match = keyof typeof matches;
type Source = { id: number; provider: Provider; board: string; company: string; enabled: boolean };
type Report = Pick<Source, 'company' | 'board'> & { source_id: number; seen: number; skipped: number; error: string | null };
export type Listing = {
  id: number;
  title: string;
  company: string;
  location: string | null;
  provider: Provider;
  url: string;
  arrangement: keyof typeof arrangements | null;
  salary: { currency: string; interval: string | null; minimum: number; maximum: number } | null;
  posted_at: string | null;
  discovered_at: string;
  last_seen_at: string;
  tracked_job_id: number | null;
  compatibility: { score: number; confidence: number; criteria: Record<string, keyof typeof criteria>;
    skills: { skill: string; confirmed: boolean; context: string; excerpt: string }[]; experience_evidence: string[] };
  match: Match;
  checks: Record<string, keyof typeof criteria>;
};
type Page = { jobs: Listing[]; has_more: boolean };
const pageSize = 50;

function salaryLabel(salary: Listing['salary']) {
  if (!salary) return 'Not provided';
  const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  const range = number.format(salary.minimum) + (salary.minimum === salary.maximum ? '' : ' - ' + number.format(salary.maximum));
  return salary.currency + ' ' + range + (salary.interval ? ' (' + salary.interval + ')' : ' (pay period not provided)');
}

function explain(field: string, state: keyof typeof criteria) {
  if (state === 'missing') return field + ' information is missing from the posting.';
  if (state === 'uncertain') return field + ' could only be partially verified.';
  if (field === 'Skills') return 'All recognized required and preferred skills appear in your confirmed skills.';
  if (state === 'not_match') return field + ' falls outside your saved preference.';
  return field + ' matches your saved preference.';
}

function DateLabel({ value, showTime = false }: { value: string | null; showTime?: boolean }) {
  return value ? <time dateTime={value}>{showTime ? new Date(value).toLocaleString() : new Date(value).toLocaleDateString()}</time> : 'Not provided';
}

export function Discovery() {
  const [sources, setSources] = useState<Source[]>([]);
  const [sourcesReady, setSourcesReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<Page>({ jobs: [], has_more: false });
  const [filters, setFilters] = useState({ q: '', provider: '', match: '', sort: '' });
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [action, setAction] = useState('');
  const [reports, setReports] = useState<Report[]>([]);
  const [problem, setProblem] = useState('');
  const [loadError, setLoadError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [success, setSuccess] = useState('');

  async function loadSources() {
    setSourceError('');
    try {
      setSources(await request<Source[]>('/api/discovery/sources'));
      setSourcesReady(true);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : 'Could not load career boards.');
    }
  }

  useEffect(() => { void loadSources(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: String(offset) });
    for (const [name, value] of Object.entries(filters)) {
      if (value) params.set(name, value);
    }
    setLoading(true);
    setLoadError('');
    void request<Page>('/api/discovery/jobs?' + params, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setPage(result); })
      .catch(error => { if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : 'Could not load jobs.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, offset, revision]);

  async function perform(label: string, operation: () => Promise<void>) {
    setAction(label);
    setProblem('');
    setSuccess('');
    try {
      await operation();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : 'Could not complete the request.');
    } finally {
      setAction('');
    }
  }

  function addSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    void perform('source', async () => {
      const source = await request<Source>('/api/discovery/sources', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      });
      setSources(current => [...current.filter(item => item.id !== source.id), source]);
      form.reset();
      setSuccess('Career board saved.');
    });
  }

  function toggleSource(source: Source) {
    void perform('source', async () => {
      const next = await request<Source>('/api/discovery/sources/' + source.id, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !source.enabled }),
      });
      setSources(current => current.map(item => item.id === next.id ? next : item));
      setSuccess(next.enabled ? 'Career board enabled.' : 'Career board paused. Its discovered jobs are still available.');
    });
  }

  function refresh() {
    void perform('refresh', async () => {
      setReports([]);
      setReports(await request<Report[]>('/api/discovery/refresh', { method: 'POST' }));
      setOffset(0);
      setRevision(current => current + 1);
    });
  }

  function save(job: Listing) {
    void perform('save', async () => {
      const result = await request<{ job_id: number }>('/api/discovery/jobs/' + job.id + '/save', { method: 'POST' });
      setPage(current => ({ ...current, jobs: current.jobs.map(item => (
        item.id === job.id ? { ...item, tracked_job_id: result.job_id } : item
      )) }));
      setSuccess(job.title + ' saved to Dashboard.');
    });
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setOffset(0);
    setFilters({ q: String(fields.get('q')).trim(), provider: String(fields.get('provider')), match: String(fields.get('match')), sort: String(fields.get('sort')) });
  }

  return (
    <main className="wrap discovery">
      <section className="intro">
        <div>
          <h1>Job discovery</h1>
          <p>Find openings on employer career boards and compare them with your saved preferences.</p>
          <a href="#shortlist">Manage automated discovery and review your shortlist</a>
        </div>
        <button className="primary" onClick={refresh} disabled={!!action || !sources.some(source => source.enabled)}>
          {action === 'refresh' ? 'Refreshing jobs...' : 'Refresh jobs'}
        </button>
      </section>
      {problem && <p className="error" role="alert">{problem}</p>}
      {success && <p className="success" role="status">{success}</p>}
      {action === 'refresh' && <p className="refresh-notice" role="status">Checking enabled boards. Larger boards may take longer.</p>}
      {reports.length > 0 && <ul className="feed-report" aria-label="Refresh results" aria-live="polite">
        {reports.map(report => <li key={report.source_id}>
          <strong>{report.company} ({report.board})</strong>
          <span>{report.error ?? (report.seen + ' seen' + (report.skipped ? ', ' + report.skipped + ' invalid skipped' : ''))}</span>
        </li>)}
      </ul>}
      <section className="panel form" aria-labelledby="boards-heading">
        <h2 id="boards-heading">Career boards</h2>
        <p>Choose a provider and enter the employer’s board identifier, such as <code>stripe</code>. Use the identifier from the career board URL.</p>
        {sourceError && <p className="error" role="alert">{sourceError} <button onClick={loadSources}>Retry</button></p>}
        {sourcesReady && <>
          <form className="discovery-controls board-form" onSubmit={addSource}>
            <label className="profile-field">Provider
              <select name="provider">{Object.entries(providers).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            </label>
            <label className="profile-field">Company<input name="company" required maxLength={120} /></label>
            <label className="profile-field">Board identifier<input name="board" required maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9_\-]{0,79}" title="Start with a letter or number, then use letters, numbers, underscores or hyphens." /></label>
            <button type="submit" disabled={!!action}>Add career board</button>
          </form>
          {sources.length > 0 && <ul className="board-list">
            {sources.map(source => <li key={source.id}>
              <div><strong>{source.company}</strong><p>{providers[source.provider]} · {source.board} · {source.enabled ? 'Enabled' : 'Paused'}</p></div>
              <button onClick={() => toggleSource(source)} disabled={!!action} aria-label={(source.enabled ? 'Pause ' : 'Enable ') + source.company}>
                {source.enabled ? 'Pause' : 'Enable'}
              </button>
            </li>)}
          </ul>}
        </>}
      </section>
      <section aria-labelledby="discoveries-heading">
        <h2 id="discoveries-heading">Discovered jobs</h2>
        <p>Match labels compare the information provided by each board with your <a href="#profile">saved preferences</a>.</p>
        <form className="discovery-controls listing-search" onSubmit={search}>
          <label className="profile-field">Search jobs<input type="search" name="q" maxLength={120} placeholder="Title, company or location" /></label>
          <label className="profile-field">Source platform
            <select name="provider"><option value="">All platforms</option>{Object.entries(providers).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          </label>
          <label className="profile-field">Preference match
            <select name="match"><option value="">All results</option>{Object.entries(matches).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          </label>
          <label className="profile-field">Sort jobs<select name="sort">
            <option value="best">Best matches first</option><option value="score">Highest compatibility score</option>
            <option value="newest">Newest discovered</option><option value="recent">Recently seen</option>
          </select></label>
          <button>Apply filters</button>
        </form>
        {loading && <p className="empty" role="status">Loading discovered jobs...</p>}
        {loadError && <div className="error" role="alert">{loadError} <button onClick={() => setRevision(current => current + 1)}>Retry</button></div>}
        {!loading && !loadError && page.jobs.length === 0 && <div className="panel empty">
          <strong>{(filters.q || filters.provider || filters.match) ? 'No jobs match these filters' : 'No jobs discovered yet'}</strong>
          <p>{(filters.q || filters.provider || filters.match) ? 'Adjust your filters and try again.' : 'Add an employer career board and select Refresh jobs.'}</p>
        </div>}
        {!loading && !loadError && <div className="discovery-cards">
          {page.jobs.map(job => <article className="panel discovery-card" key={job.id}>
            <div className="discovery-card-head">
              <div><h3>{job.title}</h3><p>{job.company} · {providers[job.provider]}</p></div>
              <span className={'match-label ' + job.match}>{matches[job.match]}</span>
            </div>
            <p><strong>Compatibility {job.compatibility.score}/100</strong> ? Evidence confidence {job.compatibility.confidence}%</p>
            <details><summary>Compatibility details</summary>
              <ul>{Object.entries(job.compatibility.criteria).map(([key, status]) => <li key={key}>{explain(key, status)}</li>)}</ul>
              {job.compatibility.skills.map(item => <p key={item.skill}><strong>{item.skill}</strong> ? {item.context} ? {item.confirmed ? 'In confirmed skills' : 'Unverified in profile'}<br /><q>{item.excerpt}</q></p>)}
              {job.compatibility.experience_evidence.map(line => <p key={line}>Experience mentioned: <q>{line}</q></p>)}
              {!job.compatibility.skills.length && <p>No recognized skill evidence.</p>}
            </details>
            <dl className="posting-details">
              <div><dt>Location</dt><dd>{job.location ?? 'Not provided'}</dd></div>
              <div><dt>Work arrangement</dt><dd>{job.arrangement ? arrangements[job.arrangement] : 'Not provided'}</dd></div>
              <div><dt>Salary</dt><dd>{salaryLabel(job.salary)}</dd></div>
              <div><dt>Posted</dt><dd><DateLabel value={job.posted_at} /></dd></div>
              <div><dt>Discovered</dt><dd><DateLabel value={job.discovered_at} /></dd></div>
              <div><dt>Last seen</dt><dd><DateLabel value={job.last_seen_at} showTime /></dd></div>
            </dl>
            {Object.keys(job.checks).length > 0 && <details>
              <summary>Preference details</summary>
              <ul>{Object.entries(job.checks).map(([field, status]) => <li key={field}>{field} · {criteria[status]}</li>)}</ul>
            </details>}
            <JobAnalysis job={job.id} />
            <div className="posting-actions">
              <a href={'#builder/' + job.id}>Tailor Resume</a>
              <a href={'#preparation/' + job.id}>Prepare application</a>
              <a href={'#queue/listing-' + job.id}>View in application queue</a>
              <a href={job.url} target="_blank" rel="noopener noreferrer">Original posting <span aria-hidden="true">↗</span></a>
              <button className="primary" onClick={() => save(job)} disabled={!!action || job.tracked_job_id !== null}>
                {job.tracked_job_id !== null ? 'Saved to Dashboard' : 'Save to Dashboard'}
              </button>
            </div>
          </article>)}
        </div>}
        {!loading && !loadError && (offset > 0 || page.has_more) && <nav className="posting-actions" aria-label="Discovery pages">
          <button disabled={offset === 0} onClick={() => setOffset(current => Math.max(0, current - pageSize))}>Previous page</button>
          <button disabled={!page.has_more} onClick={() => setOffset(current => current + pageSize)}>Next page</button>
        </nav>}
      </section>
    </main>
  );
}
