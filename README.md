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
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\005_resume_builder.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\006_application_preparation.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\007_application_queue.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\008_discovery_schedule.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\008_shortlist.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\009_ai_settings.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\009_ai_suggestions.sql
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\010_application_assistant.sql
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

The health and validation tests run without PostgreSQL. To run the full workflows, create a disposable test database owned by `jobpilot`. Apply all SQL scripts to it as shown above, using the test database name. Set `DATABASE_URL` to the test connection string and then run `$env:JOBPILOT_TEST_DATABASE_URL = $env:DATABASE_URL` in the same terminal before pytest. The integration tests write preferences and resume metadata, so use a separate database from your real profile.

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

## Phase 4 compatibility

Open Job Discovery and choose **Best matches first**, then **Apply filters**. Open **Compatibility details** on a posting to see the evidence. JobPilot recalculates scores locally using the current profile each time results load. Phase 4 uses the existing Windows startup commands, dependencies and tables.

Title carries 25 points, skills 30, experience level 15, location 10, work arrangement 10 and salary 10. The score is the percentage of applicable weight supported by matching evidence. Unset preferences are excluded, except that explicit posting skills can always be compared with confirmed skills. Unknowns and mismatches earn zero points. Skills earn proportional credit for the share of recognized required or preferred skills that you confirmed. Both categories have equal weight. Evidence confidence measures resolved weight out of all 100 points, including confirmed mismatches. A single matching title can therefore score 100 with only 25% evidence confidence. Neither measure estimates your chance of getting hired.

The matcher recognizes whole skill names, saved skill phrases and selected aliases such as JS and Postgres. Explicit headings and adjacent qualifiers distinguish required and preferred skills. Other mentions are shown for context. Unverified skills are possible gaps to check against the original posting. Years of experience in the description are shown as evidence; your saved experience level describes the roles you prefer. Resume contents are unused. Normalized descriptions can lose formatting and these rules can miss qualifications.

Best matches first ranks by score multiplied by evidence confidence. Highest score ranks by score, then confidence. Date sorts use the discovery or last-seen timestamp. Ties use the listing ID. All sorts apply to the filtered results before the 50-job page is selected.

### Manual Phase 4 checks

- [ ] Save a skill and a preferred title under Profile, then open Job Discovery and expand Compatibility details.
- [ ] Compare the excerpt with the original posting. Check the labels for confirmed, unverified, mentioned and missing evidence.
- [ ] Switch between all four sorts with Apply filters. Check a second page when available.
- [ ] Change a preference, revisit Discovery, and verify that the score updates. Save a listing and check its Dashboard stage.
- [ ] Repeat at a phone width. Use the Windows backend test and frontend build commands above for automated checks.


## Phase 5 Resume Builder on Windows

Use the Windows setup commands above. For an existing install, stop the API, run the pip install command again for python-docx and ReportLab, apply 005_resume_builder.sql to the same database, then restart FastAPI and Vite. The migration creates the master resume and draft tables and is safe to rerun.

1. Under Profile, expand **Edit verified career information**. Enter your name, contact details and verified career facts. Put each education item, role, project, achievement, certification or skill on its own line. Keep related employers, dates and outcomes together. Save the master.
2. In Discovery, choose **Tailor Resume** on a listing. You can also select an opening directly in Resume Builder. Generate a draft. Each generation creates a new history entry with a snapshot of the opening, master and matching evidence.
3. Review the preview and the separate suggestions. Edit the text and save your edits. Approve the saved revision and choose DOCX or PDF. Save the download outside the project folder. Use Draft history to reopen earlier generations or Delete draft to remove the selected one.

Tailoring orders existing facts by recognized relevant skill terms. It uses the approved summary and can add a short sentence naming verified relevant skills. A posting without a description produces a master-based draft. Uploaded PDFs are independent of this editor. Generated files are rendered in memory and downloaded directly. Long resumes can paginate differently between formats; font coverage depends on the installed fonts. The plain-text editor supports headings and simple bullet lines. Review wording, dates, and page breaks before sharing.

### Manual acceptance

- Save two projects and experiences, reload Profile, and verify their text.
- Generate twice for one job. Check the order of relevant entries, the suggestions, and both history entries.
- Edit one draft, save, approve, and download both formats. Compare the text and confirm the original uploaded resume is still listed.
- Delete one draft and verify the other still opens. Repeat the review at a narrow window width.

## Phase 6 Application Preparation on Windows

