import { useEffect, useState, type FormEvent } from 'react';
import { request } from './api';

const states = { draft: 'Draft', ready: 'Ready', submitted: 'Submitted', follow_up: 'Follow-up' };
type State = keyof typeof states;
type Entry = {
  id: number; event: 'submitted' | 'corrected'; created_at: string; submitted_at: string;
  channel: string; reference: string | null; note: string | null; follow_up_on: string | null; url: string | null;
};
type Row = {
  key: string; job_id: number | null; listing_id: number | null; title: string; company: string;
  location: string | null; status: string | null; url: string | null; state: State;
  prep_id: number | null; prep_revision: number | null; prep_approved: boolean;
  resume_id: number | null; resume_revision: number | null; resume_approved: boolean;
  readiness_reason: string; history_unknown: boolean; duplicate_warning: string | null; history: Entry[];
};
const base = '/api/queue';
const message = (e: unknown) => e instanceof Error ? e.message : 'Please try again.';

function localTime(instant: string | Date) {
  const date = new Date(instant);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function postingLink(raw: string | null) {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol === 'https:' && !url.username && !url.password) return url;
  } catch { /* The saved posting may lack a usable address. */ }
  return null;
}

function EntryDetails({ entry }: { entry: Entry }) {
  const source = postingLink(entry.url);
  return <dl className="posting-details">
    <div><dt>Submitted</dt><dd>{new Date(entry.submitted_at).toLocaleString()}</dd></div>
    <div><dt>Channel</dt><dd>{entry.channel}</dd></div>
    <div><dt>Recorded posting</dt><dd>{source ? <a href={source.href} target="_blank" rel="noopener noreferrer">{source.hostname} ↗</a> : 'Unavailable'}</dd></div>
    {entry.reference && <div><dt>Reference</dt><dd>{entry.reference}</dd></div>}
    {entry.follow_up_on && <div><dt>Follow-up date</dt><dd>{entry.follow_up_on}</dd></div>}
    {entry.note && <div className="queue-notes"><dt>Notes</dt><dd>{entry.note}</dd></div>}
  </dl>;
}

