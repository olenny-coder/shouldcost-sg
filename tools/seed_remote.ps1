#requires -Version 5.1
<#
.SYNOPSIS
    Seed a REMOTE shouldcost database (Neon / Render Postgres) from your machine.

.DESCRIPTION
    Runs backend/app/etl.py against a remote PostgreSQL, then verifies the result
    through the deployed API. The connection string is asked for at runtime and is
    never written to disk.

.EXAMPLE
    .\tools\seed_remote.ps1
    .\tools\seed_remote.ps1 -DatabaseUrl 'postgresql://...' -ApiBase 'https://x.onrender.com'
#>
[CmdletBinding()]
param(
    [string]$DatabaseUrl,
    [string]$ApiBase = 'https://shouldcost-backend-7qnj.onrender.com',
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'

$repoRoot   = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
if (-not (Test-Path (Join-Path $backendDir 'app\etl.py'))) {
    throw "Could not find backend/app/etl.py under '$repoRoot'. Run this from the repository."
}

# ---------------------------------------------------------------- get the URL --
if (-not $DatabaseUrl) {
    Write-Host ''
    Write-Host 'Paste the Neon connection string (input is hidden).' -ForegroundColor Cyan
    Write-Host 'Use the DIRECT url - the host must NOT contain "-pooler".' -ForegroundColor DarkGray
    $secure = Read-Host -Prompt 'Connection string' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try   { $DatabaseUrl = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$DatabaseUrl = $DatabaseUrl.Trim().Trim('"').Trim("'")

# The most common paste is the whole dashboard line, e.g. "DATABASE_URL = postgres://...".
#
# The pattern is deliberately narrow, because a greedy one eats the scheme from a
# perfectly good url - "postgresql:" looks exactly like "KEY:".
#
# Note PowerShell's -match/-replace are CASE-INSENSITIVE by default (unlike Python's
# re), so case alone is not enough to protect the scheme. Both forms are therefore
# written to be safe whatever the casing:
#   KEY = value    - the key charset excludes ':' and '/', so a scheme cannot match
#   KEY: value     - whitespace is REQUIRED after the colon, and "postgresql://..." has
#                    none, so the scheme can never match however it is cased
$prefixPattern = '^\s*(?:export\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s*=|(?:[A-Za-z_][A-Za-z0-9_]*)\s*:\s+)\s*'
if ($DatabaseUrl -match $prefixPattern) {
    $DatabaseUrl = ($DatabaseUrl -replace $prefixPattern, '').Trim()
    Write-Host 'Stripped a pasted "KEY = " prefix from the connection string.' -ForegroundColor Yellow
}

if ($DatabaseUrl -match '\s') {
    throw 'The connection string contains whitespace. Copy just the URL, without surrounding text.'
}
if ($DatabaseUrl -notmatch '^postgres(ql)?://') {
    throw "That does not look like a PostgreSQL URL (it must start with postgres:// or postgresql://). Got: $($DatabaseUrl.Substring(0, [Math]::Min(40, $DatabaseUrl.Length)))..."
}
if ($DatabaseUrl -match '-pooler\.') {
    Write-Host 'WARNING: this looks like the pooled (-pooler) endpoint. The direct URL is recommended for a long-running Render service.' -ForegroundColor Yellow
}

[uri]$parsed = $null
if (-not [uri]::TryCreate($DatabaseUrl, [UriKind]::Absolute, [ref]$parsed)) {
    throw 'The connection string is not a parseable URI.'
}
Write-Host ''
Write-Host "Target host : $($parsed.Host)" -ForegroundColor Green
Write-Host "Target db   : $($parsed.AbsolutePath.TrimStart('/'))" -ForegroundColor Green

# ------------------------------------------------------------- run the ETL --
$env:DATABASE_URL = $DatabaseUrl
try {
    Push-Location $backendDir
    try {
        Write-Host ''
        Write-Host 'Running python -m app.etl ...' -ForegroundColor Cyan
        & python -m app.etl
        if ($LASTEXITCODE -ne 0) { throw "The ETL exited with code $LASTEXITCODE." }
    }
    finally { Pop-Location }
}
finally {
    # Never leave the shell pointing at production.
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    Write-Host ''
    Write-Host 'Cleared DATABASE_URL from this shell.' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- verify --
if ($SkipVerify) { return }

Write-Host ''
Write-Host "Verifying through $ApiBase ..." -ForegroundColor Cyan
Start-Sleep -Seconds 2

$failed = $false
function Check([string]$Path, [string]$Label, [int]$Expected) {
    try {
        $data = Invoke-RestMethod -Uri ($ApiBase + $Path) -TimeoutSec 60
        $count = @($data).Count
        $ok = $count -ge $Expected
        $colour = if ($ok) { 'Green' } else { 'Red' }
        Write-Host ("  {0,-12} {1,4} rows   (expected >= {2})" -f $Label, $count, $Expected) -ForegroundColor $colour
        if (-not $ok) { $script:failed = $true }
    }
    catch {
        Write-Host "  $Label : request failed - $_" -ForegroundColor Red
        $script:failed = $true
    }
}

Check '/api/indices/tpi?country=SG'                'SG indexes'  100
Check '/api/indices/regions?country=IN'            'IN regions'   10
Check '/api/indices/benchmark-rates?country=SG'    'rate library' 10
Check '/api/boq?country=SG'                        'SG samples'    1
# The price series are the bridge input: producer baskets for India, the consumer
# index for both. The default series must be present, or the bridge cannot run.
Check '/api/indices/price-series?country=IN&series=PPI-ALL'  'IN PPI'  40
Check '/api/indices/price-series?country=SG&series=CPI-ALL'  'SG CPI'  55

Write-Host ''
if ($failed) {
    Write-Host 'Some checks failed. The database may still be empty - re-run and read the ETL output above.' -ForegroundColor Red
    exit 1
}
Write-Host 'Seed verified. The deployed app now has data.' -ForegroundColor Green
Write-Host ''
Write-Host "Open: $ApiBase/docs" -ForegroundColor Cyan
