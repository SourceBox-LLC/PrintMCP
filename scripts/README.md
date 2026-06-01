# PrintMCP setup scripts

Helper scripts for wiring PrintMCP into your MCP client.

## `setup-mcp.ps1` (Windows / PowerShell)

Detects which supported MCP clients are installed, lets you pick one, and writes
the PrintMCP server into that client's configuration — so it launches
automatically the next time the client starts.

**Supported clients:** Claude Code (CLI), Claude Desktop, Cursor, Windsurf, opencode.

### Usage

From the repository root:

```powershell
# Interactive: detect clients, choose one, configure it
.\scripts\setup-mcp.ps1

# Just list what was detected (no changes)
.\scripts\setup-mcp.ps1 -List

# Configure a specific client without the menu
.\scripts\setup-mcp.ps1 -Client cursor
```

Client ids for `-Client`: `claude-code`, `claude-desktop`, `cursor`, `windsurf`, `opencode`.

### ⚠️ Close the client first

MCP clients keep their configuration in memory and **rewrite it when they exit**,
which would erase anything this script changes while the client is open. If the
client you choose is running, the script stops and asks you to quit it and run
again. Fully quit it (from the tray/taskbar, not just the window), then re-run.

> Advanced: `-Force` applies the config even if the client is running. Not
> recommended — your change may be clobbered when the client next closes.

### What it does

- Resolves `uv` and the PrintMCP project root (the repo this script lives in).
- Backs up the client's existing config (`*.printmcp-backup-<timestamp>`) before
  any change.
- Adds a `printmcp` server that runs `uv run --directory <project> printmcp`,
  **without disturbing** other servers you've configured.
- For Claude Code, uses the official `claude mcp add` CLI (user scope) rather
  than hand-editing `~/.claude.json`.
- Writes UTF-8 **without a BOM** so Electron/Node-based clients parse it.

No secrets are written into any client config — PrintMCP reads those from the
project's `.env` at startup. Set that up separately; see
[docs/getting-started.md](../docs/getting-started.md).

### Requirements

- Windows PowerShell 5.1+ (ships with Windows) or PowerShell 7+.
- [`uv`](https://docs.astral.sh/uv/) on `PATH`.
- The client you're configuring, installed.

### Exit codes

| Code | Meaning |
|-----:|---------|
| `0` | Configured successfully (or `-List` shown). |
| `1` | Error (no `uv`, no clients detected, unparseable existing config, …). |
| `2` | Chosen client is running — quit it and re-run. |
