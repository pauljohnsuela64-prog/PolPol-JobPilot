import { MasterEditor } from './Builder';
import { useEffect, useState, type FormEvent } from 'react';
import { request, send } from './api';

type Preferences = {
  job_titles: string[];
  locations: string[];
  work_arrangement: 'any' | 'remote' | 'hybrid' | 'on-site';
  minimum_salary_php: number | null;
  experience_level: 'internship' | 'entry-level' | 'junior' | null;
  technical_skills: string[];
};
type Resume = { filename: string; uploaded_at: string };
type ProfileData = { preferences: Preferences; resume: Resume | null };
type ListKey = 'locations' | 'technical_skills';

function SelectionList({ label, example, name, values }: { label: string; example: string; name: ListKey; values: string[] }) {
  return (
    <label className="profile-field">
      {label}
      <textarea name={name} placeholder={example} rows={3} defaultValue={values.join('\n')} aria-describedby={name + '-help'} />
      <small id={name + '-help'}>Enter one per line, up to 30.</small>
    </label>
  );
}

// Convert the editable lines into a list and keep the first occurrence of each item.
function selectedValues(value: FormDataEntryValue | null) {
  return [...new Set(String(value ?? '').split(/\r?\n/).map(item => item.trim()).filter(Boolean))];
}

