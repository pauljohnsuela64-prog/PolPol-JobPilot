import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { request } from './api';
import { Profile } from './Profile';
import { Discovery } from './Discovery';
import './style.css';

const stages = {
  saved: 'Saved',
  applied: 'Applied',
  interview: 'Interview',
  offer: 'Offer',
  closed: 'Closed',
};
type Stage = keyof typeof stages;
const stageKeys = Object.keys(stages) as Stage[];
type Job = {
  job_id: number;
  title: string;
  company: string;
  location: string;
  status: Stage;
  created_at: string;
};

function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState<Stage | 'all'>('all');
  const [load, setLoad] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);

  async function refresh() {
    setLoad('loading');
    setError('');
    try {
      setJobs(await request<Job[]>('/api/jobs'));
      setLoad('ready');
    } catch (problem) {
      setLoad('failed');
      setError(problem instanceof Error ? problem.message : 'Unable to load jobs.');
    }
  }

  useEffect(() => { void refresh(); }, []);

  // Apply only the record returned by the server so counts stay in sync with saved data.
  async function save(body: object, jobId?: number) {
    setBusy(true);
    setError('');
    try {
      const job = await request<Job>(jobId === undefined ? '/api/jobs' : '/api/jobs/' + jobId, {
        method: jobId === undefined ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (jobId === undefined) {
        setJobs(current => [job, ...current]);
        setFilter('all');
        setAdding(false);
      } else {
        setJobs(current => current.map(item => item.job_id === job.job_id ? job : item));
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Unable to save changes.');
    } finally {
      setBusy(false);
    }
  }

  function addJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    if (!String(fields.title).trim() || !String(fields.company).trim()) {
      setError('Enter a job title and company.');
      return;
    }
    void save(fields);
  }

  const visibleJobs = filter === 'all' ? jobs : jobs.filter(job => job.status === filter);

  return (
    <main className="wrap">
        <section className="intro">
          <div>
            <h1>Application dashboard</h1>
            <p>Keep track of roles and where each application stands.</p>
          </div>
          <button className="primary" onClick={() => setAdding(value => !value)} aria-expanded={adding} aria-controls="add-job" disabled={load !== 'ready' || busy}>
            {adding ? 'Cancel' : '+ Add job'}
          </button>
        </section>
        {adding && <form className="panel form" id="add-job" onSubmit={addJob}>
          <h2>Add a job</h2>
          <div className="fields">
            <label>Job title<input name="title" autoFocus required maxLength={120} disabled={busy} /></label>
            <label>Company<input name="company" required maxLength={120} disabled={busy} /></label>
            <label>Location <span>(optional)</span><input name="location" maxLength={120} disabled={busy} /></label>
          </div>
          <button className="primary" disabled={busy}>{busy ? 'Saving...' : 'Save job'}</button>
        </form>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="stats" aria-label="Application totals">
          <div className="stat total"><span>Total jobs</span><strong>{load === 'ready' ? jobs.length : '–'}</strong></div>
          {stageKeys.map(stage => <div className="stat" key={stage}>
            <span><i className={stage} aria-hidden="true" />{stages[stage]}</span>
            <strong>{load === 'ready' ? jobs.filter(job => job.status === stage).length : '–'}</strong>
          </div>)}
        </div>
        <section className="panel pipeline" aria-labelledby="pipeline-heading">
          <div className="panel-head">
            <h2 id="pipeline-heading">Your jobs</h2>
            <div className="filter">
              <label htmlFor="stage-filter">Stage</label>
              <select id="stage-filter" value={filter} onChange={event => setFilter(event.target.value as Stage | 'all')}>
                <option value="all">All stages</option>
                {stageKeys.map(stage => <option key={stage} value={stage}>{stages[stage]}</option>)}
              </select>
            </div>
          </div>
          {load === 'loading' && <p className="empty" role="status">Loading jobs...</p>}
          {load === 'failed' && <div className="empty"><button onClick={refresh}>Retry loading jobs</button></div>}
          {load === 'ready' && visibleJobs.length === 0 && <div className="empty">
            <strong>{jobs.length ? 'No jobs at this stage' : 'No jobs tracked yet'}</strong>
            <p>{jobs.length ? 'Choose another stage to see more jobs.' : 'Add a job to start tracking your applications.'}</p>
          </div>}
          <ul className="jobs">
            {visibleJobs.map(job => <li key={job.job_id}>
              <div className="job-text">
                <strong>{job.title}</strong>
                <p>{job.company}{job.location && ' · ' + job.location}</p>
              </div>
              <select className={'stage ' + job.status} aria-label={'Stage for ' + job.title + ' at ' + job.company} value={job.status}
                disabled={busy} onChange={event => void save({ status: event.target.value }, job.job_id)}>
                {stageKeys.map(stage => <option key={stage} value={stage}>{stages[stage]}</option>)}
              </select>
            </li>)}
          </ul>
        </section>
    </main>
  );
}

function App() {
  const [route, setRoute] = useState(() => window.location.hash);

  // Hash links support browser Back and direct access without adding a router.
  useEffect(() => {
    const navigate = () => setRoute(window.location.hash);
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  return (
    <>
      <header className="top">
        <div className="wrap bar">
          <div className="brand"><span className="mark" aria-hidden="true">P</span> PolPol <span>JobPilot</span></div>
          <nav className="navigation" aria-label="Main navigation">
            <a href="#dashboard" aria-current={route !== '#profile' && route !== '#discovery' ? 'page' : undefined}>Dashboard</a>
            <a href="#profile" aria-current={route === '#profile' ? 'page' : undefined}>Profile / Preferences</a>
            <a href="#discovery" aria-current={route === '#discovery' ? 'page' : undefined}>Job Discovery</a>
          </nav>
          <span className="local">Local workspace</span>
        </div>
      </header>
      {route === '#discovery' ? <Discovery /> : route === '#profile' ? <Profile /> : <Dashboard />}
    </>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