function Confirmation({ row, onSaved, onCancel, onReload }: { row: Row; onSaved: (r: Row) => void; onCancel: () => void; onReload: () => Promise<void> }) {
  const latest = row.history[0];
  const correcting = !!latest;
  const warning = row.duplicate_warning || (row.history_unknown ? 'The tracker stage suggests an earlier application. Check your records before continuing.' : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const time = new Date(String(data.get('submitted_at')));
    setError('');
    if (!Number.isFinite(time.getTime()) || time.getTime() > Date.now()) {
      setError('Choose a submission time in the past or present.'); return;
    }
    setBusy(true);
    try {
      const body = {
        confirmed: data.has('confirmed'),
        submitted_at: latest && localTime(latest.submitted_at) === data.get('submitted_at') ? latest.submitted_at : time.toISOString(),
        channel: String(data.get('channel')).trim(),
        reference: String(data.get('reference')).trim(), note: String(data.get('note')).trim(),
        follow_up_on: data.get('follow_up_on') || null,
        ...(correcting ? {expected_event_id: latest.id} : {acknowledge_history: data.has('acknowledge_history')}),
      };
      const result = await request<Row>(base + '/' + row.key + (correcting ? '/correct' : '/submit'), {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body),
      });
      onSaved(result);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <form onSubmit={save} className="queue-confirmation">
    <h3>{correcting ? 'Correct submission details' : 'Confirm manual submission'}</h3>
    <p>{correcting ? 'Corrections add a dated entry to the application history.' : 'Complete the application with the employer, then record it here.'}</p>
    {!correcting && (!row.status || row.status === 'saved') && <p>Recording this submission sets the dashboard stage to Applied.</p>}
    <fieldset className="preference-fields" disabled={busy}>
      <div className="queue-fields">
        <label className="profile-field">Submission date and time (local)<input type="datetime-local" name="submitted_at" required defaultValue={localTime(latest?.submitted_at || new Date())} /></label>
        <label className="profile-field">Submission channel<input name="channel" required maxLength={80} defaultValue={latest?.channel} placeholder="Employer website, email, referral…" /></label>
        <label className="profile-field">Confirmation reference (optional)<input name="reference" maxLength={240} defaultValue={latest?.reference ?? ''} /></label>
        <label className="profile-field">Follow-up date (optional)<input type="date" name="follow_up_on" defaultValue={latest?.follow_up_on ?? ''} /></label>
      </div>
      <label className="profile-field">{correcting ? 'Correction notes' : 'Notes (optional)'}<textarea name="note" rows={3} required={correcting} maxLength={2000} /></label>
      {!correcting && warning && <label className="queue-check"><input type="checkbox" name="acknowledge_history" required />I checked the possible prior submission and want to record this application.</label>}
      <label className="queue-check"><input type="checkbox" name="confirmed" required />
        {correcting ? 'I confirm these corrected submission details.' : 'I confirm that I submitted this application myself.'}</label>
      {error && <div role="alert" className="error">{error} <button type="button" onClick={async () => {
        if (!window.confirm('Discard these unsaved details and reload the latest submission?')) return;
        setBusy(true); try { await onReload(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
      }}>Reload latest submission</button></div>}
      <div className="profile-actions"><button className="primary">{busy ? 'Saving…' : correcting ? 'Save correction' : 'Record submission'}</button><button type="button" onClick={onCancel}>Cancel</button></div>
    </fieldset>
  </form>;
}

function QueueDetail({ row, onSaved }: { row: Row; onSaved: (r: Row) => void }) {
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState('');
  const latest = row.history[0];
  const url = postingLink(row.url);
  const warning = row.duplicate_warning || (row.history_unknown ? 'The tracker stage indicates a possible prior application. Its submission details are unknown.' : '');
  return <section className="panel form queue-detail">
    <a href="#queue">← All queue entries</a>
    <div className="discovery-card-head"><div><h2>{row.title}</h2><p>{row.company}{row.location && ' · ' + row.location}</p></div>
      <span className={'queue-state ' + row.state}>{states[row.state]}</span></div>
    <p>Tracker stage · {row.status || 'Untracked'}</p>
    {url ? <p><a href={url.href} target="_blank" rel="noopener noreferrer">Open Official Application</a> <small>({url.hostname})</small></p>
      : <p>Original posting unavailable. Use your own employer link or submission channel.</p>}
    {warning && <p role="note" className="queue-warning">{warning}</p>}
    <h3>Materials for review</h3>
    <dl className="posting-details">
      <div><dt>Tailored resume</dt><dd>{row.resume_id ? '#' + row.resume_id + ' · revision ' + row.resume_revision + (row.resume_approved ? ' · approved' : ' · awaiting approval') : 'No selected revision'}</dd></div>
      <div><dt>Preparation materials</dt><dd>{row.prep_id ? '#' + row.prep_id + ' · revision ' + row.prep_revision + (row.prep_approved ? ' · approved' : ' · awaiting approval') : 'No preparation record'}</dd></div>
    </dl>
    {row.readiness_reason && <p>{row.readiness_reason}</p>}
    {row.listing_id && <div className="queue-links"><a href={'#builder/' + row.listing_id}>Review resume</a><a href={'#preparation/' + row.listing_id}>Review preparation materials</a></div>}
    <h3>Submission record</h3>
    {latest ? <EntryDetails entry={latest} /> : <p>No confirmed submission recorded.</p>}
    {status && <p role="status" className="success">{status}</p>}
    {editing ? <Confirmation key={latest?.id || row.key} row={row} onCancel={() => setEditing(false)} onReload={async () => {
      const entries = await request<Row[]>(base);
      const fresh = entries.find(r => r.key === row.key);
      if (!fresh) throw new Error('Queue entry unavailable.');
      setEditing(false); setStatus('Latest submission loaded.'); onSaved(fresh);
    }} onSaved={result => {
      setEditing(false); setStatus(latest ? 'Correction recorded.' : 'Manual submission recorded.'); onSaved(result);
    }} /> : <button className="primary" onClick={() => { setStatus(''); setEditing(true); }}>{latest ? 'Correct submission details' : 'Record manual submission'}</button>}
    {row.history.length > 0 && <details className="queue-history"><summary>Submission history ({row.history.length})</summary>
      <ol>{row.history.map(event => <li key={event.id}><strong>{event.event === 'corrected' ? 'Correction recorded' : 'Submission recorded'}</strong> · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time>
        <EntryDetails entry={event} /></li>)}</ol>
    </details>}
  </section>;
}

export function ApplicationQueue({ initial }: { initial: string }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<State | 'all'>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(initial);
  async function load() {
    setError('');
    try { setRows(await request<Row[]>(base)); setReady(true); } catch (e) { setError(message(e)); }
  }
  useEffect(() => { void load(); }, []);
  const row = rows.find(r => r.key === selected || (r.job_id && selected === 'job-' + r.job_id) || (r.listing_id && selected === 'listing-' + r.listing_id));
  const visible = rows.filter(r => (filter === 'all' || filter === r.state) && (r.title + ' ' + r.company).toLowerCase().includes(query.trim().toLowerCase()));
  return <main className="wrap application-queue"><div className="intro"><div><h1>Application queue</h1><p>Review materials, record manual submissions and track follow-ups.</p></div></div>
    {error && <p role="alert" className="error">{error} <button onClick={load}>Retry</button></p>}
    {!ready && !error && <p role="status">Loading application queue…</p>}
    {ready && (selected ? (row ? <QueueDetail row={row} onSaved={result => {
      setRows(current => current.map(item => item.key === row.key ? result : item)); setSelected(result.key);
      window.history.replaceState(null, '', '#queue/' + result.key);
    }} /> : <div className="panel empty"><strong>Queue entry unavailable</strong><p><a href="#queue">View all queue entries</a></p></div>) :
    <section className="panel form"><div className="queue-fields">
      <label className="profile-field">Search queue<input type="search" placeholder="Job title or company" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <label className="profile-field">Queue status<select value={filter} onChange={e => setFilter(e.target.value as State | 'all')}><option value="all">All statuses</option>
        {Object.entries(states).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
    </div><p role="status">{visible.length} {visible.length === 1 ? 'entry' : 'entries'}</p>
    {!visible.length && <div className="empty"><strong>{rows.length ? 'No entries match your filters' : 'Your queue is empty'}</strong>
      {!rows.length && <p>Find an opening in <a href="#discovery">Job Discovery</a> or add a job on your <a href="#dashboard">dashboard</a>.</p>}</div>}
    <ul className="queue-list">{visible.map(item => <li key={item.key}><div><a href={'#queue/' + item.key}>{item.title}</a>
      <p>{item.company}{item.location && ' · ' + item.location}</p>
      <p>{item.readiness_reason}</p>
      {item.history.length > 0 && <p>Submission confirmed · {new Date(item.history[0].submitted_at).toLocaleDateString()}</p>}
      {item.history[0]?.follow_up_on && <p>Follow up {item.history[0].follow_up_on}</p>}
      {!item.history.length && (item.history_unknown || item.duplicate_warning) && <p className="queue-alert-text">Review possible prior submission</p>}</div>
      <span className={'queue-state ' + item.state}>{states[item.state]}</span></li>)}</ul></section>)}
  </main>;
}


