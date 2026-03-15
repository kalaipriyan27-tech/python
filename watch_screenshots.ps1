$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "watch_screenshots.py"
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"

function Copy-SavedEnvToProcess {
    param([string[]]$Names)

    foreach ($name in $Names) {
        $currentValue = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($currentValue) {
            continue
        }

        $savedValue = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not $savedValue) {
            $savedValue = [Environment]::GetEnvironmentVariable($name, "Machine")
        }

        if ($savedValue) {
            [Environment]::SetEnvironmentVariable($name, $savedValue, "Process")
        }
    }
}

Copy-SavedEnvToProcess -Names @(
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_APIKEY",
    "OPENAI_API_KEY"
)

if (Test-Path $venvPython) {
    & $venvPython $scriptPath @args
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & $py.Source $scriptPath @args
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python -and $python.Source -notlike "*WindowsApps*") {
    & $python.Source $scriptPath @args
    exit $LASTEXITCODE
}

Write-Error "No usable Python interpreter was found. Create .venv or install Python first."
exit 1
