$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$javaHome = if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
    $env:JAVA_HOME
} elseif (Test-Path "C:\Program Files\Java\jdk-17\bin\java.exe") {
    "C:\Program Files\Java\jdk-17"
} else {
    throw "Set JAVA_HOME to a JDK 17 installation before building the HermiT bridge."
}

$env:JAVA_HOME = $javaHome
$env:PATH = (Join-Path $javaHome "bin") + ";" + $env:PATH
$projectMavenRepo = Join-Path $projectRoot ".m2repo"
$mavenRepoForwardSlash = $projectMavenRepo.Replace('\', '/')
$env:MAVEN_OPTS = (($env:MAVEN_OPTS + " -Dmaven.repo.local=$mavenRepoForwardSlash").Trim())
mvn -f (Join-Path $PSScriptRoot "pom.xml") -DskipTests clean package
if ($LASTEXITCODE -ne 0) {
    throw "Maven failed to build the HermiT validator."
}

$jar = Join-Path $PSScriptRoot "target\hermit-validator.jar"
if (-not (Test-Path $jar)) {
    throw "Build finished without producing $jar"
}
Write-Output "Built $jar"
