# PolPol JobPilot

PolPol JobPilot is a local job application tracker. Add roles by hand, filter by stage, and update each stage as your search progresses. Use Profile / Preferences to save your job-search preferences and manage one resume. Use Job Discovery to retrieve public openings from employer boards you pick. The dashboard and API run on your PC.

## Run on Windows

Install Node.js 24 LTS, Python 3.12 or newer, and PostgreSQL 18 with its command-line tools. The commands below use PowerShell and assume you start in the project folder. The initial dependency install needs internet access.

### Set up PostgreSQL

If PostgreSQL is installed in a different folder, adjust the executable path. When prompted, enter the PostgreSQL password you chose during installation.

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
& $psql -h 127.0.0.1 -U postgres -d postgres
```

For a new installation, create a dedicated local user and database at the `psql` prompt. If you already have a JobPilot database, keep it and skip the user and database creation. The password command prompts for a new password without putting it in shell history.

```sql
CREATE ROLE jobpilot LOGIN;
\password jobpilot
CREATE DATABASE jobpilot OWNER jobpilot;
\q
```

From the project folder, run the schema and migrations in order against the same database. These commands are safe to rerun. The Phase 3 migration adds two tables and preserves existing jobs, application stages, preferences, and resume metadata. Use your existing database and role names if they differ.

```powershell
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\schema.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\002_profile.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\003_discovery.sql
```

### Start the API

Open PowerShell in the project folder. Create the virtual environment on your first run. Run the install command when upgrading so the local PDF parser and HTTP client are available. Enter your `jobpilot` password when prompted and leave this window open.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
$password = Read-Host 'JobPilot database password' -AsSecureString
$escaped = [uri]::EscapeDataString(([System.Net.NetworkCredential]::new("", $password)).Password)
$env:DATABASE_URL = "postgresql://jobpilot:${escaped}@127.0.0.1:5432/jobpilot"
Remove-Variable password, escaped
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Check the API at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). It returns `{"status":"ok"}` when FastAPI is responding. The dashboard and Profile page check that the database is available. If they report a schema problem after upgrading, apply the migrations above and refresh.

### Start the dashboard

Open a second PowerShell terminal in the project folder.

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) in Edge. Choose **Dashboard** to manage jobs, **Profile / Preferences** to edit your profile, or **Job Discovery** to check employer boards. Press **Ctrl+C** in both terminal windows to stop the app. To restart, repeat the API password prompt and startup commands, then run `npm.cmd run dev` from `frontend`. You can skip the install commands on later starts.

## Run the checks

From the project folder, run the backend tests and frontend production build.

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd run build
```

The health and validation tests run without PostgreSQL. To run the full workflows, create a disposable test database owned by `jobpilot`. Apply all three SQL scripts to it as shown above, using the test database name. Set `DATABASE_URL` to the test connection string and then run `$env:JOBPILOT_TEST_DATABASE_URL = $env:DATABASE_URL` in the same terminal before pytest. The integration tests write preferences and resume metadata, so use a separate database from your real profile.

## Discover and save jobs