**Upgrade an existing Phase 5 installation.** Stop FastAPI, then back up PostgreSQL and the uploaded resume directory together. Run these commands from the project folder in PowerShell. Adjust the PostgreSQL installation path and connection details for your installation. Choose a private local backup location with enough free space.

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$backup = Join-Path $env:LOCALAPPDATA ("JobPilot-backup-" + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop
& "$pgBin\pg_dump.exe" -h 127.0.0.1 -U jobpilot -d jobpilot -Fc -f "$backup\jobpilot.dump"
if ($LASTEXITCODE) { throw 'Database backup failed.' }
& "$pgBin\pg_restore.exe" --list "$backup\jobpilot.dump" | Out-Null
if ($LASTEXITCODE) { throw 'Cannot read the backup archive.' }
$resumes = if ($env:JOBPILOT_RESUME_DIR) { $env:JOBPILOT_RESUME_DIR } else { Join-Path $env:LOCALAPPDATA 'PolPolJobPilot\resumes' }
if (Test-Path -LiteralPath $resumes) { Copy-Item -LiteralPath $resumes -Destination "$backup\resumes" -Recurse -ErrorAction Stop }
& "$pgBin\psql.exe" -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\006_application_preparation.sql
if ($LASTEXITCODE) { throw 'Migration failed.' }
```

Migration 006 adds a preparation table and index and can be rerun. Start FastAPI and Vite using the two Windows terminals described above. For a restore rehearsal, create a separate empty database owned by the same role, restore the archive with `pg_restore --exit-on-error -d <empty_database>` and the same host and user options, and point a test installation at the restored database and backed-up resume directory.

### Prepare and verify a packet

1. Save a verified master under Profile. For a synthetic check, enter Python as a skill and a Python catalog as a project. Choose a Discovery listing that mentions Python. Generate a tailored resume in Resume Builder if you want to attach one.
2. Open Application Preparation from that listing. Check the selected role, expand the original posting details, and choose the tailored resume version. Enter one screening question per line. Try “What are your relevant skills?” and “When can you start?”.
3. Generate the record. Compare the letter and suggested answers with your master facts. The start-date answer should read **Needs your input.** Edit the text, complete your checklist, and save. Approve the saved revision, then download the letter as DOCX and PDF and the answers and checklist as text files.
4. Change a checklist item and save again. Confirm that exports require fresh approval. Generate a second record for the same job, reload the page, and reopen both from Preparation history. Delete one and verify that the other record, its resume snapshot, and the Dashboard stage are intact.
5. Repeat at a 360px viewport. Check that the editor, approval controls, and downloads remain reachable.

**Limitations.** Suggestions use local phrase matching against manually verified master skills and related project, experience, and achievement entries. Salary, availability, work authorization, unfamiliar wording, and compound questions require your input. A sparse master can produce a short letter with no personal qualifications. Review every claim and fill unresolved answers before sharing. Approval records your review of one saved revision. The checklist can represent work still outstanding. History keeps separate generated records, each with its latest saved edits and its original job and selected resume snapshots. Downloaded files stay in the location you choose. Submitting an application and updating its Dashboard stage are manual steps.

## Phase 7 Controlled Application Workflow on Windows

**Upgrade from Phase 6.** Stop the API and take the private database and resume-folder backup described under Phase 6. Use the same PostgreSQL host, role and database that your API uses. From the project folder, apply migration 007 in PowerShell:

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
& $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f .\backend\migrations\007_application_queue.sql
if ($LASTEXITCODE) { throw 'Queue migration failed.' }
```

Migration 007 adds a nullable application-approval revision to saved resumes and a table for submission history. It can be rerun. Earlier downloads and Dashboard stages do not establish a verified submission time or a durable resume approval. Start FastAPI and Vite as described under **Run on Windows**, then open **Application Queue** in Edge.

### Manual acceptance

1. In Queue, find an unsaved Discovery opening by company or title. Check its tracker stage, materials and original hostname. **Open Official Application** opens the stored, validated posting in a new tab. Opening the link leaves the queue and Dashboard unchanged. A manual Dashboard job can use the same recording workflow without an employer link.
2. In Resume Builder, save and approve a resume revision **for applications**. In Application Prep, create a packet with that resume and approve the saved packet. Revisit Queue and filter by **Ready**. Edit the resume and confirm the entry becomes **Draft**. Approving the edited resume still requires a new approved packet containing that revision. Editing the packet also clears readiness.
3. After submitting to the employer yourself, choose **Record manual submission**. Enter the local submission time and channel, plus a reference, notes, or follow-up date if wanted. Confirm the checkbox and save. Verify one entry in submission history and one Dashboard job. A previously untracked or Saved job becomes Applied. A job already at Interview, Offer, or Closed retains that stage.
4. Reopen the entry from Dashboard. Choose **Correct submission details**, change the reference, add a correction note and confirm. Expand submission history to see both versions. Set a follow-up date of today or earlier and check the **Follow-up** filter. Clear it or choose a future date to return to **Submitted**.
5. Repeat at 360px. Try a manual job with a later tracker stage and no queue history. Review the prior-submission warning before deciding whether to log it.

**Limitations.** Ready describes the latest preparation record and its attached resume revision. You can record a submission made outside JobPilot even when its local materials are incomplete. A validated posting can expire or redirect at the employer. Similar company and title pairs produce a warning that you resolve manually. JobPilot cannot verify activity on another board, in another browser, or by email. Follow-up is a date-based queue view. Enter only the reference and notes you need, and keep account credentials out of these fields.

## Phase 8 Automatic Discovery and Smart Shortlist

**Windows upgrade from Phase 7.** Stop FastAPI. Make the local database and uploaded-resume backup described under Phase 6. Apply both Phase 8 migrations to the same database, then restart FastAPI and Vite using the Windows startup steps above. Both migrations can be rerun. Existing board, review, tracking and application data remain in place.

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
foreach ($file in @('008_discovery_schedule.sql', '008_shortlist.sql')) {
    & $psql -h 127.0.0.1 -U jobpilot -d jobpilot -v ON_ERROR_STOP=1 -f ".\backend\migrations\$file"
    if ($LASTEXITCODE) { throw "Migration failed for $file" }
}
```

**Scheduling.** Set up public boards in Job Discovery, then open Smart Shortlist. Automatic refresh starts paused with a 60-minute interval. Choose 15 minutes to 7 days and start it. Starting makes the first refresh due immediately. The API periodically checks the saved due time. It fetches enabled boards using the same Greenhouse and Lever clients as manual refresh. Match scoring uses local preferences. Providers receive only public board requests. A database lock keeps a manual refresh and scheduled refresh from running at the same time.

The browser can close while the API continues working. While the API is stopped or Windows is asleep, refreshes wait. On restart the worker performs one overdue refresh and schedules the next interval from completion. Pausing prevents future scheduled runs. A refresh already underway can finish. Check last run, next run and activity for results, including individual board failures. A listing can remain visible after the employer closes it.

**Reading the shortlist.** Both the score and evidence confidence must meet your saved minimums. For example, a title match alone can score 100 while covering only 25 points of evidence. Setting confidence above 25 excludes that title-only result. The list uses current preferences and thresholds across saved Discovery listings. At refresh, the first qualifying match for each stable listing ID creates one activity entry. Changing preferences can bring an older listing into the shortlist. Review state is saved separately from the Dashboard stage.

### Optional Windows Task Scheduler startup

Use this option if you want the local API to start when you sign in. JobPilot controls the refresh interval. Task Scheduler only starts the API. Configure this under your own Windows account, with **Run only when user is logged on** and ordinary user privileges.

1. Configure PostgreSQL password-file authentication for your existing JobPilot role in your private account profile. On Windows the default file is `%APPDATA%\postgresql\pgpass.conf`. Restrict access to your account and use a specific host, port, database, and role. The password stays out of the repository, task arguments and logs. [PostgreSQL password-file instructions](https://www.postgresql.org/docs/18/libpq-pgpass.html)
2. Under your **user** environment variables, set `DATABASE_URL` to the password-free connection string `postgresql://jobpilot@127.0.0.1:5432/jobpilot`, adjusting the names if needed. Preserve your existing `JOBPILOT_RESUME_DIR` setting if you use a custom directory. Sign out and sign back in so the task receives the updated environment.
3. In Task Scheduler choose **Create Task** with an **At log on** trigger for your account. For **Start a program**, use the full path to `C:\PolPol-JobPilot\.venv\Scripts\python.exe`. Set arguments to `-m uvicorn main:app --host 127.0.0.1 --port 8000` and **Start in** to `C:\PolPol-JobPilot\backend`. Adjust both paths for your install. Select **Do not start a new instance** if the task is already running. For long-running use, clear any task time limit. [Microsoft multiple-instance settings](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-multipleinstances)
4. Stop any manually started API on port 8000, run the task once, and verify the health endpoint and saved schedule from the dashboard. Start Vite separately for browser access. Disable the task to prevent future sign-in launches. To stop a currently running API, end that task as well.

### Manual acceptance

1. Save a target title and relevant skills in Profile. Configure an enabled board and open Smart Shortlist. Start a schedule, let it finish, and verify last run, next run and activity. Pause it, reload, and confirm it stays paused.
2. Choose score and confidence thresholds on either side of a known match. Check sorting, search and reviewed filters. Mark the listing reviewed, reload, and confirm the review persists.
3. Refresh the same board again. Verify one listing and one new-match notification for the same opening. Open it in the existing application workflow and check its Dashboard stage.
4. Try an unavailable board alongside a working one. Confirm the error identifies the failing board and successful results remain available. Pause or remove the test configuration when done.
5. With automatic refresh enabled, stop the API until a run is due, then restart it. Check that it runs once and sets a future due time. Repeat the shortlist review at a 360px browser width.

The **New match** label identifies an unreviewed opening first discovered in the last 24 hours. Older unreviewed openings remain eligible. The activity panel shows the latest 100 events. The scheduler checks for due work every 15 seconds. Refreshes use a 15-second deadline per board and persist provider cooldowns after a rate-limit response.

## Phase 9 — optional local AI

**Upgrade from Phase 8.** Stop FastAPI, take the database and resume-folder backup described under Phase 6, and run the two `009_` migration commands under **Set up PostgreSQL**. They add settings and suggestion storage. Restart the API and frontend. AI starts disabled.

### Connect Ollama on Windows

1. Open PowerShell and run `ollama --version`. If you want AI and the command is missing, use the [official Windows setup](https://docs.ollama.com/windows).
2. Quit any running Ollama instance from the tray. Start a local-only server in a PowerShell window and leave the window open. These settings apply to that server process. [Ollama local configuration](https://docs.ollama.com/faq)

   ```powershell
   $env:OLLAMA_HOST = '127.0.0.1:11434'
   $env:OLLAMA_NO_CLOUD = '1'
   ollama serve
   ```

3. In another terminal, run `ollama list`. On the inspected machine, the existing `qwen3.5:4b` is a reasonable first choice for the RTX 4060 Laptop GPU with 8 GiB VRAM and 15.7 GiB system RAM. Loading time and available memory affect response time. If the list is empty, keep using the rule-based workflows until you choose to install a local model yourself. [Model details](https://ollama.com/library/qwen3.5:4b)
4. In **AI Settings**, save the numeric loopback endpoint `http://127.0.0.1:11434` with assistance disabled to discover installed models. Select a model, enable assistance, and save again. A ready status confirms that Ollama reports the model installed and local. An error here leaves ordinary matching and document preparation available.

A cold model can take longer than the 60-second generation timeout. If that happens, open the model shown by `ollama list` with `ollama run qwen3.5:4b` (substitute the installed name), wait for the prompt, then enter `/bye` and retry in JobPilot. Using a name absent from the installed list can trigger an Ollama download.

### Privacy and limitations

FastAPI sends job text and selected master-resume fields to the configured local Ollama process when you request assistance. The application trusts the local process you configure. Point it directly at Ollama. Model requests bypass environment proxies, reject redirects and check local model metadata before sending the prompt. JobPilot has no model-download, cloud inference, or application-submission action. Existing public employer-board refreshes still use the network.

The model selects evidence by identifier. The backend resolves accepted identifiers to source text and composes documents using verified master-resume entries. Review each match in context. A possible gap means the saved resume lacks supporting evidence, and a quoted job requirement is an employer statement. Accuracy depends on the master resume you confirmed. Unsupported screening questions, including compensation or availability, need your input. AI suggestions can change the emphasis or order of existing facts. Rule-based suggestions remain available when Ollama is disabled, times out, returns unusable output, or lacks the selected model.

### Manual acceptance in Edge

1. Use a disposable database with all migrations. Save a master resume with SQL experience. Choose a discovered job mentioning SQL and a skill absent from the master. With AI disabled, run **Job analysis** and check the fallback label and source excerpts.
2. Enable a local model in **AI Settings**, repeat the analysis, and compare the employer's wording with the master-resume evidence. A requested skill alone should never become an applicant qualification.
3. Create a Resume Builder draft and an Application Preparation draft. Include a technical-skills question and a salary or availability question. Generate an assisted suggestion for each. Compare the saved and proposed versions, dismiss one, and reopen the draft to verify its content and approval.
4. Apply a reviewed suggestion that changes content. Reopen the draft, check the revision history, and confirm it needs approval again. Open the same draft in two tabs, change it in one, and try applying the older preview in the other.
5. Stop Ollama and request assistance. Check the fallback label, generate a rule-based draft, and verify that reviewing, editing, and exporting still follow the existing approval flow. Repeat the comparison on a narrow mobile viewport.

## Phase 10 — Authorized Application Assistant

The assistant turns a Ready job into a field-by-field package. It rechecks the current resume approval, the latest approved preparation packet, and existing duplicate warnings. The optional Edge companion fills the bundled practice form. For other forms, review and copy individual values from the workspace.

### Windows setup

1. Back up the existing database as described under Phase 6. For an installation already on Phase 9, run the single `010_application_assistant.sql` command under **Set up PostgreSQL**. This additive migration creates handoff and activity tables. Start FastAPI and Vite as usual. Ollama is optional.
2. From the project root, start the practice form in a separate PowerShell window. Serve only the synthetic-form directory. Open the exact address `http://127.0.0.1:4179/application.html` in desktop Microsoft Edge.

   ```powershell
   .\.venv\Scripts\python.exe -m http.server 4179 --bind 127.0.0.1 --directory .\synthetic-form
   ```

3. In `edge://extensions`, turn on **Developer mode**, choose **Load unpacked**, and select the project's `extension` folder containing `manifest.json`. Its ID should be `ienannceaienjfffohgjidcfcmjfnlki`. The companion expects FastAPI at `http://127.0.0.1:8000`. [Microsoft Edge installation instructions](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading)

### Review, fill and copy

Open **Application Assistant**, or follow the assistant link from a Ready queue entry. Contact details, education and experience must appear in both the verified master and the approved resume. Ambiguous contact values and missing answers appear as **Needs your input.** The approved preparation packet supplies the cover letter and reviewed screening answers. Review the proposed values and check the confirmation box.

For manual entry, choose **Copy** beside a supported value and paste it yourself into the intended field. Use manual selection if clipboard access is unavailable. For the practice form, choose **Create handoff code**, open the practice tab, invoke the Edge extension from its toolbar, paste the code, and choose **Preview fields**. Select individual fields and choose **Confirm and fill selected fields**. A new code replaces any earlier unclaimed code for that job. Each code expires in five minutes and authorizes one fill attempt.

The companion supports empty full-name, email, phone, experience and education fields, plus the exact reviewed questions “What are your relevant skills?” and “Describe a relevant project.” Cover letters and other reviewed questions are available for manual copy. The companion has temporary access to the active tab after invocation, checks the exact practice URL, and rechecks the document and field identities before writing. It skips unsupported, changed, occupied, hidden and sensitive controls. Real employer sites require a separately verified mapping and authorization before browser filling can be enabled. [Chromium activeTab permission](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)

The code goes into the extension popup. Keep it out of page fields, URLs and messages. The backend stores hashes of the short-lived credentials. The popup contacts only the fixed loopback API and passes only selected values to the practice document. Activity records show preparation, copy requests, fill authorizations and reported field counts. If the popup closes before it reports a result, the authorization can appear without a completed fill. Review activity in the assistant or on the queue entry after its status changes. Recording an actual submission remains a separate, explicit Application Queue action.

### Manual acceptance tests

1. In a disposable installation, create a synthetic master resume with an email, experience and education. Generate and approve a tailored resume. Attach that revision to a preparation packet with a supported skills question and a salary question, review it, and approve the packet.
2. Open the Ready job in the assistant. Confirm the evidence labels and unresolved salary answer. Copy one supported value after review. Check that the queue still reports Ready and has no submission record.
3. In the practice tab, use the extension to preview a fresh code. Confirm the page is still empty. Select just email and fill it. Confirm only email changed, check **Refresh activity**, and try the same code again.
4. With another code, try a different tab or add a query string to the practice URL. Try editing a populated field and previewing again. The companion should refuse the wrong page and skip existing values.
5. Generate another code and edit the approved preparation document before filling. Check that the handoff fails and the queue requests approval again. Restore readiness, then verify the manual copy path for a reviewed question outside the synthetic mapping.
6. Run the backend tests as described under **Run the checks** using a disposable PostgreSQL database. Run `node --test tests/companion.test.mjs` from the project root, then `npm.cmd run build` from `frontend`.
