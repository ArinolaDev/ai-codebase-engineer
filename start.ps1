# Starts AI Codebase Engineer: sets up Python packages on the first run, then opens the app.
# Run it by double-clicking start.bat (or: powershell -ExecutionPolicy Bypass -File start.ps1)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
Set-Location $backend

# 1) Python version: the tree-sitter packages have no build for Python 3.13 yet
try {
    $minor = [int](& python -c "import sys; print(sys.version_info.minor)")
    $major = [int](& python -c "import sys; print(sys.version_info.major)")
} catch {
    Write-Host "Python was not found. Install Python 3.11 or 3.12 from python.org and tick 'Add to PATH'." -ForegroundColor Red
    exit 1
}
if ($major -ne 3 -or $minor -lt 10 -or $minor -gt 12) {
    Write-Warning "You have Python $major.$minor. This project is tested on 3.11 and 3.12. Installation may fail on other versions."
}

# 2) Virtual environment
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creating the virtual environment (one time)..."
    & python -m venv venv
}
$py = Join-Path $backend "venv\Scripts\python.exe"

# 3) Packages (again only if requirements.txt changed)
$marker = Join-Path $backend "venv\.deps-installed"
$needs = (-not (Test-Path $marker)) -or ((Get-Item "requirements.txt").LastWriteTime -gt (Get-Item $marker).LastWriteTime)
if ($needs) {
    Write-Host "Installing packages (the first time takes a few minutes)..."
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "Installing the packages failed. See the messages above." -ForegroundColor Red; exit 1 }
    Set-Content -Path $marker -Value (Get-Date)
}

# 4) Ollama (the free local models). The app starts without it, but fixes, tests and chat need it.
try {
    $tags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3
    $names = @($tags.models | ForEach-Object { $_.name })
    if (-not ($names | Where-Object { $_ -like "qwen2.5-coder*" })) {
        Write-Warning "No coder model found. Run:  ollama pull qwen2.5-coder:3b"
    }
} catch {
    Write-Warning "Ollama is not running. Open the Ollama app. Scanning works without it, but model fixes, tests and chat do not."
}

# 5) Open the browser a few seconds after the server starts, then run the server
Start-Job -ScriptBlock { Start-Sleep -Seconds 7; Start-Process "http://127.0.0.1:8000/ui/" } | Out-Null
Write-Host ""
Write-Host "AI Codebase Engineer is starting at http://127.0.0.1:8000/ui/  (press Ctrl+C to stop)" -ForegroundColor Green
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