1. Open **Profile / Preferences**, fill in the job preferences you care about, and save them.
2. Open [Job Discovery](http://127.0.0.1:5173/#discovery). Under **Career boards**, choose the provider, enter a company label, and enter its board identifier. The identifier is the employer part of the career-board path. For example, use **Greenhouse**, **Stripe**, and `stripe` for the Stripe board, or **Lever**, **Spotify**, and `spotify` for the Spotify board. For a board hosted on `jobs.eu.lever.co`, choose **Lever (EU)**. Available openings change over time.
3. Click **Add career board**, then **Refresh jobs**. Each board reports how many posts it supplied or why it could not be reached. **Pause** stops checking a board and keeps its previously discovered listings.
4. Search by title, company, or location and use the platform and preference filters. Open **Preference details** to see which fields affected a result. **Original posting** opens the employer's job page in a new tab.
5. Click **Save to Dashboard** to create a tracked job at **Saved**. Visit **Dashboard** to change the stage.

Refresh checks only the boards you configure. JobPilot sends their identifiers to the public feed and keeps your resume and preferences local. Jobs stay in Discovery even when a board stops listing them, so check **Last seen** and the original posting if availability matters.

### How matching works

- A confirmed match has evidence for every selected preference. Titles and locations match any saved phrase in their respective lists. The skills check looks for every saved skill in the posting text. A mention is evidence for matching purposes and can appear outside the job requirements.
- Work arrangement uses the provider's structured field or an explicit Greenhouse workplace field. Experience uses explicit level labels in the title. Ambiguous wording is flagged as uncertain.
- Salary is compared only when the feed supplies a structured PHP range with a monthly pay period. Ranges spanning your minimum are uncertain. Other currencies and pay periods stay as reported. Missing salary, location, work arrangement, or dates are shown as unavailable. Posting dates are shown only when the feed provides a publication date.
- Lever boards are limited to 1,000 posts per refresh and Greenhouse boards to 2,500, subject to response size and time limits. Greenhouse job lists often omit salary. This version uses the list feeds and does not issue a separate request for each job.

Provider documentation is available in the [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) and [Lever Postings API](https://github.com/lever/postings-api).

### Optional real-feed smoke test

The regular automated suite mocks external feeds. To make read-only requests to the verified public Stripe and Spotify boards, run this from `backend` in PowerShell. It needs internet access and leaves the database untouched.

```powershell
$env:JOBPILOT_LIVE_FEEDS = '1'
..\.venv\Scripts\python.exe -m pytest -q tests/test_discovery.py -k live_public_board
Remove-Item Env:JOBPILOT_LIVE_FEEDS
```

## Manual acceptance checklist

- [ ] Open **Dashboard** and check that your Phase 1 records and stages are present. Add a test job, change its stage, and filter to that stage.
- [ ] Open **Profile / Preferences**. Add two job titles using **Add title**, enter multiple locations and skills on separate lines, choose an arrangement and experience level, and enter a salary in PHP (for example, 30000).
- [ ] Click **Save preferences** and confirm the success message. Refresh the page, then stop and restart the API and confirm the preferences are still there.
- [ ] Upload a PDF smaller than 5 MB. Confirm the filename and upload date. Replace it with a different PDF and check the new filename. Delete it and confirm that the page says there is no resume.
- [ ] Try uploading a text file renamed to `.pdf` and a file larger than 5 MB. Confirm that both are rejected and that any previously saved resume remains listed.
- [ ] Add a confirmed public board and refresh. Check the title, company, source, link, and discovery date. Check that unavailable fields say **Not provided**.
- [ ] Set a preference, then filter the results by **Confirmed match**, **Uncertain match**, and **Missing information**. Inspect the preference details on results.
- [ ] Refresh again. Verify that jobs appear once, their original discovery dates are preserved, and **Last seen** updates.
- [ ] Save a discovery result. Confirm the button changes to **Saved to Dashboard** and the Dashboard contains one tracked application for it. Return to Discovery and check that its saved state persists.
- [ ] Add a deliberately invalid board identifier such as a full URL, then try a well-formed board slug that does not exist. Check that the form rejects the URL and refresh reports a clear error for the unavailable board while other boards still work.
- [ ] Resize the browser to a phone-width window and confirm that all three pages remain usable.

## Local resume storage

The app accepts one readable, unencrypted PDF at a time, up to 5 MB (5,242,880 bytes). On Windows, resume files are stored in `%LOCALAPPDATA%\PolPolJobPilot\resumes`. The API can use a different directory through `JOBPILOT_RESUME_DIR`. Keep that directory outside the project and use the same setting on each restart.

The Profile page reads the filename and upload date from PostgreSQL. Resume files use generated names and are accessible through the local folder. The app has no file download route. Moving the database alone preserves metadata, so keep the resume directory with the database if you move this installation.

The migrations only add tables. If you temporarily run an earlier version, leave the new tables in place so you can resume later.

## How the parts fit together

- The React dashboard sends requests to FastAPI through Vite's local proxy.
- PostgreSQL stores the role details in `jobs` and its stage in `applications`. Each application row points to one job by ID.
- FastAPI validates the form input before writing to PostgreSQL. A transaction groups the two inserts so they succeed or roll back together.
