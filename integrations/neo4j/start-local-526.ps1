param(
    [ValidateSet('start', 'stop', 'status', 'console')]
    [string]$Command = 'start'
)

$neo4jHome = 'D:\Neo4j-5.26\neo4j-community-5.26.28-windows\neo4j-community-5.26.28'
$jdkHome = 'C:\Program Files\Java\jdk-21'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$neo4jConf = Join-Path $projectRoot 'integrations\neo4j\local-526-conf'
$shortTemp = "C:\Users\run'wu'xi'wu's\Documents\Codex\.neo4j-tmp-526"

$env:JAVA_HOME = $jdkHome
$env:NEO4J_HOME = $neo4jHome
$env:NEO4J_CONF = $neo4jConf
$env:TEMP = $shortTemp
$env:TMP = $shortTemp
$env:Path = "$jdkHome\bin;$neo4jHome\bin;" + $env:Path

if (-not (Test-Path -LiteralPath "$neo4jHome\bin\neo4j.bat")) {
    throw "Neo4j 5.26 executable not found under $neo4jHome"
}

New-Item -ItemType Directory -Force -Path (
    Join-Path $projectRoot 'runtime\neo4j-526\data'
), (
    Join-Path $projectRoot 'runtime\neo4j-526\logs'
), (
    Join-Path $projectRoot 'runtime\neo4j-526\run'
), (
    Join-Path $projectRoot 'runtime\neo4j-526\transactions'
), (
    $shortTemp
) | Out-Null

& "$neo4jHome\bin\neo4j.bat" $Command
exit $LASTEXITCODE
