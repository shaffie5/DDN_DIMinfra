<#
.SYNOPSIS
  Launch the bundled BRouter HTTP routing server (port 17777) using
  the local JDK and segment tiles in this repository — no Docker required.

.DESCRIPTION
  Uses:
    tools\jdk\jdk-21.0.11+10\bin\java.exe
    tools\brouter\brouter-1.7.9\brouter-1.7.9-all.jar
    tools\brouter\brouter-1.7.9\profiles2\
    tools\brouter\brouter-1.7.9\customprofiles\
    data\brouter\segments\        (downloaded via prepare-brouter.ps1)
    data\brouter\profiles\barge.brf  (copied into profiles2 on each launch)

  After it prints "BRouter 1.7.9 ..." and binds the port, set
  BROUTER_URL=http://127.0.0.1:17777 in the shell that runs flask_app.py
  (flask_app.py also auto-detects a running BRouter on that port).

.PARAMETER Port
  TCP port to bind. Defaults to 17777.

.PARAMETER MaxRunningTime
  Per-request hard timeout passed to the JVM. Defaults to 300 s.

.EXAMPLE
  ./scripts/start-brouter.ps1
#>

[CmdletBinding()]
param(
    [int]$Port = 17777,
    [int]$MaxRunningTime = 300,
    [string]$Xmx = "512M"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$jdkDir   = Get-ChildItem (Join-Path $repoRoot "tools\jdk") -Directory |
            Where-Object { $_.Name -like "jdk-*" } |
            Select-Object -First 1
if (-not $jdkDir) { throw "No JDK found under tools\jdk\." }

$java     = Join-Path $jdkDir.FullName "bin\java.exe"
$brouterDir = Get-ChildItem (Join-Path $repoRoot "tools\brouter") -Directory |
              Where-Object { $_.Name -like "brouter-*" } |
              Select-Object -First 1
if (-not $brouterDir) { throw "No BRouter install found under tools\brouter\." }

$jar    = Get-ChildItem $brouterDir.FullName -Filter "brouter-*-all.jar" |
          Select-Object -First 1 -ExpandProperty FullName
$profs  = Join-Path $brouterDir.FullName "profiles2"
$cprof  = Join-Path $brouterDir.FullName "customprofiles"
$segs   = Join-Path $repoRoot "data\brouter\segments"

if (-not (Test-Path $jar))   { throw "BRouter jar not found: $jar" }
if (-not (Test-Path $profs)) { throw "Profiles dir not found: $profs" }
if (-not (Test-Path $segs))  { throw "Segments dir not found: $segs (run scripts\prepare-brouter.ps1 first)" }

# Ensure our barge profile is in profiles2 (BRouter only loads from there).
$bargeSrc = Join-Path $repoRoot "data\brouter\profiles\barge.brf"
if (Test-Path $bargeSrc) {
    Copy-Item -Force $bargeSrc (Join-Path $profs "barge.brf")
}

Write-Host "BRouter:   $jar"
Write-Host "Java:      $java"
Write-Host "Segments:  $segs"
Write-Host "Profiles:  $profs"
Write-Host "Listening: http://127.0.0.1:$Port"
Write-Host ""

& $java "-DmaxRunningTime=$MaxRunningTime" "-Xmx$Xmx" `
        "-cp" $jar "btools.server.RouteServer" `
        $segs $profs $cprof $Port 1
