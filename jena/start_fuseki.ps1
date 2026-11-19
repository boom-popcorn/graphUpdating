param(
    [string]$DatasetDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'storage\fuseki-demo'),
    [ValidateRange(1, 65535)][int]$Port = 3030
)
$ErrorActionPreference = 'Stop'
$jar = Join-Path $PSScriptRoot 'target\jena-store.jar'
if (-not (Test-Path -LiteralPath $jar)) { throw 'Run .\jena\build.ps1 first.' }
$java = if ($env:JAVA) { $env:JAVA } elseif ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME 'bin\java.exe' } elseif (Test-Path 'C:\Program Files\Java\jdk-17\bin\java.exe') { 'C:\Program Files\Java\jdk-17\bin\java.exe' } else { 'java' }
$datasetPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DatasetDir)
Write-Host "Open http://localhost:$Port/"
Write-Host "In the application terminal set: `$env:MARITIME_FUSEKI_URL = 'http://localhost:$Port'"
Write-Host "Use --dataset-dir '$datasetPath' (or MARITIME_DATASET_DIR for the web app)."
& $java -cp $jar org.example.maritime.FusekiLauncher $datasetPath $Port (Join-Path $PSScriptRoot 'web')
if ($LASTEXITCODE -ne 0) { throw 'Fuseki exited with an error. Check the port and TDB2 directory lock.' }
