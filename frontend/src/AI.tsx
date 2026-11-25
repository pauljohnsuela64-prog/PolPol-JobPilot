import { useEffect, useState } from 'react';
import { request } from './api';

const base = '/api/ai';
const message = (error: unknown) => error instanceof Error ? error.message : 'Please try again.';
const json = (method: string, value: unknown) => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});

type Settings = { enabled: boolean; endpoint: string; model: string; models: string[]; status: string; error: string | null };

export function AISettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  async function load() {
    setBusy(true); setError('');
    try { setSettings(await request<Settings>(base + '/settings')); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  return <main className="wrap ai-settings"><section className="intro"><div><h1>AI Settings</h1>
    <p>Connect a local Ollama model for optional job analysis and draft suggestions.</p></div></section>
    {error && <p role="alert" className="error">{error} {!settings && <button onClick={load}>Retry</button>}</p>}
    {!settings && busy && <p role="status">Loading settings…</p>}
    {settings && <form className="panel form" onChange={() => setSaved(false)} onSubmit={async e => {
      e.preventDefault();
      const values = new FormData(e.currentTarget);
      setBusy(true); setError(''); setSaved(false);
      try {
        setSettings(await request<Settings>(base + '/settings', json('PUT', {
          enabled: values.has('enabled'), endpoint: values.get('endpoint'), model: values.get('model') ?? settings.model,
        })));
        setSaved(true);
      } catch (problem) { setError(message(problem)); } finally { setBusy(false); }
    }}><fieldset className="preference-fields" disabled={busy}>
      <legend>Local model connection</legend>
      <label className="queue-check"><input type="checkbox" name="enabled" defaultChecked={settings.enabled} />Enable local AI assistance</label>
      <label className="profile-field">Ollama endpoint<input type="url" name="endpoint" required aria-describedby="endpoint-help" defaultValue={settings.endpoint} /></label>
      <small id="endpoint-help">Use a numeric HTTP loopback address such as http://127.0.0.1:11434. Save it first to list installed models.</small>
      <label className="profile-field">Installed model<select name="model" defaultValue={settings.model}>
        <option value="">Choose an installed model</option>
        {settings.model && !settings.models.includes(settings.model) && <option disabled value={settings.model}>{settings.model} (unavailable)</option>}
        {settings.models.map(name => <option key={name} value={name}>{name}</option>)}
      </select></label>
      <button className="primary">{busy ? 'Checking connection…' : 'Save and check connection'}</button>
    </fieldset>
    <p role="status">{saved && 'Settings saved. '}Connection status: {settings.status}</p>
    {settings.error && <p className="error">{settings.error}</p>}
    </form>}
  </main>;
}

type Origin = { mode: 'ai' | 'rules'; reason: string };
function Source({ result }: { result: Origin }) {
  return <p><strong>{result.mode === 'ai' ? 'Local AI' : 'Rules-based fallback'}</strong>{result.reason && ' · ' + result.reason}</p>;
}

type Analysis = Origin & { matched_skills: string[]; missing_skills: string[]; requirement_excerpts: string[]; possible_gaps: string[]; requirements: { skill: string; context: string; excerpt: string; confirmed: boolean }[]; evidence: string[] };
export function JobAnalysis({ job }: { job: number }) {
  const [result, setResult] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <details className="ai-review"><summary>Job analysis</summary>
    <button disabled={busy} onClick={async () => {
      setBusy(true); setError('');
      try { setResult(await request<Analysis>(base + '/jobs/' + job + '/analysis', { method: 'POST' })); }
      catch (e) { setError(message(e)); } finally { setBusy(false); }
    }}>{busy ? 'Analyzing…' : 'Analyze job description'}</button>
    {error && <p role="alert" className="error">{error}</p>}
    {result && <div><Source result={result} />
      <p><strong>Matched skills</strong> · {result.matched_skills.join(', ') || 'None identified'}</p>
      <p><strong>Unverified skills</strong> · {result.missing_skills.join(', ') || 'None identified'}</p>
      <strong>Job requirements</strong>{result.requirements.length ? <ul>{result.requirements.map((item, i) => <li key={i}>{item.skill} &middot; {item.context}<br /><q>{item.excerpt}</q></li>)}</ul> : <p>No skill requirements identified.</p>}
      <strong>Requirements from the posting</strong>{result.requirement_excerpts.length ? <ul>{result.requirement_excerpts.map((line, i) => <li key={i}><q>{line}</q></li>)}</ul> : <p>No requirement excerpts found.</p>}
      <strong>Possible gaps to review</strong>{result.possible_gaps.length ? <ul>{result.possible_gaps.map((line, i) => <li key={i}><q>{line}</q></li>)}</ul> : <p>No possible gaps identified.</p>}
      <strong>Verified resume evidence</strong>{result.evidence.length ? <ul>{result.evidence.map((line, i) => <li key={i}>{line}</li>)}</ul> : <p>No supporting excerpts found.</p>}
    </div>}
  </details>;
}

type Material = { content: string } | { cover_letter: string; answers: { question: string; answer: string }[]; checklist: { text: string; done: boolean }[] };
type Suggestion = Origin & { id: number; original: Material; proposed: Material; revision: number };
function MaterialPreview({ material }: { material: Material }) {
  if ('content' in material) return <pre className="resume-preview">{material.content}</pre>;
  return <div><h4>Cover letter</h4><pre className="resume-preview">{material.cover_letter}</pre>
    <h4>Screening answers</h4>{material.answers.length ? material.answers.map((item, i) => <div key={i}><strong>{item.question}</strong><p>{item.answer}</p></div>) : <p>No questions.</p>}
    <h4>Checklist</h4><ul>{material.checklist.map((item, i) => <li key={i}>{item.done ? 'Completed' : 'Pending'} · {item.text}</li>)}</ul>
  </div>;
}

type Accepted = Pick<Suggestion, 'id' | 'revision' | 'original' | 'proposed'> & { applied_revision: number };
function sameMaterial(a: Material, b: Material) {
  if ('content' in a || 'content' in b) return 'content' in a && 'content' in b && a.content === b.content;
  return a.cover_letter === b.cover_letter && a.answers.length === b.answers.length &&
    a.answers.every((v, i) => v.question === b.answers[i].question && v.answer === b.answers[i].answer) &&
    a.checklist.length === b.checklist.length && a.checklist.every((v, i) => v.text === b.checklist[i].text && v.done === b.checklist[i].done);
}

export function SuggestionReview({ kind, id, revision, dirty, lockEditor, onApplied }: {
  kind: 'resume' | 'preparation'; id: number; revision: number; dirty: boolean; lockEditor: (locked: boolean) => void; onApplied: () => Promise<void>;
}) {
  const [result, setResult] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<Accepted[] | null>(null);
  const unchanged = !!result && sameMaterial(result.original, result.proposed);
  return <details className="ai-review"><summary>Review assisted suggestions</summary>
    <p>Compare the suggestion with saved revision {revision} before applying it.</p>
    <button disabled={busy || dirty} onClick={async () => {
      setBusy(true); setError('');
      try { setResult(await request<Suggestion>(base + '/' + kind + '/' + id + '/suggestions', json('POST', { revision }))); }
      catch (e) { setError(message(e)); } finally { setBusy(false); }
    }}>{busy ? 'Working…' : 'Generate suggestion'}</button>
    {dirty && <p>Save or discard your edits to use suggestions.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {result && <><Source result={result} /><div className="ai-comparison">
      <section><h3>Saved revision {result.revision}</h3><MaterialPreview material={result.original} /></section>
      <section><h3>Proposed revision</h3><MaterialPreview material={result.proposed} /></section>
    </div><div className="profile-actions">
      {unchanged && <p>The suggestion matches the saved revision.</p>}
      <button disabled={busy || dirty || unchanged} onClick={async () => {
        setBusy(true); lockEditor(true); setError('');
        try {
          await request(base + '/suggestions/' + result.id + '/apply', json('POST', { revision: result.revision }));
          setResult(null); await onApplied();
        } catch (e) { setError(message(e)); } finally { setBusy(false); lockEditor(false); }
      }}>Apply reviewed suggestion</button>
      <button disabled={busy} onClick={() => setResult(null)}>Dismiss</button>
    </div></>}
    <button disabled={busy} onClick={async () => {
      setBusy(true); setError('');
      try { setHistory(await request<Accepted[]>(base + '/' + kind + '/' + id + '/history')); }
      catch (e) { setError(message(e)); } finally { setBusy(false); }
    }}>View accepted suggestion history</button>
    {history && (history.length ? history.map(item => <details key={item.id}>
      <summary>Revision {item.revision} to {item.applied_revision}</summary>
      <div className="ai-comparison"><section><h3>Before</h3><MaterialPreview material={item.original} /></section>
        <section><h3>Accepted revision</h3><MaterialPreview material={item.proposed} /></section></div>
    </details>) : <p>No accepted suggestions for this draft.</p>)}
  </details>;
}
