<#
.SYNOPSIS
  Download BRouter routing segments (.rd5 tiles) for offline inland-
  waterway routing in DDN_DIMinfra.

.DESCRIPTION
  BRouter splits the world into 5° × 5° lon/lat tiles named
  E{lon}_N{lat}.rd5 (W/S for negative). This script downloads every
  tile that intersects the requested region into
  ./data/brouter/segments/, where it is mounted read-only by the
  brouter service in docker-compose.routing.yml.

.PARAMETER Region
  Pre-defined region. Supported values:
    benelux         (4 tiles, ~250 MB)   — BE/NL/LU
    ddn             (~60 tiles, ~9 GB)   — BE/NL/LU/FR/DE/IT/ES/PL/NO/DK/SE
                                            (the 11 countries DDN ships to)
    western-europe  (~30 tiles, ~5 GB)   — UK/IE + ES/PT + FR/DE/IT/CH/AT + Benelux
    europe          (~80 tiles, ~12 GB)  — full continent incl. Scandinavia + E-EU

.PARAMETER MinLat
  Override: minimum latitude (overrides -Region).

.PARAMETER MaxLat
  Override: maximum latitude.

.PARAMETER MinLon
  Override: minimum longitude (negative = west).

.PARAMETER MaxLon
  Override: maximum longitude.

.PARAMETER BaseUrl
  BRouter segments base URL. Defaults to the official mirror.

.PARAMETER Force
  Re-download tiles even if they already exist on disk.

.EXAMPLE
  ./scripts/prepare-brouter.ps1 -Region benelux

.EXAMPLE
  ./scripts/prepare-brouter.ps1 -Region ddn

.EXAMPLE
  ./scripts/prepare-brouter.ps1 -Region western-europe

.EXAMPLE
  # Custom bbox: Northern Italy
  ./scripts/prepare-brouter.ps1 -MinLat 43 -MaxLat 47 -MinLon 6 -MaxLon 14
#>

[CmdletBinding(DefaultParameterSetName = "Region")]
param(
    [Parameter(ParameterSetName = "Region")]
    [ValidateSet("benelux", "ddn", "western-europe", "europe")]
    [string]$Region = "ddn",

    [Parameter(ParameterSetName = "Custom", Mandatory)]
    [double]$MinLat,
    [Parameter(ParameterSetName = "Custom", Mandatory)]
    [double]$MaxLat,
    [Parameter(ParameterSetName = "Custom", Mandatory)]
    [double]$MinLon,
    [Parameter(ParameterSetName = "Custom", Mandatory)]
    [double]$MaxLon,

    [string]$BaseUrl = "http://brouter.de/brouter/segments4",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ($PSCmdlet.ParameterSetName -eq "Region") {
    switch ($Region) {
        "benelux" {
            $MinLat = 49; $MaxLat = 54; $MinLon = 2;   $MaxLon = 8
        }
        "ddn" {
            # BE/NL/LU/FR/DE/IT/ES/PL/NO/DK/SE bounding box.
            # Tightened to landmass only — Atlantic tiles west of Iberia
            # and far-northern tiles above Nordkapp are excluded because
            # they don't exist on the BRouter server (saves wasted HTTP
            # round trips and disk).
            $MinLat = 35; $MaxLat = 70; $MinLon = -10; $MaxLon = 25
        }
        "western-europe" {
            # Iberia + UK/IE + France + Germany + Alps + Italy + Benelux
            $MinLat = 35; $MaxLat = 60; $MinLon = -10; $MaxLon = 20
        }
        "europe" {
            $MinLat = 35; $MaxLat = 71; $MinLon = -10; $MaxLon = 35
        }
    }
}

# BRouter tile naming: E{lon}_N{lat}.rd5 where lon/lat is the SW corner,
# rounded down to the nearest multiple of 5. Negative lon/lat use W/S.
function Get-TileName([int]$lonSW, [int]$latSW) {
    $lonPart = if ($lonSW -lt 0) { "W{0}" -f ([math]::Abs($lonSW)) } else { "E$lonSW" }
    $latPart = if ($latSW -lt 0) { "S{0}" -f ([math]::Abs($latSW)) } else { "N$latSW" }
    return "${lonPart}_${latPart}.rd5"
}

# Snap bbox edges down/up to multiples of 5.
$lonStart = [int]([math]::Floor($MinLon / 5.0) * 5)
$lonEnd   = [int]([math]::Floor($MaxLon / 5.0) * 5)
$latStart = [int]([math]::Floor($MinLat / 5.0) * 5)
$latEnd   = [int]([math]::Floor($MaxLat / 5.0) * 5)

$tiles = @()
for ($lat = $latStart; $lat -le $latEnd; $lat += 5) {
    for ($lon = $lonStart; $lon -le $lonEnd; $lon += 5) {
        $tiles += Get-TileName $lon $lat
    }
}

$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..")
$segmentDir = Join-Path $repoRoot "data\brouter\segments"
$null = New-Item -ItemType Directory -Force -Path $segmentDir

Write-Host "Region:  $Region (or custom bbox)"
Write-Host "BBox:    lat $MinLat..$MaxLat, lon $MinLon..$MaxLon"
Write-Host "Tiles:   $($tiles.Count)"
Write-Host "Target:  $segmentDir"
Write-Host ""

$downloaded = 0; $skipped = 0; $failed = @()
foreach ($tile in $tiles) {
    $dest = Join-Path $segmentDir $tile
    if ((Test-Path $dest) -and -not $Force) {
        $skipped++
        continue
    }
    $url = "$BaseUrl/$tile"
    Write-Host "  Downloading $tile ..." -ForegroundColor Cyan
    try {
        # BITS for resumable / parallel-friendly downloads when available.
        try {
            Start-BitsTransfer -Source $url -Destination $dest -ErrorAction Stop
        } catch {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        }
        $downloaded++
    } catch {
        Write-Warning "    failed: $tile  ($($_.Exception.Message))"
        if (Test-Path $dest) { Remove-Item -Force $dest -ErrorAction SilentlyContinue }
        $failed += $tile
    }
}

Write-Host ""
Write-Host "Downloaded: $downloaded   Skipped (already present): $skipped   Failed: $($failed.Count)" -ForegroundColor Green
if ($failed.Count -gt 0) {
    Write-Warning "Some tiles failed to download (often because they cover only ocean and don't exist):"
    $failed | ForEach-Object { Write-Host "    $_" }
}

Write-Host "`nStart the routing stack with:" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.routing.yml up -d brouter" -ForegroundColor Green
