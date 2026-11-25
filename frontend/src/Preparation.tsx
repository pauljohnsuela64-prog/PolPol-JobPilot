import { SuggestionReview } from './AI';
import { useEffect, useState } from 'react';
import { request, send } from './api';

type Job = { id: number; title: string; company: string; location?: string; description?: string; url?: string };
type Version = { id: number; revision: number; created_at: string };
type Content = {
  cover_letter: string;
  answers: { question: string; answer: string }[];
  checklist: { text: string; done: boolean }[];
};
type Draft = Version & Content & {
  approved_revision: number | null;
  job_snapshot: Job;
  resume_snapshot: { id: number; revision: number; content: string } | null;
};
const api = '/api/preparation';
const json = (method: string, body: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const message = (error: unknown) => error instanceof Error ? error.message : 'Please try again.';
const versionLabel = (v: Version) => '#' + v.id + ' · revision ' + v.revision + ' · ' + new Date(v.created_at).toLocaleString();

function JobDetails({ job, saved = false }: { job: Job; saved?: boolean }) {
  return <details><summary>{saved ? 'Saved job snapshot' : 'Job details and original posting'}</summary>
    <p><strong>{job.title}</strong> · {job.company}{job.location && ' · ' + job.location}</p>
    <p className="prep-description">{job.description || 'Job description unavailable.'}</p>
    {job.url && /^https?:\/\//i.test(job.url) && <a href={job.url} target="_blank" rel="noopener noreferrer">Open original posting</a>}
  </details>;
}

function Workspace({ job }: { job: Job }) {
  const path = api + '/jobs/' + job.id;
  const [versions, setVersions] = useState<Version[]>([]);
  const [resumes, setResumes] = useState<Version[]>([]);
  const [resume, setResume] = useState('');
  const [prompts, setPrompts] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [content, setContent] = useState<Content | null>(null);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const draftPath = api + '/drafts/' + draft?.id;
  const dirty = !!draft && !!content && (draft.cover_letter !== content.cover_letter ||
    JSON.stringify(draft.answers) !== JSON.stringify(content.answers) ||
    JSON.stringify(draft.checklist) !== JSON.stringify(content.checklist));
  const approved = !!draft && draft.approved_revision === draft.revision && !dirty;
  const complete = !!content?.cover_letter.trim() && content.answers.every(a => a.question.trim()) && content.checklist.every(c => c.text.trim());
  function display(next: Draft) {
    setDraft(next);
    setContent({ cover_letter: next.cover_letter, answers: next.answers, checklist: next.checklist });
  }
  async function history() { setVersions(await request<Version[]>(path + '/drafts')); }
  async function load() {
    setError('');
    try {
      const [saved, choices] = await Promise.all([request<Version[]>(path + '/drafts'), request<Version[]>(path + '/resumes')]);
      setVersions(saved); setResumes(choices); setReady(true);
    } catch (e) { setError(message(e)); }
  }
  useEffect(() => { void load(); }, [path]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('');
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function download(artifact: string, format: string) {
    if (!approved || !draft) return;
    const response = await send(draftPath + '/export/' + artifact + '/' + format, json('POST', { revision: draft.revision }));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = artifact + '-' + draft.id + '-r' + draft.revision + '.' + format;
    link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus('Download started.');
  }
  return <>
    {error && <p role="alert" className="error">{error} {!ready && <button onClick={load}>Retry</button>}
      {draft && <button disabled={busy} onClick={() => {
        if (!dirty || window.confirm('Discard unsaved edits and load the latest saved revision?')) void run(async () => display(await request<Draft>(draftPath)));
      }}>Reload saved revision</button>}</p>}
    {status && <p role="status" className="success">{status}</p>}
    {!ready && !error && <p role="status">Loading preparation records…</p>}
    {ready && <><div className="prep-setup">
      <section className="panel form"><h2>New preparation record</h2>
        <JobDetails job={job} />
        <form onSubmit={e => { e.preventDefault(); void run(async () => {
          const next = await request<Draft>(path + '/drafts', json('POST', { resume_id: resume ? Number(resume) : null, questions: prompts.split('\n').map(s => s.trim()).filter(Boolean) }));
          display(next); setPrompts(''); await history(); setStatus('Preparation record created. Review the generated materials below.');
        }); }}>
          <fieldset className="preference-fields" disabled={busy || dirty}>
            <label className="profile-field">Tailored resume version<select value={resume} onChange={e => setResume(e.target.value)}>
              <option value="">Continue without a tailored resume</option>
              {resumes.map(v => <option key={v.id} value={v.id}>{versionLabel(v)}</option>)}
            </select></label>
            <label className="profile-field">Screening questions <small>Enter one employer question per line to generate answer suggestions.</small>
              <textarea rows={3} value={prompts} onChange={e => setPrompts(e.target.value)} /></label>
            <button className="primary">Generate preparation draft</button>
          </fieldset>
        </form>
      </section>
      <section className="panel form"><h2>Preparation history</h2>
        {versions.length ? <label className="profile-field">Saved record<select value={draft?.id ?? ''} disabled={busy || dirty} onChange={e => {
          const id = e.target.value; void run(async () => display(await request<Draft>(api + '/drafts/' + id)));
        }}><option value="" disabled>Choose a record</option>{versions.map(v => <option key={v.id} value={v.id}>{versionLabel(v)}</option>)}</select></label>
          : <p>No saved records for this job.</p>}
      </section>
    </div>
    {draft && content && <section className="panel form"><h2>Preparation record #{draft.id}</h2>
      <p>Revision {draft.revision} · {dirty ? 'Unsaved changes' : approved ? 'Approved for export' : 'Awaiting review'}</p>
      <JobDetails job={draft.job_snapshot} saved />
      {draft.resume_snapshot ? <details><summary>Selected resume #{draft.resume_snapshot.id} · revision {draft.resume_snapshot.revision}</summary>
        <pre className="resume-preview">{draft.resume_snapshot.content}</pre></details> : <p>No tailored resume attached.</p>}
      <fieldset disabled={busy} className="preference-fields">
        <label className="profile-field"><strong>Cover letter</strong><textarea rows={12} maxLength={16000} value={content.cover_letter}
          onChange={e => setContent({ ...content, cover_letter: e.target.value })} /></label>
        <h3>Screening answers</h3>
        {content.answers.map((item, index) => <div className="prep-question" key={index}>
          {(['question', 'answer'] as const).map(field => <label className="profile-field" key={field}>{field === 'question' ? 'Question' : 'Your answer'} {index + 1}
            <textarea rows={field === 'question' ? 2 : 3} maxLength={field === 'question' ? 1500 : 16000} value={item[field]} onChange={e => setContent({ ...content,
              answers: content.answers.map((a, i) => {
                if (i !== index) return a;
                return field === 'question' ? { question: e.target.value, answer: 'Needs your input.' } : { ...a, answer: e.target.value };
              }) })} /></label>)}
          <button onClick={() => setContent({ ...content, answers: content.answers.filter((_, i) => i !== index) })}>Remove question {index + 1}</button>
        </div>)}
        <button disabled={content.answers.length >= 40} onClick={() => setContent({ ...content, answers: [...content.answers, { question: '', answer: 'Needs your input.' }] })}>Add screening question</button>
        <h3>Application checklist</h3>
        {content.checklist.map((item, index) => <div key={index} className="prep-item">
          <input type="checkbox" checked={item.done} aria-label={'Completed: ' + (item.text || 'Checklist item ' + (index + 1))} onChange={e => setContent({ ...content,
            checklist: content.checklist.map((c, i) => i === index ? { ...c, done: e.target.checked } : c) })} />
          <input aria-label={'Checklist item ' + (index + 1)} value={item.text} maxLength={1500} onChange={e => setContent({ ...content,
            checklist: content.checklist.map((c, i) => i === index ? { ...c, text: e.target.value } : c) })} />
          <button aria-label={'Remove checklist item ' + (index + 1)} onClick={() => setContent({ ...content, checklist: content.checklist.filter((_, i) => i !== index) })}>Remove</button>
        </div>)}
        <button disabled={content.checklist.length >= 60} onClick={() => setContent({ ...content, checklist: [...content.checklist, {text: '', done: false}] })}>Add checklist item</button>
        <div className="profile-actions"><button className="primary" disabled={!dirty || !complete} onClick={() => run(async () => {
          display(await request<Draft>(draftPath, json('PUT', { revision: draft.revision, ...content }))); await history(); setStatus('Edits saved.');
        })}>Save edits</button>
        {dirty && <button onClick={() => { if (window.confirm('Discard unsaved changes to this record?')) display(draft); }}>Discard edits</button>}</div>
        <SuggestionReview key={draft.id + '-' + draft.revision} kind="preparation" id={draft.id} revision={draft.revision}
          dirty={dirty} lockEditor={setBusy} onApplied={async () => { display(await request<Draft>(draftPath)); await history(); setStatus('Reviewed suggestion saved.'); }} />
        <h3>Review and export</h3>
        <p>Review the letter, answers and checklist before approving this revision. Editing a record requires fresh approval.</p>
        <button disabled={dirty || approved} onClick={() => run(async () => {
          display(await request<Draft>(draftPath + '/approve', json('POST', { revision: draft.revision }))); setStatus('Revision approved for export.');
        })}>Approve revision {draft.revision}</button>
        <div className="profile-actions">{[['cover_letter','docx','Cover letter DOCX'],['cover_letter','pdf','Cover letter PDF'],['answers','txt','Screening answers TXT'],['checklist','txt','Checklist TXT']].map(([artifact,format,label]) =>
          <button key={label} disabled={!approved} onClick={() => run(() => download(artifact, format))}>{label}</button>)}</div>
        <button onClick={() => { if (window.confirm('Delete this preparation record and its saved materials?')) void run(async () => {
          await send(draftPath, {method:'DELETE'}); setDraft(null); setContent(null); await history(); setStatus('Preparation record deleted.');
        }); }}>Delete preparation record</button>
      </fieldset>
    </section>}</>}
  </>;
}

export function Preparation({ initial }: { initial: number }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    setError('');
    try { setJobs(await request<Job[]>(api + '/jobs')); setReady(true); }
    catch (e) { setError(message(e)); }
  }
  useEffect(() => { void load(); }, []);
  const selected = jobs.find(j => j.id === initial);
  return <main className="wrap preparation"><section className="intro"><div><h1>Application preparation</h1>
    <p>Prepare and review materials for a discovered opening.</p></div></section>
    {error && <p className="error" role="alert">{error} <button onClick={load}>Retry</button></p>}
    {!ready && !error && <p role="status">Loading discovered jobs…</p>}
    {ready && <><label className="profile-field prep-job">Selected job<select value={selected?.id ?? ''} onChange={e => { window.location.hash = '#preparation/' + e.target.value; }}>
      <option value="" disabled>Choose a discovered job</option>{jobs.map(j => <option key={j.id} value={j.id}>{j.title} · {j.company}</option>)}</select></label>
      {!jobs.length && <p>Visit <a href="#discovery">Job Discovery</a> to find openings.</p>}
      {selected && <Workspace key={selected.id} job={selected} />}</>}
  </main>;
}

