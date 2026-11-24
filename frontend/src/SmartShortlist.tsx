import { useEffect, useState, type FormEvent } from 'react';
import { request, send } from './api';
import { arrangements, matches, providers, type Listing } from './Discovery';

type Schedule = { enabled: boolean; interval_minutes: number; last_run_at: string | null; next_run_at: string | null; last_error: string | null };
type Settings = { min_score: number; min_confidence: number };
type Job = Omit<Listing, 'url'> & { url: string | null; reviewed: boolean; newly_discovered: boolean; board: string };
type Activity = { id: number; kind: 'new_match' | 'refresh_failed'; title: string | null; company: string | null; detail: string; created_at: string };
type Page = { jobs: Job[]; has_more: boolean };
const base = '/api/shortlist';
const schedulePath = '/api/discovery/schedule';
const json = (method: string, body: object): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
function time(value: string | null, fallback: string) {
  return value ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : fallback;
}
function numeric(form: HTMLFormElement, name: string) {
  return Number(new FormData(form).get(name));
}

export function SmartShortlist() {
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [page, setPage] = useState<Page | null>(null);
  const [query, setQuery] = useState('sort=best');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  useEffect(() => {
    const timer = window.setInterval(() => setRevision(n => n + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    Promise.all([request<Schedule>(schedulePath, options), request<Settings>(base + '/settings', options),
      request<Activity[]>(base + '/activity', options), request<Page>(base + '?' + query + '&offset=' + offset, options)])
      .then(([schedule, settings, activity, page]) => {
        if (controller.signal.aborted) return;
        setSchedule(schedule); setSettings(settings); setActivity(activity); setPage(page); setLoadError('');
      }).catch(e => { if (!controller.signal.aborted) setLoadError(e.message); });
    return () => controller.abort();
  }, [query, offset, revision]);

  async function update(path: string, method: string, body: object, success: string) {
    setBusy(true); setError(''); setMessage('');
    try {
      await send(path, json(method, body));
      setRevision(n => n + 1); setMessage(success);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save changes.'); }
    finally { setBusy(false); }
  }
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params = new URLSearchParams();
    new FormData(event.currentTarget).forEach((value, key) => { if (String(value).trim()) params.set(key, String(value).trim()); });
    setQuery(params.toString()); setOffset(0); setPage(null); setRevision(n => n + 1);
  }
  return <main className="wrap discovery smart-shortlist">
    <header className="intro"><div><h1>Smart shortlist</h1><p>Review openings that meet your compatibility and evidence thresholds.</p></div>
      <a href="#discovery">Manage career boards</a></header>
    {loadError && <div className="error" role="alert">{loadError} <button onClick={() => setRevision(n => n + 1)}>Retry</button></div>}
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    {!settings && !loadError && <p role="status">Loading shortlist...</p>}
    {schedule && settings && <>
      <div className="prep-setup">
        <section className="panel form"><div className="discovery-card-head"><h2>Automated discovery</h2>
          <span className={'queue-state ' + (schedule.enabled ? 'ready' : '')}>{schedule.enabled ? 'Active' : 'Paused'}</span></div>
          <p>Checks enabled career boards while the local API is running.</p>
          <dl className="posting-details"><div><dt>Last run</dt><dd>{time(schedule.last_run_at, 'No runs yet')}</dd></div>
            <div><dt>Next run</dt><dd>{time(schedule.next_run_at, 'Unscheduled')}</dd></div></dl>
          {schedule.last_error && <p className="queue-warning">Last refresh error: {schedule.last_error}</p>}
          <form className="shortlist-controls" onSubmit={e => { e.preventDefault(); void update(schedulePath, 'PATCH', { interval_minutes: numeric(e.currentTarget, 'interval') }, 'Refresh interval saved.'); }}>
            <label className="profile-field">Check every (minutes)<input key={schedule.interval_minutes} defaultValue={schedule.interval_minutes} name="interval" type="number" required min={15} max={10080} /></label>
            <button disabled={busy}>Save interval</button>
          </form>
          <button className="primary" disabled={busy} onClick={() => void update(schedulePath, 'PATCH', { enabled: !schedule.enabled }, schedule.enabled ? 'Automated discovery paused.' : 'Automated discovery started.')}>{schedule.enabled ? 'Pause discovery' : 'Start discovery'}</button>
        </section>
        <section className="panel form"><h2>Shortlist thresholds</h2><p>An opening must meet both minimums to appear here.</p>
          <form key={settings.min_score + '-' + settings.min_confidence} onSubmit={e => { e.preventDefault(); setOffset(0); void update(base + '/settings', 'PUT', { min_score: numeric(e.currentTarget, 'score'), min_confidence: numeric(e.currentTarget, 'confidence') }, 'Shortlist thresholds saved.'); }}>
            <div className="queue-fields"><label className="profile-field">Minimum score (0–100)<input type="number" required min={0} max={100} name="score" defaultValue={settings.min_score} /></label>
            <label className="profile-field">Minimum confidence (%)<input type="number" required min={0} max={100} name="confidence" defaultValue={settings.min_confidence} /></label></div>
            <button disabled={busy}>Save thresholds</button>
          </form>
        </section>
      </div>
      <section className="panel form"><h2>Shortlisted jobs</h2>
        <form className="shortlist-filters" onSubmit={filter}>
          <label className="profile-field">Search jobs<input name="q" type="search" maxLength={120} placeholder="Title, company or location" /></label>
          <label className="profile-field">Review status<select name="reviewed"><option value="">All jobs</option><option value="false">Unreviewed</option><option value="true">Reviewed</option></select></label>
          <label className="profile-field">Provider<select name="provider"><option value="">All providers</option>{Object.entries(providers).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="profile-field">Work arrangement<select name="arrangement"><option value="">All arrangements</option>{Object.entries(arrangements).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="profile-field">Preference match<select name="match"><option value="">All matches</option>{Object.entries(matches).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="profile-field">Sort by<select name="sort"><option value="best">Best match</option><option value="score">Highest score</option><option value="confidence">Highest confidence</option><option value="newest">Most recently discovered</option><option value="recent">Most recently seen</option></select></label>
          <button>Apply filters</button>
        </form>
        {!page && !loadError && <p role="status">Loading jobs...</p>}
        {page && !page.jobs.length && <p className="empty">No openings on this page. Check your filters, thresholds or enabled career boards.</p>}
        <div className="discovery-cards">{page?.jobs.map(job => <article className="panel discovery-card" key={job.id}>
          <div className="discovery-card-head"><div><h3>{job.title}</h3><p>{job.company} · {providers[job.provider]}</p></div><span className={'queue-state' + (job.newly_discovered ? ' ready' : '')}>{job.reviewed ? 'Reviewed' : job.newly_discovered ? 'New match' : 'Unreviewed'}</span></div>
          <dl className="posting-details"><div><dt>Compatibility</dt><dd>{job.compatibility.score}/100</dd></div><div><dt>Evidence confidence</dt><dd>{job.compatibility.confidence}%</dd></div>
            <div><dt>Location</dt><dd>{job.location || 'Unspecified'}</dd></div><div><dt>Preference match</dt><dd>{matches[job.match]}</dd></div></dl>
          <div className="queue-links">{job.url && <a href={job.url} target="_blank" rel="noopener noreferrer">Original posting</a>}<a href={'#preparation/' + job.id}>Prepare application</a></div>
          <button disabled={busy} onClick={() => void update(base + '/jobs/' + job.id, 'PATCH', { reviewed: !job.reviewed }, job.title + (job.reviewed ? ' marked unreviewed.' : ' marked reviewed.'))}>{job.reviewed ? 'Mark unreviewed' : 'Mark reviewed'}</button>
        </article>)}</div>
        {(offset > 0 || page?.has_more) && <nav className="profile-actions" aria-label="Shortlist pages"><button disabled={!offset} onClick={() => {setPage(null); setOffset(n => n - 50);}}>Previous page</button><button disabled={!page?.has_more} onClick={() => {setPage(null); setOffset(n => n + 50);}}>Next page</button></nav>}
      </section>
      <section className="panel form"><h2>Discovery activity</h2>
        {activity.length ? <ul className="queue-list">{activity.map(item => <li key={item.id}><div><strong>{item.kind === 'new_match' ? 'New match' : 'Refresh failed'}{item.title ? ' · ' + item.title : ''}</strong>
          {item.company && <p>{item.company}</p>}{item.detail && <p>{item.detail}</p>}</div>{time(item.created_at, '')}</li>)}</ul> : <p>New matches and refresh errors will appear here.</p>}
      </section>
    </>}
  </main>;
}
