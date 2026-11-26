import { useEffect, useState } from 'react';
import { request } from './api';

const base = '/api/assistant/jobs';
const synthetic = 'http://127.0.0.1:4179/application.html';
type Job = { listing_id: number; title: string; company: string };
type Field = { key: string; label: string; value: string; status: 'verified' | 'reviewed' | 'unsupported'; source: string };
type Activity = { event: string; field_count: number; created_at: string };
type Packet = Job & { resume_revision: number; prep_revision: number; revision_key: string; fields: Field[]; activity: Activity[] };
type Handoff = { token: string; expires_at: string };
const labels = { verified: 'Verified fact', reviewed: 'Reviewed answer', unsupported: 'Needs your input.' };
const events: Record<string, string> = {
  prepared: 'Package prepared', copy_requested: 'Field copy requested',
  fill_authorized: 'Field filling authorized', filled: 'Fields filled',
};
const message = (e: unknown) => e instanceof Error ? e.message : 'Please try again.';
const post = (body: object) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function ActivityList({ items }: { items: Activity[] }) {
  return items.length ? <ol className="assistant-activity">{items.map((item, i) => <li key={i}>
    <strong>{events[item.event] || item.event}</strong>{' · ' + item.field_count + (item.field_count === 1 ? ' field' : ' fields')}
    <small><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></small>
  </li>)}</ol> : <p>No assistant activity recorded.</p>;
}

export function QueueAssistantActivity({ listing }: { listing: number }) {
  const [items, setItems] = useState<Activity[] | null>(null);
  const [error, setError] = useState('');
  return <details onToggle={async e => {
    if (!e.currentTarget.open) return;
    setError('');
    try { setItems(await request<Activity[]>(base + '/' + listing + '/activity', { cache: 'no-store' })); }
    catch (err) { setError(message(err)); }
  }}><summary>Assistant activity</summary>
    {error ? <p role="alert">{error}</p> : items ? <ActivityList items={items} /> : <p>Loading activity…</p>}
  </details>;
}

function PackageReview({ listing }: { listing: number }) {
  const [packet, setPacket] = useState<Packet | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const path = base + '/' + listing;
  const fetchPacket = () => request<Packet>(path + '/package', { cache: 'no-store' });

  async function load() {
    setBusy(true); setReviewed(false); setHandoff(null); setPacket(null); setError(''); setStatus('');
    try { setPacket(await fetchPacket()); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, [listing]);
  useEffect(() => {
    if (!handoff) return;
    const timeout = window.setTimeout(() => {
      setHandoff(null); setStatus('The handoff code expired. Review the package to create another.'); setReviewed(false);
    }, Math.max(0, Date.parse(handoff.expires_at) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [handoff]);

  async function act(task: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('');
    try { await task(); } catch (e) { setError(message(e)); setReviewed(false); setHandoff(null); }
    finally { setBusy(false); }
  }

  return <section className="panel form assistant-review">
    <a href="#assistant">← Ready jobs</a>
    {error && <p className="error" role="alert">{error} <button disabled={busy} onClick={load}>Reload package</button></p>}
    {!packet && busy && <p role="status">Loading reviewed fields…</p>}
    {packet && <>
      <div className="discovery-card-head"><div><h2>{packet.title}</h2><p>{packet.company}</p></div>
        <a href={'#queue/listing-' + listing}>View queue entry</a></div>
      <p>Approved resume revision {packet.resume_revision} · Approved preparation revision {packet.prep_revision}</p>
      <h3>Field preview</h3>
      <p>Review each value and its source. You can copy individual values for other forms after review.</p>
      <ul className="assistant-fields">{packet.fields.map(field => <li key={field.key}>
        <div><h4>{field.label}</h4><small>{labels[field.status]}{field.source && ' · ' + field.source}</small>
          {field.status !== 'unsupported' && <p className="assistant-value">{field.value}</p>}</div>
        {field.status !== 'unsupported' && <button disabled={!reviewed || busy} aria-label={'Copy ' + field.label} onClick={() => void act(async () => {
          const result = await request<{ value: string }>(path + '/copy', post({ revision_key: packet.revision_key, field_key: field.key }));
          if (result.value !== field.value) throw new Error('This value changed. Reload and review the package.');
          try { await navigator.clipboard.writeText(result.value); setStatus(field.label + ' copied.'); }
          catch { setStatus('Clipboard access is unavailable. Select and copy the displayed value manually.'); }
        })}>Copy</button>}
      </li>)}</ul>
      <label className="queue-check"><input type="checkbox" checked={reviewed} disabled={busy} onChange={e => {
        setReviewed(e.target.checked); setHandoff(null); setStatus('');
      }} />I reviewed these fields against my approved application materials.</label>
      <h3>Fill the local practice form</h3>
      <p>Open the <a href={synthetic} rel="noopener noreferrer" target="_blank">synthetic application form</a>. Paste the one-time code into the JobPilot Edge extension and confirm the fields there.</p>
      <button className="primary" disabled={busy || !reviewed || !!handoff} onClick={() => void act(async () => {
        setHandoff(await request<Handoff>(path + '/handoff', post({ reviewed: true, revision_key: packet.revision_key })));
        setStatus('Handoff code ready.');
      })}>Create handoff code</button>
      {handoff && <div className="assistant-handoff"><label className="profile-field">One-time code
        <input readOnly value={handoff.token} autoComplete="off" spellCheck={false} onFocus={e => e.currentTarget.select()} />
        <small>Expires at {new Date(handoff.expires_at).toLocaleTimeString()}.</small></label></div>}
      {status && <p role="status" className="success">{status}</p>}
      <h3>Assistant activity</h3>
      <p>Submission history is recorded separately in the <a href={'#queue/listing-' + listing}>Application Queue</a>.</p>
      <button disabled={busy} onClick={() => void act(async () => {
        const fresh = await fetchPacket();
        if (fresh.revision_key !== packet.revision_key) { setReviewed(false); setHandoff(null); }
        setPacket(fresh); setStatus('Activity refreshed.');
      })}>Refresh activity</button>
      <ActivityList items={packet.activity} />
    </>}
  </section>;
}

export function ApplicationAssistant({ listing }: { listing: number }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState('');
  async function load() {
    setError('');
    try { setJobs(await request<Job[]>(base, { cache: 'no-store' })); }
    catch (e) { setError(message(e)); }
  }
  useEffect(() => { if (!listing) void load(); }, [listing]);
  return <main className="wrap application-assistant"><div className="intro"><div>
    <h1>Application Assistant</h1><p>Prepare approved application fields for a Ready job.</p>
  </div></div>
    {listing ? <PackageReview listing={listing} /> : <section className="panel form"><h2>Ready jobs</h2>
      {error && <p role="alert" className="error">{error} <button onClick={load}>Retry</button></p>}
      {!jobs && !error && <p role="status">Loading Ready jobs…</p>}
      {jobs && (jobs.length ? <ul className="queue-list">{jobs.map(job => <li key={job.listing_id}><div>
        <a href={'#assistant/' + job.listing_id}>{job.title}</a><p>{job.company}</p>
      </div></li>)}</ul> : <p>No eligible jobs. Check your resume and preparation approvals in the <a href="#queue">Application Queue</a>.</p>)}
    </section>}
  </main>;
}
