# AutoCAD MCP server setup for Claude Desktop (Windows)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Write-Host "== [1/3] Checking uv ==" -ForegroundColor Cyan
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found. Installing uv (official installer)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

Write-Host "== [2/3] Installing server packages (uv sync) ==" -ForegroundColor Cyan
Set-Location $root
uv sync
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found: $py" }
& $py -c "import autocad_mcp, ezdxf; print('import OK, ezdxf', ezdxf.__version__)"

Write-Host "== [3/3] Registering server in Claude Desktop config ==" -ForegroundColor Cyan
$cands = @("$env:APPDATA\Claude")
$cands += Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude" }
$dir = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $dir) { $dir = "$env:APPDATA\Claude"; New-Item -ItemType Directory -Force $dir | Out-Null }
$cfg = Join-Path $dir "claude_desktop_config.json"
if (Test-Path $cfg) {
    Copy-Item $cfg "$cfg.bak_$(Get-Date -Format yyyyMMdd_HHmmss)"
    $raw = Get-Content $cfg -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { $j = [pscustomobject]@{} } else { $j = $raw | ConvertFrom-Json }
} else { $j = [pscustomobject]@{} }
if (-not $j.PSObject.Properties["mcpServers"]) { $j | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) }
$server = [pscustomobject]@{
    command = $py
    args    = @("-m", "autocad_mcp")
    env     = [pscustomobject]@{ AUTOCAD_MCP_BACKEND = "auto" }
}
if ($j.mcpServers.PSObject.Properties["autocad-mcp"]) { $j.mcpServers."autocad-mcp" = $server }
else { $j.mcpServers | Add-Member -NotePropertyName "autocad-mcp" -NotePropertyValue $server }
$out = $j | ConvertTo-Json -Depth 50
[IO.File]::WriteAllText($cfg, $out, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Config written: $cfg" -ForegroundColor Green
Write-Host ""
Write-Host "DONE. Now fully quit Claude Desktop (tray icon > Quit) and start it again." -ForegroundColor Green
