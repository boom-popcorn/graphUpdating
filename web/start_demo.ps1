$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$javaHome = if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
    $env:JAVA_HOME
} elseif (Test-Path "C:\Program Files\Java\jdk-17\bin\java.exe") {
    "C:\Program Files\Java\jdk-17"
} else {
    throw "Set JAVA_HOME to a JDK 17 installation."
}
$env:JAVA_HOME = $javaHome
$env:PATH = (Join-Path $javaHome "bin") + ";" + $env:PATH
Set-Location $projectRoot
if (-not $env:MARITIME_DEMO_PORT) { $env:MARITIME_DEMO_PORT = "8765" }
Write-Output "Starting demo at http://127.0.0.1:$($env:MARITIME_DEMO_PORT)"
python -m mvp.web_server
