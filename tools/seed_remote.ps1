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

# Singapore has FOUR index series (BCA, HDB, RLB, AECOM) x 8 quarters = 32 rows. This
# threshold used to read 100, which no seed in this repository has ever produced, so it
# failed a perfectly good reseed. India has 94: six WPI materials series x 13 quarters
# (78, real) plus CPWD and NBO x 8 quarters (16, indicative).
Check '/api/indices/tpi?country=SG'                'SG indexes'   32
Check '/api/indices/tpi?country=IN'                'IN indexes'   94
Check '/api/indices/regions?country=IN'            'IN regions'   12
# ONE row per section per market. More than that means a stale library is loaded beside
# the current one - see the note on load_benchmark_rates in app/etl.py.
Check '/api/indices/benchmark-rates?country=SG'    'SG rates'     10
Check '/api/indices/benchmark-rates?country=IN'    'IN rates'     10
Check '/api/boq?country=SG'                        'SG samples'    1
# The price series are the bridge input: producer baskets for India, the consumer index
# for both. The default series must be present, or the bridge cannot run at all.
Check '/api/indices/price-series?country=IN&series=PPI-ALL'  'IN PPI'  40
Check '/api/indices/price-series?country=SG&series=CPI-ALL'  'SG CPI'  55

Write-Host ''

# The rate library must hold exactly one row per section, or the engine is choosing
# between a current rate and a stale one. It picks by confidence, so a stale 'medium'
# row beats a current 'low' one - which is how the deployed database ended up pricing
# Singapore Concrete at the old invented 145.00 instead of the SOR-derived 140.53.
foreach ($country in 'SG', 'IN') {
    try {
        # Assign first, then wrap: an inline @(Invoke-RestMethod ...) with a concatenated
        # URI is parsed as a single element, which silently disabled this check.
        $fetched = Invoke-RestMethod -Uri ($ApiBase + '/api/indices/benchmark-rates?country=' + $country) -TimeoutSec 60
        $rows = @($fetched)
        $dupes = @($rows | Group-Object smm2_section | Where-Object { $_.Count -gt 1 })
        if ($dupes.Count -gt 0) {
            Write-Host ('  ' + $country + ' rate library: ' + $dupes.Count + ' section(s) carry more than one row') -ForegroundColor Red
            Write-Host ('    ' + (($dupes | ForEach-Object { $_.Name }) -join ', ')) -ForegroundColor Red
            Write-Host '    A stale row is sitting beside the current one. Re-run this script.' -ForegroundColor Red
            $script:failed = $true
        }
        else {
            $derivedRows = @($rows | Where-Object { -not $_.is_placeholder })
            $retainedRows = @($rows | Where-Object { $_.is_placeholder })
            $derived = $derivedRows.Count
            $retained = $retainedRows.Count
            Write-Host ('  ' + $country + ' rate library: one row per section, ' + $derived + ' derived / ' + $retained + ' retained') -ForegroundColor Green
        }
    }
    catch {
        Write-Host ('  ' + $country + ' rate library check failed - ' + $_) -ForegroundColor Red
        $script:failed = $true
    }
}

# The producer index must actually be loaded for India, or every bridged rate falls back
# to the weaker consumer proxy.
try {
    $cov = Invoke-RestMethod -Uri ($ApiBase + '/api/indices/coverage?country=IN') -TimeoutSec 60
    if ($cov.producer_series_count -lt 16 -or $cov.carry_index_kind -ne 'producer') {
        Write-Host ('  IN coverage: producer_series_count=' + $cov.producer_series_count + ', carried by ' + $cov.carry_index_kind + ' - expected 16 and producer') -ForegroundColor Red
        $script:failed = $true
    }
    else {
        Write-Host ('  IN coverage: ' + $cov.producer_series_count + ' producer baskets, rates carried by ' + $cov.carry_index_series) -ForegroundColor Green
    }
}
catch {
    Write-Host ('  IN coverage check failed - ' + $_) -ForegroundColor Red
    $script:failed = $true
}

Write-Host ''
if ($failed) {
    Write-Host 'Some checks failed. The lines above name what is wrong.' -ForegroundColor Red
    exit 1
}
Write-Host 'Seed verified. The deployed app now has data.' -ForegroundColor Green
Write-Host ''
Write-Host "Open: $ApiBase/docs" -ForegroundColor Cyan
