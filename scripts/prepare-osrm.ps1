<#
.SYNOPSIS
  Download an OSM PBF extract from Geofabrik and preprocess it for
  self-hosted OSRM (offline truck routing for DDN_DIMinfra).

.DESCRIPTION
  Runs the standard OSRM 3-step pipeline (extract -> partition ->
  customize) against the Geofabrik PBF using the official osrm-backend
  Docker image. The resulting .osrm.* files land in ./data/osrm/ and
  are loaded read-only by the osrm service in
  docker-compose.routing.yml.

  Requires Docker Desktop (or any docker engine) on PATH.

.PARAMETER Region
  Pre-defined region. Supported values:
    benelux         (~500 MB PBF, ~3 GB processed) — BE + NL + LU
    ddn             (~12 GB PBF merged, ~50 GB processed) — BE/NL/LU/FR/
                    DE/IT/ES/PL/NO/DK/SE (the 11 countries DDN ships to).
                    Downloads per-country Geofabrik PBFs and merges them
                    via the openmaptiles-tools Docker image (osmium).
    western-europe  (~25 GB PBF, ~100 GB processed) — full Europe extract
                    (Geofabrik has no W-EU subextract).
    europe          (~25 GB PBF, ~120 GB processed) — full continent

.PARAMETER Url
  Override URL of a custom .osm.pbf to download (skips -Region presets).

.PARAMETER Profile
  OSRM profile to use. Defaults to "car" (correct for truck routing
  in this app — switch to "truck.lua" only if you build a custom
  profile).

.PARAMETER Force
  Re-download the PBF even if it already exists on disk.

.PARAMETER Slim
  Pre-filter the (merged) PBF with `osmium tags-filter` so only
  routable features survive (highways, ferries, waterways and the
  nodes they reference). Drops buildings, POIs, addresses, land-use
  polygons, etc. Typically shrinks the resulting OSRM graph by 70-80 %.
  Recommended for laptops / disk-constrained deployments.

.PARAMETER KeepSourcePbfs
  By default the per-country PBFs (and the merged PBF, when -Slim is
  used) are deleted after the OSRM graph has been built, to save disk
  space. Pass this switch to keep them.

.EXAMPLE
  # Belgium + Netherlands + Luxembourg
  ./scripts/prepare-osrm.ps1 -Region benelux

.EXAMPLE
  # The 11 DDN target countries (BE/NL/LU/FR/DE/IT/ES/PL/NO/DK/SE)
  ./scripts/prepare-osrm.ps1 -Region ddn

.EXAMPLE
  # Full Western Europe (large download, slow preprocessing)
  ./scripts/prepare-osrm.ps1 -Region western-europe
#>

[CmdletBinding()]
param(
    [ValidateSet("benelux", "ddn", "western-europe", "europe")]
    [string]$Region = "ddn",
    [string]$Url,
    [string]$Profile = "car",
    [switch]$Force,
    [switch]$Slim,
    [switch]$KeepSourcePbfs
)

$ErrorActionPreference = "Stop"

$presets = @{
    "benelux"        = "https://download.geofabrik.de/europe/benelux-latest.osm.pbf"
    "western-europe" = "https://download.geofabrik.de/europe-latest.osm.pbf"  # Geofabrik has no W-EU subextract; full continent
    "europe"         = "https://download.geofabrik.de/europe-latest.osm.pbf"
}

# Per-country PBFs that compose the 'ddn' region.
# Note: 'benelux' covers BE+NL+LU as a single Geofabrik subextract.
$ddnCountries = @(
    "https://download.geofabrik.de/europe/benelux-latest.osm.pbf",
    "https://download.geofabrik.de/europe/france-latest.osm.pbf",
    "https://download.geofabrik.de/europe/germany-latest.osm.pbf",
    "https://download.geofabrik.de/europe/italy-latest.osm.pbf",
    "https://download.geofabrik.de/europe/spain-latest.osm.pbf",
    "https://download.geofabrik.de/europe/poland-latest.osm.pbf",
    "https://download.geofabrik.de/europe/norway-latest.osm.pbf",
    "https://download.geofabrik.de/europe/denmark-latest.osm.pbf",
    "https://download.geofabrik.de/europe/sweden-latest.osm.pbf"
)

# Resolve repo root = parent of this script's directory.
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$osrmDir  = Join-Path $repoRoot "data\osrm"
$null = New-Item -ItemType Directory -Force -Path $osrmDir

function Invoke-Download($srcUrl, $destPath) {
    if ((Test-Path $destPath) -and -not $Force) {
        Write-Host "  already present: $(Split-Path -Leaf $destPath)" -ForegroundColor Yellow
        return
    }
    Write-Host "  downloading $srcUrl" -ForegroundColor Cyan
    try {
        Start-BitsTransfer -Source $srcUrl -Destination $destPath -ErrorAction Stop
    } catch {
        Invoke-WebRequest -Uri $srcUrl -OutFile $destPath -UseBasicParsing
    }
}