export function Profile() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [titles, setTitles] = useState<string[]>([]);
  const [titleDraft, setTitleDraft] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      const saved = await request<ProfileData>('/api/profile');
      setProfile(saved);
      setTitles(saved.preferences.job_titles);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not load your profile.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function update(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError('');
    setSuccess('');
    try {
      await action();
      setSuccess(message);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'The change could not be saved.');
    } finally {
      setBusy(false);
    }
  }

  function addTitle() {
    const title = titleDraft.trim();
    if (!title) return;
    if (titles.length >= 30 && !titles.includes(title)) {
      setSuccess('');
      setError('Choose up to 30 job titles.');
      return;
    }
    setTitles(current => [...new Set([...current, title])]);
    setTitleDraft('');
    setError('');
    setSuccess('');
  }

  function savePreferences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const salary = fields.get('minimum_salary_php');
    const values = {
      job_titles: [...new Set([...titles, titleDraft.trim()].filter(Boolean))],
      locations: selectedValues(fields.get('locations')),
      technical_skills: selectedValues(fields.get('technical_skills')),
      work_arrangement: fields.get('work_arrangement'),
      experience_level: fields.get('experience_level') || null,
      minimum_salary_php: salary === '' ? null : Number(salary),
    };
    const invalidList = [values.job_titles, values.locations, values.technical_skills]
      .some(list => list.length > 30 || list.some(item => item.length > 120));
    if (invalidList) {
      setSuccess('');
      setError('Enter up to 30 items per list. Each item can be up to 120 characters.');
      return;
    }
    void update(async () => {
      const preferences = await request<Preferences>('/api/profile/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      setProfile(current => current && { ...current, preferences });
      setTitles(preferences.job_titles);
      setTitleDraft('');
    }, 'Preferences saved.');
  }

  function uploadResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get('resume');
    if (!(file instanceof File) || !file.name || file.size > 5 * 1024 * 1024) {
      setSuccess('');
      setError('Choose a PDF file no larger than 5 MB.');
      return;
    }
    void update(async () => {
      const resume = await request<Resume>('/api/profile/resume', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/pdf', 'X-Resume-Filename': encodeURIComponent(file.name) },
        body: file,
      });
      setProfile(current => current && { ...current, resume });
      form.reset();
    }, profile?.resume ? 'Resume replaced.' : 'Resume uploaded.');
  }

  function deleteResume() {
    if (!window.confirm('Delete your saved resume?')) return;
    void update(async () => {
      await send('/api/profile/resume', { method: 'DELETE' });
      setProfile(current => current && { ...current, resume: null });
    }, 'Resume deleted.');
  }

  return (
    <main className="wrap profile">
      <section className="intro">
        <div>
          <h1>Profile and preferences</h1>
          <p>Manage your resume and save the kinds of jobs you want to find.</p>
        </div>
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      {success && <p className="success" role="status">{success}</p>}
      {loading && <p role="status">Loading profile...</p>}
      {!loading && !profile && <button onClick={load}>Retry loading profile</button>}
      {profile && (
        <div className="profile-grid">
          <section className="panel form" aria-labelledby="resume-heading">
            <h2 id="resume-heading">Resume</h2>
            {profile.resume ? (
              <div className="resume-summary">
                <strong>{profile.resume.filename}</strong>
                <p>Uploaded <time dateTime={profile.resume.uploaded_at}>{new Date(profile.resume.uploaded_at).toLocaleString()}</time></p>
              </div>
            ) : <p>No resume uploaded yet.</p>}
            <form className="resume-form" onSubmit={uploadResume}>
              <label className="profile-field">
                Choose PDF
                <input type="file" name="resume" accept=".pdf,application/pdf" required disabled={busy} aria-describedby="resume-help" />
                <small id="resume-help">PDF only, up to 5 MB.</small>
              </label>
              <div className="profile-actions">
                <button className="primary" disabled={busy}>{profile.resume ? 'Replace resume' : 'Upload resume'}</button>
                {profile.resume && <button type="button" onClick={deleteResume} disabled={busy}>Delete resume</button>}
              </div>
            </form>
          </section>
          <section className="panel form" aria-labelledby="preferences-heading">
            <h2 id="preferences-heading">Job preferences</h2>
            <form onSubmit={savePreferences}>
              <fieldset className="preference-fields" disabled={busy}>
                <div>
                  <label htmlFor="preferred-titles">Preferred job titles</label>
                  <div className="title-entry">
                    <input id="preferred-titles" list="job-title-suggestions" value={titleDraft} maxLength={120}
                      aria-describedby="title-help" onChange={event => setTitleDraft(event.target.value)}
                      onKeyDown={event => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          addTitle();
                        }
                      }} />
                    <datalist id="job-title-suggestions">
                      <option value="Junior Developer" />
                      <option value="QA Engineer" />
                      <option value="IT Support Specialist" />
                      <option value="Data Analyst" />
                    </datalist>
                    <button type="button" onClick={addTitle}>Add title</button>
                  </div>
                  <small className="hint" id="title-help">Choose a suggestion or type your own. You can add up to 30.</small>
                  {titles.length > 0 && <ul className="selected-titles">
                    {titles.map(title => (
                      <li key={title}>
                        <button type="button" aria-label={'Remove ' + title}
                          onClick={() => setTitles(current => current.filter(item => item !== title))}>
                          {title} <span aria-hidden="true">&times;</span>
                        </button>
                      </li>
                    ))}
                  </ul>}
                </div>
                <SelectionList label="Preferred locations" example="Manila" name="locations" values={profile.preferences.locations} />
                <label className="profile-field" htmlFor="work-arrangement">Work arrangement</label>
                <select id="work-arrangement" name="work_arrangement" defaultValue={profile.preferences.work_arrangement}>
                  <option value="any">Any</option>
                  <option value="remote">Remote</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="on-site">On-site</option>
                </select>
                <label className="profile-field">
                  Minimum monthly salary (PHP)
                  <input name="minimum_salary_php" type="number" min="0" max="999999999" step="1" defaultValue={profile.preferences.minimum_salary_php ?? ''} />
                  <small>Leave blank if you have no minimum.</small>
                </label>
                <label className="profile-field" htmlFor="experience-level">Experience level</label>
                <select id="experience-level" name="experience_level" defaultValue={profile.preferences.experience_level ?? ''}>
                  <option value="">Choose a level</option>
                  <option value="internship">Internship</option>
                  <option value="entry-level">Entry-level</option>
                  <option value="junior">Junior</option>
                </select>
                <SelectionList label="Technical skills" example="Python" name="technical_skills" values={profile.preferences.technical_skills} />
                <button className="primary">Save preferences</button>
              </fieldset>
            </form>
          </section>
        </div>
      )}
    <details><summary>Edit verified career information</summary><MasterEditor /></details>
    </main>
  );
}
