# PolPol JobPilot

PolPol JobPilot is a local job application tracker. Add roles by hand, filter by stage, and update each stage as your search progresses. Use Profile / Preferences to save your job-search preferences and manage one resume. The dashboard and API run on your PC.

## Run on Windows

Install Node.js 24 LTS, Python 3.12 or newer, and PostgreSQL 18 with its command-line tools. The commands below use PowerShell and assume you start in the project folder. The initial dependency install needs internet access.

### Set up PostgreSQL

If PostgreSQL is installed in a different folder, adjust the executable path. When prompted, enter the PostgreSQL password you chose during installation.

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
& $psql -h 127.0.0.1 -U postgres -d postgres
```

For a new installation, create a dedicated local user and database at the `psql` prompt. If you already have a Phase 1 database, keep it and skip the user and database creation. The password command prompts for a new password without putting it in shell history.

```sql
CREATE ROLE jobpilot LOGIN;
\password jobpilot
CREATE DATABASE jobpilot OWNER jobpilot;
\q
```

From the project folder, initialize the Phase 1 tables and apply the Phase 2 migration to the same database. Both commands are safe to rerun, including on an existing Phase 1 database. Use your existing database and role names if they differ.

```powershell
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\schema.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\002_profile.sql
```

### Start the API

Open PowerShell in the project folder. Create the virtual environment on your first run. Run the install command when upgrading from Phase 1 so the local PDF parser is available. Enter your `jobpilot` password when prompted and leave this window open.

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

Check the API at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). It returns `{"status":"ok"}` when FastAPI is responding. The dashboard and Profile page check that the database is available. If they report a schema problem after upgrading, apply the Phase 2 migration and refresh.

### Start the dashboard

Open a second PowerShell terminal in the project folder.

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) in Edge. Choose **Dashboard** to manage jobs or **Profile / Preferences** to edit your profile. Press **Ctrl+C** in both terminal windows to stop the app. To restart, repeat the API password prompt and startup commands, then run `npm.cmd run dev` from `frontend`. You can skip the install commands on later starts.

## Run the checks

From the project folder, run the backend tests and frontend production build.

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd run build
```

The health and validation tests run without PostgreSQL. To run the full workflows, create a disposable test database owned by `jobpilot`. Apply both SQL scripts to it as shown above, using the test database name. Set `DATABASE_URL` to the test connection string and then run `$env:JOBPILOT_TEST_DATABASE_URL = $env:DATABASE_URL` in the same terminal before pytest. The integration tests write preferences and resume metadata, so use a separate database from your real profile.

## Manual acceptance checklist

- [ ] Open **Dashboard** and check that your Phase 1 records and stages are present. Add a test job, change its stage, and filter to that stage.
- [ ] Open **Profile / Preferences**. Add two job titles using **Add title**, enter multiple locations and skills on separate lines, choose an arrangement and experience level, and enter a salary in PHP (for example, 30000).
- [ ] Click **Save preferences** and confirm the success message. Refresh the page, then stop and restart the API and confirm the preferences are still there.
- [ ] Upload a PDF smaller than 5 MB. Confirm the filename and upload date. Replace it with a different PDF and check the new filename. Delete it and confirm that the page says there is no resume.
- [ ] Try uploading a text file renamed to `.pdf` and a file larger than 5 MB. Confirm that both are rejected and that any previously saved resume remains listed.
- [ ] Resize the browser to a phone-width window and confirm that both pages remain usable.

## Local resume storage

The app accepts one readable, unencrypted PDF at a time, up to 5 MB (5,242,880 bytes). On Windows, resume files are stored in `%LOCALAPPDATA%\PolPolJobPilot\resumes`. The API can use a different directory through `JOBPILOT_RESUME_DIR`. Keep that directory outside the project and use the same setting on each restart.

The Profile page reads the filename and upload date from PostgreSQL. Resume files use generated names and are accessible through the local folder. The app has no file download route. Moving the database alone preserves metadata, so keep the resume directory with the database if you move this installation.

The migration only adds the profile table. If you switch back to Phase 1 code, leave the table in place so you can resume using Phase 2 later.

## How the parts fit together

- The React dashboard sends requests to FastAPI through Vite's local proxy.
- PostgreSQL stores the role details in `jobs` and its stage in `applications`. Each application row points to one job by ID.
- FastAPI validates the form input before writing to PostgreSQL. A transaction groups the two inserts so they succeed or roll back together.
