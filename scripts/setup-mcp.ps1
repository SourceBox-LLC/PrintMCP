#Requires -Version 5.1
<#
.SYNOPSIS
    Auto-configure PrintMCP for a local MCP client (Claude Code, Claude Desktop,
    Cursor, Windsurf, or opencode).

.DESCRIPTION
    Detects which supported MCP clients are installed on this machine, lets you
    pick one, and writes the PrintMCP server into that client's MCP config so it
    launches `uv run --directory <project> printmcp` automatically.

    IMPORTANT: GUI clients keep their config in memory and rewrite it on exit, so
    edits made while the client is running get clobbered. If the chosen client is
    running, this script stops and asks you to close it and run again. (Override
    with -Force only if you know what you're doing.)

    Existing configs are backed up before any change, and PrintMCP is added
    without disturbing other servers you've configured.

.PARAMETER Client
    Skip the menu and target a client by id: claude-code | claude-desktop |
    cursor | windsurf | opencode.

.PARAMETER List
    Just list detected clients and their config paths, then exit.

.PARAMETER Force
    Apply the config even if the client appears to be running. Not recommended.

.EXAMPLE
    .\scripts\setup-mcp.ps1
    Interactive: detect clients, choose one, configure it.

.EXAMPLE
    .\scripts\setup-mcp.ps1 -List
    Show which clients were detected.

.EXAMPLE
    .\scripts\setup-mcp.ps1 -Client cursor
    Configure Cursor non-interactively.

.NOTES
    PrintMCP reads its secrets from the project's .env file (loaded at startup),
    so no credentials are written into any client config.
#>
[CmdletBinding()]
param(
    [ValidateSet('claude-code', 'claude-desktop', 'cursor', 'windsurf', 'opencode')]
    [string]$Client,
    [switch]$List,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ServerName = 'printmcp'

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
function Write-Info([string]$m) { Write-Host $m }
function Write-Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok([string]$m) { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn2([string]$m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err2([string]$m) { Write-Host "[FAIL] $m" -ForegroundColor Red }

# --------------------------------------------------------------------------- #
# Preflight: locate uv and the PrintMCP project
# --------------------------------------------------------------------------- #
function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Could not find 'uv' on PATH. Install it from https://docs.astral.sh/uv/ and re-run."
    }
    return $cmd.Source
}

function Resolve-ProjectRoot {
    # This script lives in <project>\scripts\, so the project root is its parent.
    $root = Split-Path -Parent $PSScriptRoot
    $pyproject = Join-Path $root 'pyproject.toml'
    if (-not (Test-Path $pyproject)) {
        throw "Could not find pyproject.toml at $root. Run this script from inside the PrintMCP repo (scripts\setup-mcp.ps1)."
    }
    if (-not (Select-String -Path $pyproject -Pattern 'name\s*=\s*"printmcp"' -Quiet)) {
        Write-Warn2 "pyproject.toml at $root doesn't look like PrintMCP; continuing anyway."
    }
    return (Resolve-Path $root).Path
}

# --------------------------------------------------------------------------- #
# JSON read/write (UTF-8 no BOM; high depth so nested config isn't truncated)
# --------------------------------------------------------------------------- #
function Read-JsonOrInit([string]$Path) {
    if (-not (Test-Path $Path)) { return [pscustomobject]@{} }
    $raw = Get-Content -Raw -Path $Path -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { return [pscustomobject]@{} }
    # Strip a UTF-8 BOM if present so ConvertFrom-Json doesn't choke.
    $raw = $raw -replace "^\xEF\xBB\xBF", ""
    $raw = $raw.TrimStart([char]0xFEFF)
    try {
        return ($raw | ConvertFrom-Json)
    }
    catch {
        throw "EXISTING_UNPARSEABLE"
    }
}

function Write-JsonNoBom([string]$Path, $Object) {
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = $Object | ConvertTo-Json -Depth 32
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

function Test-HasProp($Object, [string]$Name) {
    if ($null -eq $Object) { return $false }
    return ($Object.PSObject.Properties.Name -contains $Name)
}

function Set-Prop($Object, [string]$Name, $Value) {
    if (Test-HasProp $Object $Name) { $Object.$Name = $Value }
    else { Add-Member -InputObject $Object -NotePropertyName $Name -NotePropertyValue $Value }
}

function Backup-IfExists([string]$Path) {
    if (Test-Path $Path) {
        $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
        $backup = "$Path.printmcp-backup-$stamp"
        Copy-Item -Path $Path -Destination $backup -Force
        Write-Info "      backed up existing config -> $backup"
    }
}

# --------------------------------------------------------------------------- #
# Client descriptors
# --------------------------------------------------------------------------- #
function Get-ClaudeDesktopConfigPath {
    # MSIX builds read from a virtualized Roaming path; prefer it when present.
    $pkgRoot = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path $pkgRoot) {
        $msix = Get-ChildItem -Path $pkgRoot -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($msix) {
            $msixDir = Join-Path $msix.FullName 'LocalCache\Roaming\Claude'
            if (Test-Path $msixDir) { return (Join-Path $msixDir 'claude_desktop_config.json') }
        }
    }
    return (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')
}

function Get-OpencodeConfigPath {
    $base = $env:XDG_CONFIG_HOME
    if ([string]::IsNullOrWhiteSpace($base)) { $base = Join-Path $env:USERPROFILE '.config' }
    $dir = Join-Path $base 'opencode'
    $jsonc = Join-Path $dir 'opencode.jsonc'
    if (Test-Path $jsonc) { return $jsonc }       # respect an existing .jsonc
    return (Join-Path $dir 'opencode.json')
}

function Get-Clients {
    $up = $env:USERPROFILE

    $clients = @()

    # --- Claude Code (CLI) ---
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    $clients += [pscustomobject]@{
        Id          = 'claude-code'
        Name        = 'Claude Code (CLI)'
        Format      = 'claude-cli'
        ConfigPath  = (Join-Path $up '.claude.json') + '  (user scope, via `claude mcp add`)'
        Detected    = [bool]$claudeCmd -or (Test-Path (Join-Path $up '.claude.json'))
        Processes   = @('claude')
        CliPath     = if ($claudeCmd) { $claudeCmd.Source } else { $null }
    }

    # --- Claude Desktop ---
    $cdPath = Get-ClaudeDesktopConfigPath
    $clients += [pscustomobject]@{
        Id          = 'claude-desktop'
        Name        = 'Claude Desktop'
        Format      = 'mcpServers'
        ConfigPath  = $cdPath
        Detected    = (Test-Path (Split-Path -Parent $cdPath))
        Processes   = @('claude', 'Claude')
        CliPath     = $null
    }

    # --- Cursor ---
    $cursorCfg = Join-Path $up '.cursor\mcp.json'
    $cursorExe = Join-Path $env:LOCALAPPDATA 'Programs\cursor\Cursor.exe'
    $clients += [pscustomobject]@{
        Id          = 'cursor'
        Name        = 'Cursor'
        Format      = 'mcpServers'
        ConfigPath  = $cursorCfg
        Detected    = (Test-Path (Join-Path $up '.cursor')) -or (Test-Path $cursorExe) -or [bool](Get-Command cursor -ErrorAction SilentlyContinue)
        Processes   = @('Cursor')
        CliPath     = $null
    }

    # --- Windsurf ---
    $wsCfg = Join-Path $up '.codeium\windsurf\mcp_config.json'
    $wsExe = Join-Path $env:LOCALAPPDATA 'Programs\Windsurf\Windsurf.exe'
    $clients += [pscustomobject]@{
        Id          = 'windsurf'
        Name        = 'Windsurf'
        Format      = 'mcpServers'
        ConfigPath  = $wsCfg
        Detected    = (Test-Path (Join-Path $up '.codeium\windsurf')) -or (Test-Path $wsExe)
        Processes   = @('Windsurf')
        CliPath     = $null
    }

    # --- opencode ---
    $ocCfg = Get-OpencodeConfigPath
    $clients += [pscustomobject]@{
        Id          = 'opencode'
        Name        = 'opencode'
        Format      = 'opencode'
        ConfigPath  = $ocCfg
        Detected    = (Test-Path (Split-Path -Parent $ocCfg)) -or [bool](Get-Command opencode -ErrorAction SilentlyContinue)
        Processes   = @('opencode')
        CliPath     = $null
    }

    return $clients
}

function Test-ClientRunning($ClientObj) {
    foreach ($n in $ClientObj.Processes) {
        $p = Get-Process -Name $n -ErrorAction SilentlyContinue
        if ($p) { return $true }
    }
    return $false
}

# --------------------------------------------------------------------------- #
# Apply handlers (one per config format)
# --------------------------------------------------------------------------- #
function Apply-McpServers {
    param($ConfigPath, $UvPath, $ProjectRoot, [string]$ContainerKey = 'mcpServers')

    try { $root = Read-JsonOrInit $ConfigPath }
    catch {
        Show-ManualInstructions -ConfigPath $ConfigPath -UvPath $UvPath -ProjectRoot $ProjectRoot -Format 'mcpServers'
        throw "Existing config at $ConfigPath isn't valid JSON; not overwriting it."
    }

    Backup-IfExists $ConfigPath

    if (-not (Test-HasProp $root $ContainerKey)) {
        Set-Prop $root $ContainerKey ([pscustomobject]@{})
    }
    $server = [ordered]@{
        command = $UvPath
        args    = @('run', '--directory', $ProjectRoot, $ServerName)
        env     = @{}
    }
    Set-Prop $root.$ContainerKey $ServerName $server
    Write-JsonNoBom $ConfigPath $root
}

function Apply-Opencode {
    param($ConfigPath, $UvPath, $ProjectRoot)

    $isNew = -not (Test-Path $ConfigPath)
    try { $root = Read-JsonOrInit $ConfigPath }
    catch {
        Show-ManualInstructions -ConfigPath $ConfigPath -UvPath $UvPath -ProjectRoot $ProjectRoot -Format 'opencode'
        throw "Existing config at $ConfigPath isn't valid JSON; not overwriting it."
    }

    Backup-IfExists $ConfigPath

    if ($isNew -and -not (Test-HasProp $root '$schema')) {
        Set-Prop $root '$schema' 'https://opencode.ai/config.json'
    }
    if (-not (Test-HasProp $root 'mcp')) {
        Set-Prop $root 'mcp' ([pscustomobject]@{})
    }
    $server = [ordered]@{
        type    = 'local'
        command = [string[]]@($UvPath, 'run', '--directory', $ProjectRoot, $ServerName)
        enabled = $true
    }
    Set-Prop $root.'mcp' $ServerName $server
    Write-JsonNoBom $ConfigPath $root
}

function Apply-ClaudeCli {
    param($ClientObj, $UvPath, $ProjectRoot)

    if (-not $ClientObj.CliPath) {
        throw "The 'claude' CLI isn't on PATH, so PrintMCP can't be added automatically. Install Claude Code, or add it manually (see the printed instructions)."
    }
    # Replace any existing entry, then add fresh. Remove failures are non-fatal.
    try { & $ClientObj.CliPath mcp remove $ServerName --scope user 2>$null | Out-Null } catch {}
    & $ClientObj.CliPath mcp add --scope user --transport stdio $ServerName -- $UvPath run --directory $ProjectRoot $ServerName
    if ($LASTEXITCODE -ne 0) {
        throw "`claude mcp add` exited with code $LASTEXITCODE."
    }
}

function Show-ManualInstructions {
    param($ConfigPath, $UvPath, $ProjectRoot, [string]$Format)
    # Build the snippet with the JSON serializer so paths are properly escaped
    # (backslashes doubled) and the output is copy-paste-valid JSON.
    if ($Format -eq 'opencode') {
        $snippet = [ordered]@{
            mcp = [ordered]@{
                $ServerName = [ordered]@{
                    type    = 'local'
                    command = [string[]]@($UvPath, 'run', '--directory', $ProjectRoot, $ServerName)
                    enabled = $true
                }
            }
        }
    }
    else {
        $snippet = [ordered]@{
            mcpServers = [ordered]@{
                $ServerName = [ordered]@{
                    command = $UvPath
                    args    = @('run', '--directory', $ProjectRoot, $ServerName)
                    env     = @{}
                }
            }
        }
    }
    Write-Info ""
    Write-Warn2 "Add this to $ConfigPath manually:"
    Write-Info ($snippet | ConvertTo-Json -Depth 32)
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
try {
    Write-Step "PrintMCP client setup"

    $uv = Resolve-Uv
    $projectRoot = Resolve-ProjectRoot
    Write-Info "      uv:      $uv"
    Write-Info "      project: $projectRoot"
    Write-Info ""

    $clients = Get-Clients

    if ($List) {
        Write-Step "Detected MCP clients"
        foreach ($c in $clients) {
            $tag = if ($c.Detected) { "[detected]    " } else { "[not found]   " }
            $color = if ($c.Detected) { 'Green' } else { 'DarkGray' }
            Write-Host ("  {0} {1,-18} {2}" -f $tag, $c.Name, $c.ConfigPath) -ForegroundColor $color
        }
        exit 0
    }

    # Choose the target client.
    $target = $null
    if ($Client) {
        $target = $clients | Where-Object { $_.Id -eq $Client } | Select-Object -First 1
        if (-not $target.Detected) {
            Write-Warn2 "$($target.Name) wasn't detected on this machine, but proceeding because you named it explicitly."
        }
    }
    else {
        $detected = @($clients | Where-Object { $_.Detected })
        if ($detected.Count -eq 0) {
            Write-Err2 "No supported MCP clients were detected."
            Write-Info "Checked: Claude Code, Claude Desktop, Cursor, Windsurf, opencode."
            Write-Info "Install one, or re-run with -Client <id> to force a choice."
            exit 1
        }
        Write-Step "Which client should I configure?"
        for ($i = 0; $i -lt $detected.Count; $i++) {
            Write-Host ("  [{0}] {1}" -f ($i + 1), $detected[$i].Name)
            Write-Host ("      {0}" -f $detected[$i].ConfigPath) -ForegroundColor DarkGray
        }
        Write-Host ""
        $choice = Read-Host "Enter a number (1-$($detected.Count)), or Q to quit"
        if ($choice -match '^[Qq]') { Write-Info "Cancelled."; exit 0 }
        $idx = 0
        if (-not [int]::TryParse($choice, [ref]$idx) -or $idx -lt 1 -or $idx -gt $detected.Count) {
            Write-Err2 "Invalid selection."
            exit 1
        }
        $target = $detected[$idx - 1]
    }

    Write-Info ""
    Write-Step "Target: $($target.Name)"

    # The crucial guard: a running client will overwrite its config on exit.
    if (Test-ClientRunning $target) {
        if ($Force) {
            Write-Warn2 "$($target.Name) appears to be running, but -Force was given. Continuing."
        }
        else {
            Write-Err2 "$($target.Name) is currently running."
            Write-Info ""
            Write-Info "  MCP clients keep their config in memory and rewrite it when they close,"
            Write-Info "  which would erase the changes this script makes. Please:"
            Write-Info ""
            Write-Info "    1. Fully quit $($target.Name) (close it from the tray/taskbar, not just the window)."
            Write-Info "    2. Run this script again."
            Write-Info ""
            Write-Info "  (Advanced: re-run with -Force to configure anyway.)"
            exit 2
        }
    }

    # Apply per format.
    switch ($target.Format) {
        'claude-cli'  { Apply-ClaudeCli -ClientObj $target -UvPath $uv -ProjectRoot $projectRoot }
        'opencode'    { Apply-Opencode -ConfigPath $target.ConfigPath -UvPath $uv -ProjectRoot $projectRoot }
        default       { Apply-McpServers -ConfigPath $target.ConfigPath -UvPath $uv -ProjectRoot $projectRoot }
    }

    Write-Ok "PrintMCP configured for $($target.Name)."
    Write-Info ""
    Write-Step "Next steps"
    Write-Info "  1. Make sure your .env is set up in:"
    Write-Info "       $projectRoot"
    Write-Info "     (copy .env.example to .env and fill in THINGIVERSE_TOKEN, and the"
    Write-Info "      OCTOPRINT_* values if you'll print). See docs/getting-started.md."
    if ($target.Format -eq 'claude-cli') {
        Write-Info "  2. Start a new 'claude' session - PrintMCP's tools will be available."
    }
    else {
        Write-Info "  2. Start $($target.Name). PrintMCP's tools will load on launch."
    }
    Write-Info ""
    exit 0
}
catch {
    Write-Err2 $_.Exception.Message
    exit 1
}