if ($Region -eq "ddn" -and -not $Url) {
    Write-Host "Region 'ddn' -> downloading $($ddnCountries.Count) per-country PBFs and merging..." -ForegroundColor Cyan
    $localPbfs = @()
    foreach ($u in $ddnCountries) {
        $name = [System.IO.Path]::GetFileName($u)
        $dest = Join-Path $osrmDir $name
        Invoke-Download $u $dest
        $localPbfs += "/data/$name"
    }

    $mergedName = "ddn-merged.osm.pbf"
    $pbfName    = $mergedName
    $pbfPath    = Join-Path $osrmDir $mergedName

    if ((Test-Path $pbfPath) -and -not $Force) {
        Write-Host "Merged PBF already present: $pbfPath (use -Force to rebuild)" -ForegroundColor Yellow
    } else {
        Write-Host "`nMerging per-country PBFs via osmium (openmaptiles-tools image)..." -ForegroundColor Cyan
        $mergeMount = "${osrmDir}:/data"
        $argList    = @("merge") + $localPbfs + @("-o", "/data/$mergedName", "--overwrite")
        docker run --rm -v $mergeMount openmaptiles/openmaptiles-tools:latest osmium @argList
        if ($LASTEXITCODE -ne 0) {
            throw "osmium merge failed (exit $LASTEXITCODE). If the openmaptiles-tools image is unavailable, install osmium-tool locally and run: osmium merge <files...> -o $pbfPath"
        }
    }
} else {
    if (-not $Url) {
        if ($Region -eq "western-europe") {
            Write-Warning "Geofabrik does not publish a single 'Western Europe' subextract."
            Write-Warning "Using the full Europe extract (~25 GB PBF). For a smaller download,"
            Write-Warning "use -Region ddn (BE/NL/LU/FR/DE/IT/ES/PL/NO/DK/SE)."
        }
        $Url = $presets[$Region]
    }

    $pbfName = [System.IO.Path]::GetFileName($Url)
    $pbfPath = Join-Path $osrmDir $pbfName
    Invoke-Download $Url $pbfPath
}

# Inside the OSRM container the data dir is mounted at /data and the
# PBF will be referenced by its filename.
$containerPbf = "/data/$pbfName"
$containerOsrm = "/data/region.osrm"
$image = "ghcr.io/project-osrm/osrm-backend:v5.27.1"
$mount = "${osrmDir}:/data"
$profilePath = "/opt/$Profile.lua"

if ($Slim) {
    $slimName = [System.IO.Path]::GetFileNameWithoutExtension([System.IO.Path]::GetFileNameWithoutExtension($pbfName)) + "-slim.osm.pbf"
    $slimPath = Join-Path $osrmDir $slimName
    if ((Test-Path $slimPath) -and -not $Force) {
        Write-Host "`nSlim PBF already present: $slimPath" -ForegroundColor Yellow
    } else {
        Write-Host "`nFiltering PBF for routable features only (osmium tags-filter)..." -ForegroundColor Cyan
        # Keep ways tagged highway/route=ferry/waterway plus referenced
        # nodes/relations. -R recursively pulls in members so ferry
        # routes and turn-restriction relations remain valid.
        $tagsArgs = @(
            "tags-filter", "-R", "--overwrite",
            "-o", "/data/$slimName",
            $containerPbf,
            "w/highway", "w/route=ferry", "r/route=ferry", "w/waterway"
        )
        docker run --rm -v $mount openmaptiles/openmaptiles-tools:latest osmium @tagsArgs
        if ($LASTEXITCODE -ne 0) { throw "osmium tags-filter failed (exit $LASTEXITCODE)" }
    }
    $pbfName      = $slimName
    $containerPbf = "/data/$pbfName"
}

Write-Host "`nRunning OSRM extract (this is the slowest step)..." -ForegroundColor Cyan
docker run --rm -v $mount $image osrm-extract -p $profilePath $containerPbf
if ($LASTEXITCODE -ne 0) { throw "osrm-extract failed (exit $LASTEXITCODE)" }

# osrm-extract writes <input>.osrm — rename to region.osrm so the
# compose file's path is stable across regions/downloads.
$baseName = [System.IO.Path]::GetFileNameWithoutExtension($pbfName)  # e.g. "benelux-latest.osm"
$baseStem = [System.IO.Path]::GetFileNameWithoutExtension($baseName) # e.g. "benelux-latest"
Get-ChildItem -Path $osrmDir -Filter "$baseStem.osrm*" | ForEach-Object {
    $newName = $_.Name -replace [regex]::Escape($baseStem), "region"
    Move-Item -Force -Path $_.FullName -Destination (Join-Path $osrmDir $newName)
}

Write-Host "`nRunning OSRM partition..." -ForegroundColor Cyan
docker run --rm -v $mount $image osrm-partition $containerOsrm
if ($LASTEXITCODE -ne 0) { throw "osrm-partition failed (exit $LASTEXITCODE)" }

Write-Host "`nRunning OSRM customize..." -ForegroundColor Cyan
docker run --rm -v $mount $image osrm-customize $containerOsrm
if ($LASTEXITCODE -ne 0) { throw "osrm-customize failed (exit $LASTEXITCODE)" }

if (-not $KeepSourcePbfs) {
    Write-Host "`nReclaiming disk space (delete intermediate PBFs)..." -ForegroundColor Cyan
    Get-ChildItem -Path $osrmDir -Filter "*.osm.pbf" | ForEach-Object {
        Write-Host "  removing $($_.Name) ($([math]::Round($_.Length / 1GB, 2)) GB)"
        Remove-Item -Force $_.FullName
    }
    Write-Host "  (pass -KeepSourcePbfs next time to keep them)" -ForegroundColor DarkGray
}

$graphSize = (Get-ChildItem -Path $osrmDir -Filter "region.osrm*" | Measure-Object Length -Sum).Sum
Write-Host ("`nOSRM graph size on disk: {0:N2} GB" -f ($graphSize / 1GB)) -ForegroundColor Green

Write-Host "`nDone. Start the routing stack with:" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.routing.yml up -d osrm" -ForegroundColor Green
Write-Host "Test with:" -ForegroundColor Green
Write-Host '  Invoke-RestMethod "http://127.0.0.1:5500/route/v1/driving/4.40,51.22;4.70,50.85?overview=false"' -ForegroundColor Green
