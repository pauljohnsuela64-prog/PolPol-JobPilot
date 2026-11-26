param([switch]$SetPassword)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$data = Join-Path $env:LOCALAPPDATA 'PolPolJobPilot\postgres-data'
$saved = Join-Path $data 'jobpilot.dpapi'
$python = Join-Path $root '.venv\Scripts\python.exe'
$env:DATABASE_URL = 'postgresql://jobpilot@127.0.0.1:5433/jobpilot'
Set-Location (Join-Path $root 'backend')
if ($SetPassword) {
    $admin = Get-Content (Join-Path $data 'bootstrap-admin.dpapi') | ConvertTo-SecureString
    $env:PGPASSWORD = [Net.NetworkCredential]::new('', $admin).Password
    $secret = Read-Host 'Choose the local jobpilot database password' -AsSecureString
    if (!$secret.Length) { throw 'A password is required.' }
    $env:JOBPILOT_NEW_PASSWORD = [Net.NetworkCredential]::new('', $secret).Password
    try {
        @'
import os
import psycopg
from psycopg import sql

with psycopg.connect('postgresql://postgres@127.0.0.1:5433/jobpilot') as db:
    verifier = db.pgconn.encrypt_password(os.environ.pop('JOBPILOT_NEW_PASSWORD').encode(), b'jobpilot', b'scram-sha-256')
    db.execute(sql.SQL('ALTER ROLE jobpilot PASSWORD {}').format(sql.Literal(verifier.decode())))
'@ | & $python -
        if ($LASTEXITCODE) { throw 'Password setup failed.' }
        $secret | ConvertFrom-SecureString | Set-Content -LiteralPath $saved -Encoding ASCII
    } finally {
        Remove-Item Env:JOBPILOT_NEW_PASSWORD, Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}
$secret = Get-Content -LiteralPath $saved | ConvertTo-SecureString
$env:PGPASSWORD = [Net.NetworkCredential]::new('', $secret).Password
& $python -c "import os, psycopg; db = psycopg.connect(os.environ['DATABASE_URL']); print(db.execute('select current_user, current_database(), inet_server_port()').fetchone()); db.close()"
if ($LASTEXITCODE) { throw 'Application database login failed.' }
& $python -m uvicorn main:app --host 127.0.0.1 --port 8000
