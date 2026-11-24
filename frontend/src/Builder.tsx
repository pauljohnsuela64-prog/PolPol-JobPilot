import { useEffect, useState, type FormEvent } from 'react';
import { request, send } from './api';

const fields = {
  contact: 'Contact details', education: 'Education', experience: 'Work and internship experience',
  skills: 'Technical skills', projects: 'Projects', achievements: 'Achievements', certifications: 'Certifications',
};
type Master = { name: string; summary: string | null } & Record<keyof typeof fields, string[]>;
type Job = { id: number; title: string; company: string };
type Version = { id: number; created_at: string; revision: number };
type Draft = { id: number; revision: number; approved_revision: number | null; content: string; description: string;
  evidence: { skill: string; context: string; confirmed: boolean }[] };
const base = '/api/builder';
const json = (method: string, body: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const message = (e: unknown) => e instanceof Error ? e.message : 'The operation failed. Please retry.';

export function MasterEditor() {
  const [master, setMaster] = useState<Master | null>();
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    setError('');
    try { setMaster(await request<Master | null>(base + '/master')); }
    catch (e) { setError(message(e)); }
  }
  useEffect(() => { void load(); }, []);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const data = Object.fromEntries(Object.keys(fields).map(key => [key, String(form.get(key)).split('\n').map(s => s.trim()).filter(Boolean)]));
    setBusy(true); setError(''); setStatus('');
    try {
      setMaster(await request<Master>(base + '/master', json('PUT', { ...data, name: form.get('name'), summary: form.get('summary') || null })));
      setStatus('Verified master resume saved.');
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <section className="panel form"><h2>Master resume</h2>
    <p>Enter only facts you can verify. Use one line per entry, keeping each role or project with its dates and achievements.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {master === undefined ? <p>{error ? <button onClick={load}>Retry</button> : 'Loading master resume...'}</p> :
    <form onSubmit={save}><fieldset className="preference-fields" disabled={busy}>
      <label className="profile-field">Full name<input name="name" required maxLength={160} defaultValue={master?.name} /></label>
      <label className="profile-field">Professional summary<input name="summary" maxLength={1500} defaultValue={master?.summary ?? ''} /></label>
      {Object.entries(fields).map(([key,label]) => <div className="profile-field" key={key}><label htmlFor={'master-' + key}>{label}</label>
        <textarea id={'master-' + key} name={key} rows={2} defaultValue={master?.[key as keyof typeof fields]?.join('\n')} /></div>)}
      <button className="primary">Save verified master resume</button>
    </fieldset></form>}
    {status && <p role="status">{status}</p>}
  </section>;
}

function Drafts({ job }: { job: number }) {
  const path = base + '/jobs/' + job + '/drafts';
  const [versions, setVersions] = useState<Version[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [text, setText] = useState('');
  const [approved, approve] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const dirty = draft !== null && text !== draft.content;
  async function history() { setVersions(await request<Version[]>(path)); }
  useEffect(() => { void history().catch(e => setError(message(e))); }, [path]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('');
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function open(id: number) {
    const data = await request<Draft>(base + '/drafts/' + id);
    setDraft(data); setText(data.content); approve(false);
  }
  async function download(format: string) {
    if (!draft || !approved || dirty) return;
    const response = await send(base + '/drafts/' + draft.id + '/export/' + format, json('POST', { approved, revision: draft.revision }));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = 'resume-' + draft.id + '-r' + draft.revision + '.' + format;
    link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus('Download started.');
  }
  return <section className="panel form"><h2>Tailored drafts</h2>
    {error && <p role="alert" className="error">{error}</p>}
    <fieldset className="preference-fields" disabled={busy}>
      <button className="primary" disabled={dirty} onClick={() => run(async () => {
        const next = await request<{id:number}>(path, { method: 'POST' }); await open(next.id); await history();
      })}>Generate tailored draft</button>
      <label className="profile-field">Draft history<select value={draft?.id ?? ''} disabled={dirty} onChange={e => run(() => open(Number(e.target.value)))}>
        <option value="" disabled>Choose a saved draft</option>{versions.map(v => <option key={v.id} value={v.id}>Draft {v.id} · revision {v.revision} · {new Date(v.created_at).toLocaleString()}</option>)}
      </select></label>
      {draft && <>
        {!draft.description && <p>This opening has no stored description. Review the draft against the original posting.</p>}
        <h3>Suggestions for review</h3>
        <p>{draft.evidence.filter(e => !e.confirmed && e.context !== 'mentioned').map(e => e.skill + ' (' + e.context + ')').join(', ') || 'No unverified required or preferred skills identified.'}</p>
        <label className="profile-field">Draft content<textarea rows={12} value={text} onChange={e => { setText(e.target.value); approve(false); }} /></label>
        <button disabled={!dirty || !text.trim()} onClick={() => run(async () => {
          await request(base + '/drafts/' + draft.id, json('PUT', { content: text, revision: draft.revision }));
          await open(draft.id); await history(); setStatus('Draft edits saved.');
        })}>Save edits</button>
        {dirty && <button onClick={() => { if (window.confirm('Discard unsaved edits and reload the saved draft?')) void run(() => open(draft.id)); }}>Discard unsaved edits</button>}
        <h3>Resume preview</h3><pre className="resume-preview">{text}</pre>
        <h3>Application approval</h3>
        <p>{draft.approved_revision === draft.revision ? 'Approved for applications' :
          draft.approved_revision == null ? 'Approval needed' : 'Approval outdated'} · saved revision {draft.revision}</p>
        <button disabled={dirty} onClick={() => run(async () => {
          await request(base + '/drafts/' + draft.id + '/approval', json('PUT', {
            revision: draft.revision, approved: draft.approved_revision !== draft.revision,
          }));
          await open(draft.id);
        })}>{draft.approved_revision === draft.revision ? 'Revoke application approval' : 'Approve this revision for applications'}</button>
        <label><input type="checkbox" checked={approved} disabled={dirty} onChange={e => approve(e.target.checked)} /> I reviewed and approve this revision for export.</label>
        <div className="profile-actions">{['docx','pdf'].map(format => <button key={format} disabled={!approved || dirty} onClick={() => run(() => download(format))}>Download {format.toUpperCase()}</button>)}</div>
        <button onClick={() => { if (window.confirm('Delete this saved draft?')) void run(async () => {
          await send(base + '/drafts/' + draft.id, {method:'DELETE'}); setDraft(null); approve(false); await history();
        }); }}>Delete draft</button>
      </>}
    </fieldset>{status && <p role="status">{status}</p>}
  </section>;
}

export function Builder({ initial }: { initial: number }) {
  const [jobs,setJobs] = useState<Job[]>([]);
  const [job,setJob] = useState(initial);
  const [error,setError] = useState('');
  useEffect(() => { void request<Job[]>(base + '/jobs').then(setJobs).catch(e => setError(message(e))); }, []);
  return <main className="wrap builder"><section className="intro"><h1>Resume Builder</h1></section>
    <details><summary>Edit verified master resume</summary><MasterEditor /></details>
    {error && <p role="alert">{error}</p>}
    <label className="profile-field">Selected opening<select value={job} onChange={e => setJob(Number(e.target.value))}>
      <option value={0}>Choose a discovered job</option>{jobs.map(j => <option key={j.id} value={j.id}>{j.title} · {j.company}</option>)}
    </select></label>
    {job > 0 && <Drafts key={job} job={job} />}
  </main>;
}
