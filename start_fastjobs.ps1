param(
  [string]$VenvPath = ".\.venv",
  [string]$BotPath  = ".\fastjob_bot.py",
  [string]$DashPath = ".\dashboard\app.py"
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
  if (Test-Path ".\requirements.txt") {
    pip install -r .\requirements.txt
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
Ensure-Venv
Ensure-Deps

$env:DRY_RUN    = Prompt-RunMode
$env:LIMIT_JOBS = Prompt-Limit

Write-Host "DRY_RUN=$($env:DRY_RUN)  LIMIT_JOBS=$($env:LIMIT_JOBS)" -ForegroundColor Yellow
Write-Host "Starting FastJobs bot..." -ForegroundColor Cyan

# Your bot will still ask for the time interval like before
python $BotPath

$openDash = Prompt-Dashboard
if ($openDash) {
  Write-Host "Launching Streamlit dashboard in a new window..." -ForegroundColor Cyan
  $dashCmd = "cd `"$PWD`"; . `"$VenvPath\Scripts\Activate.ps1`"; streamlit run `"$DashPath`""
  Start-Process -FilePath "powershell" -ArgumentList "-NoExit","-Command",$dashCmd | Out-Null
  Write-Host "Dashboard started in a new window. Close it with Ctrl+C in that window." -ForegroundColor Green
}

Write-Host "Done." -ForegroundColor Green
