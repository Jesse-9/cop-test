param(
  [string]$DumpPath = "ref/fmpmm.dump",
  [string]$Volume = "cop-test-neo4j-data",
  [string]$Image = "neo4j:5.13-community",
  [string]$Container = "cop-test-neo4j",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$composePath = Join-Path $PSScriptRoot "docker-compose.yml"

if (-not (Test-Path $DumpPath)) {
  throw "Dump file not found: $DumpPath"
}
if (-not $env:NEO4J_AUTH -or -not $env:NEO4J_AUTH.Contains("/")) {
  throw "Set NEO4J_AUTH='neo4j/<password>' before running this script."
}

if (-not $Force) {
  $answer = Read-Host "This overwrites the local Neo4j database in volume '$Volume'. Type 'yes' to continue"
  if ($answer -ne "yes") {
    throw "Aborted."
  }
}

$absDump = (Resolve-Path $DumpPath).Path
$authParts = $env:NEO4J_AUTH.Split("/", 2)
$neoUser = $authParts[0]
$neoPassword = $authParts[1]

Write-Host "[1/5] Stop Neo4j compose service if running..."
try {
  docker compose -f $composePath stop neo4j | Out-Host
} catch {
  Write-Host "No running compose service; continuing."
}

Write-Host "[2/5] Ensure named data volume exists..."
docker volume create $Volume | Out-Null

Write-Host "[3/5] Import dump with official neo4j-admin..."
docker run --rm `
  -v "${Volume}:/data" `
  -v "${absDump}:/import/neo4j.dump:ro" `
  $Image `
  neo4j-admin database load neo4j --from-path=/import --overwrite-destination=true | Out-Host
if ($LASTEXITCODE -ne 0) { throw "neo4j-admin database load failed." }

Write-Host "[4/5] Migrate database to Neo4j 5 format..."
docker run --rm `
  -v "${Volume}:/data" `
  $Image `
  neo4j-admin database migrate neo4j --verbose | Out-Host
if ($LASTEXITCODE -ne 0) { throw "neo4j-admin database migrate failed." }

Write-Host "[5/5] Start Neo4j and wait for readiness..."
docker compose -f $composePath up -d neo4j | Out-Host
$maxTry = 30
for ($i = 1; $i -le $maxTry; $i++) {
  docker exec $Container cypher-shell -u $neoUser -p $neoPassword "RETURN 1;" | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Neo4j import completed and service is ready."
    exit 0
  }
  Start-Sleep -Seconds 2
  Write-Host "Waiting Neo4j... attempt $i/$maxTry"
}

throw "Neo4j did not become ready after import."
