# PolPol JobPilot

PolPol JobPilot is a local job application tracker. Add roles by hand, filter by stage, and update each stage as your search progresses. The dashboard and API run on your PC, and records are stored in your local PostgreSQL database.

## Run on Windows

Install Node.js 24 LTS, Python 3.12 or newer, and PostgreSQL 18 with its command-line tools. The commands below use PowerShell and assume you start in the project folder. The initial dependency install needs internet access.

### Set up PostgreSQL

If PostgreSQL is installed in a different folder, adjust the executable path. When prompted, enter the PostgreSQL password you chose during installation.

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
& $psql -h 127.0.0.1 -U postgres -d postgres
```

At the `psql` prompt, create a dedicated local user and database. The password command prompts for a new password without putting it in shell history.

```sql
CREATE ROLE jobpilot LOGIN;
\password jobpilot
CREATE DATABASE jobpilot OWNER jobpilot;
\q
```

Initialize the tables once. The schema script is safe to run again for the Phase 1 schema.

```powershell
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\schema.sql
```

### Start the API

In PowerShell, create a virtual environment so this project's Python packages are kept separate from other projects. Enter the same `jobpilot` password when prompted. Keep this terminal open.

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

Check the API at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). It returns `{"status":"ok"}` when FastAPI is responding. Opening the dashboard checks the database connection.

### Start the dashboard

Open a second PowerShell terminal in the project folder.

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173), select **Add job**, and enter a title and company. A new record starts at **Saved**. Use the dropdown beside the job to change its stage. Press Ctrl+C in each terminal to stop the servers. PostgreSQL keeps your data between sessions.

## Run the checks

From the project folder, run the backend tests and frontend production build.

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd run build
```

The health and validation tests run without PostgreSQL. To include the create, update, and list workflow, create a separate test database owned by `jobpilot`, apply `backend/schema.sql` to it, and set `DATABASE_URL` to that database in the test terminal before running pytest. The workflow removes the job it creates.

## How the parts fit together

- The React dashboard sends requests to FastAPI through Vite's local proxy.
- PostgreSQL stores the role details in `jobs` and its stage in `applications`. Each application row points to one job by ID.
- FastAPI validates the form input before writing to PostgreSQL. A transaction groups the two inserts so they succeed or roll back together.
