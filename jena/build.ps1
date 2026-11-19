$ErrorActionPreference = 'Stop'
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = 'C:\Program Files\Java\jdk-17' }
$env:PATH = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:PATH
$repo = (Join-Path (Split-Path -Parent $PSScriptRoot) '.m2repo').Replace('\', '/')
mvn -f (Join-Path $PSScriptRoot 'pom.xml') "-Dmaven.repo.local=$repo" -DskipTests package
if ($LASTEXITCODE -ne 0) { throw 'Jena bridge build failed' }
Write-Output 'Built Jena TDB2 bridge and Apache Jena Fuseki Main (5.1.0).'
Write-Output 'Start the database query console with .\jena\start_fuseki.ps1'
