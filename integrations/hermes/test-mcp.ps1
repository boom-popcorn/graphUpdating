param(
    [string]$HermesExe,
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $PythonExe) {
    $PythonExe = Join-Path $ProjectRoot ".venv-hermes\Scripts\python.exe"
}
if (-not $HermesExe) {
    $HermesExe = Join-Path $ProjectRoot ".venv-hermes\Scripts\hermes.exe"
}

& $PythonExe (Join-Path $ProjectRoot "scripts\render_hermes_config.py") `
    --root $ProjectRoot --python $PythonExe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:HERMES_HOME = Join-Path $ProjectRoot "runtime\hermes-home"
& $HermesExe mcp test ontology_driven_kg
exit $LASTEXITCODE
