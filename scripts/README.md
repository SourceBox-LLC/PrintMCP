# PrintMCP setup scripts

Helper scripts for wiring PrintMCP into your MCP client. They detect which
supported clients are installed, let you pick one, and write the PrintMCP server
into that client's configuration — so it launches automatically next time the
client starts.

**Supported clients:** Claude Code (CLI), Claude Desktop, Cursor, Windsurf, opencode.

- **Windows:** [`setup-mcp.ps1`](#setup-mcpps1-windows--powershell) (PowerShell)
- **macOS / Linux:** [`setup-mcp.sh`](#setup-mcpsh-macos--linux-bash) (bash)

Both behave the same way and take the same client ids; only the invocation and
flag syntax differ.

---

## `setup-mcp.ps1` (Windows / PowerShell)

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

---

## `setup-mcp.sh` (macOS / Linux, bash)

### Usage

From the repository root:

```bash
# Interactive: detect clients, choose one, configure it
./scripts/setup-mcp.sh

# Just list what was detected (no changes)
./scripts/setup-mcp.sh --list

# Configure a specific client without the menu
./scripts/setup-mcp.sh --client cursor
```

Client ids for `--client`: `claude-code`, `claude-desktop`, `cursor`, `windsurf`, `opencode`.

If it isn't already executable: `chmod +x scripts/setup-mcp.sh` (or run it as
`bash scripts/setup-mcp.sh`).

### ⚠️ Close the client first

Same rule as the PowerShell version: if the client you pick is running, the
script stops (exit `2`) and asks you to quit it and re-run, because clients
rewrite their config on exit. `--force` overrides.

### Notes

- Config locations are resolved per-OS — e.g. Claude Desktop lives at
  `~/Library/Application Support/Claude/` on macOS and `~/.config/Claude/` on
  Linux; Cursor at `~/.cursor/mcp.json`; Windsurf at
  `~/.codeium/windsurf/mcp_config.json`; opencode under `$XDG_CONFIG_HOME`.
- JSON editing is done with Python (already required for a Python project), so
  there's **no `jq` dependency**.
- Same guarantees as the Windows script: backs up existing configs, preserves
  your other servers, uses `claude mcp add` for Claude Code, and won't overwrite
  an unparseable config. Exit codes match the table above.

### Requirements

- `bash`, plus `python3` (or `python`) and [`uv`](https://docs.astral.sh/uv/) on `PATH`.
- `pgrep` or `ps` (standard on macOS/Linux) for the running-client check.

### Development

`setup-mcp.sh` is linted with [ShellCheck](https://www.shellcheck.net/) in CI on every push.
To run it locally:

```bash
shellcheck scripts/*.sh
# or, without a system install (bundled binary via uv):
uvx --from shellcheck-py shellcheck scripts/*.sh
```
