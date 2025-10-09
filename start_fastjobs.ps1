param(
  [string]$VenvPath = ".\.venv",
  [string]$BotPath  = ".\fastjob_bot.py",
  [string]$DashPath = ".\dashboard\app.py",
  [string]$EnvFile  = ".\.env",
  [string]$EnvExample = ".\.env.example",
  [string]$StoragePath = ".\storage\state.json"
)

function Ensure-Venv {
  if (-not (Test-Path $VenvPath)) {
    Write-Host "Creating virtual environment at $VenvPath..." -ForegroundColor Cyan
    python -m venv $VenvPath
  }
  $activate = Join-Path $VenvPath "Scripts\Activate.ps1"
  . $activate
  Write-Host "Virtual environment activated." -ForegroundColor Green
}

function Ensure-Deps {
  Write-Host "Installing required packages (if missing)..." -ForegroundColor Cyan
  if (Test-Path ".\requirements.txt") { pip install -r .\requirements.txt | Out-Null }
}

function Ensure-Env {
  if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
      Copy-Item $EnvExample $EnvFile
      Write-Host "Created $EnvFile from $EnvExample. Please edit it with real credentials if needed." -ForegroundColor Yellow
    } else {
      Write-Host "No .env and no .env.example found. Creating a minimal template..." -ForegroundColor Yellow
      @"
FASTJOBS_EMAIL=your_email_here
FASTJOBS_PASSWORD=your_password_here
FASTJOBS_LOGIN_URL=https://employer.fastjobs.sg/site/login/
STORAGE_STATE=storage/state.json
"@ | Set-Content -Encoding UTF8 $EnvFile
      Write-Host "Created $EnvFile. Please edit with real credentials." -ForegroundColor Yellow
    }
  }
}

function Ensure-Playwright {
  Write-Host "Ensuring Playwright browsers are installed..." -ForegroundColor Cyan
  try {
    # Prefer IPv4 to avoid IPv6 CDN hiccups
    $oldNode = $env:NODE_OPTIONS
    $env:NODE_OPTIONS = "--dns-result-order=ipv4first"

    # Try a minimal install (chromium only)
    python -m playwright install chromium | Out-Null

    # Restore env var
    $env:NODE_OPTIONS = $oldNode
  }
  catch {
    Write-Warning "Playwright browser install failed. You can try later:"
    Write-Host "  1) Set IPv4 first: `$env:NODE_OPTIONS=\"--dns-result-order=ipv4first\""
    Write-Host "  2) Then run: python -m playwright install chromium"
    Write-Host "Continuing without blocking..."
  }
}

function Ensure-Session {
  if (-not (Test-Path $StoragePath)) {
    Write-Host "No login session found. Launching login flow (login_check.py)..." -ForegroundColor Cyan
    python .\login_check.py
    if (-not (Test-Path $StoragePath)) {
      Write-Host "Login session was not created. Please ensure credentials in .env are correct and try again." -ForegroundColor Red
      exit 1
    }
  }
}

function Prompt-RunMode {
  $mode = Read-Host "Run mode? (D=Dry / L=Live) [D]"
  if ([string]::IsNullOrWhiteSpace($mode)) { $mode = "D" }
  $mode = $mode.Trim().ToUpper()
  if ($mode -eq "L" -or $mode -eq "LIVE") { return "false" } else { return "true" }
}

function Prompt-Limit {
  $limit = Read-Host "LIMIT_JOBS? (0=all, 1=first job, etc.) [0]"
  if ([string]::IsNullOrWhiteSpace($limit)) { $limit = "0" }
  return $limit
}

function Prompt-Dashboard {
  $ans = Read-Host "Open dashboard after bot finishes? (Y/n) [Y]"
  if ([string]::IsNullOrWhiteSpace($ans)) { $ans = "Y" }
  $ans = $ans.Trim().ToUpper()
  return ($ans -eq "Y" -or $ans -eq "YES")
}

# ---- main ----
# allow script in this session if needed
try { $null = Get-ExecutionPolicy } catch {}
# Prepare runtime
Ensure-Venv
Ensure-Deps
Ensure-Env
Ensure-Playwright
Ensure-Session

# Prompt runtime choices
$env:DRY_RUN    = Prompt-RunMode
$env:LIMIT_JOBS = Prompt-Limit
Write-Host "DRY_RUN=$($env:DRY_RUN)  LIMIT_JOBS=$($env:LIMIT_JOBS)" -ForegroundColor Yellow

# Run bot (it will ask interval as usual)
Write-Host "Starting FastJobs bot..." -ForegroundColor Cyan
python $BotPath

# Offer dashboard
if (Prompt-Dashboard) {
  Write-Host "Launching Streamlit dashboard in a new window..." -ForegroundColor Cyan
  $dashCmd = "cd `"$PWD`"; . `"$VenvPath\Scripts\Activate.ps1`"; streamlit run `"$DashPath`""
  Start-Process -FilePath "powershell" -ArgumentList "-NoExit","-Command",$dashCmd | Out-Null
  Write-Host "Dashboard started in a new window. Close it with Ctrl+C in that window." -ForegroundColor Green
}

Write-Host "Done." -ForegroundColor Green
