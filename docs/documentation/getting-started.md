# 🚀 Getting Started

This guide takes you from a fresh clone to a running PrintMCP server connected to your AI
assistant. Budget about **10 minutes**.

> **New here?** PrintMCP is an [MCP](https://modelcontextprotocol.io) server. You don't run it
> like a normal app — an MCP *client* (Claude Desktop, Claude Code, etc.) launches it and talks
> to it over stdin/stdout. This guide ends with registering it in a client.

---

## 1. Prerequisites

| Tool | Why | Check |
|------|-----|-------|
| **Python ≥ 3.10** | Runtime | `python --version` |
| **[uv](https://docs.astral.sh/uv/)** *(recommended)* | Installs & runs PrintMCP (or use `pipx`) | `uv --version` |
| **A Thingiverse token** | Level 1 (search/download) | [Get one below](#3-configure-your-credentials) |
| **Ultimaker Cura** *(optional)* | Level 2 (slicing) | Only if you'll slice |
| **OctoPrint + API key** *(optional)* | Level 3 (printing) | Only if you'll print |

> [!TIP]
> The three levels are **independent**. You can finish this guide with *only* a Thingiverse
> token and start searching/downloading immediately — add Cura and OctoPrint later.

If you don't have `uv` yet:

```bash
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. Install

**From PyPI** (puts a `printmcp` command on your PATH):

```bash
uv tool install printmcp      # or:  pipx install printmcp
```

**From source** (for development, or to get the latest unreleased code):

```bash
git clone https://github.com/SourceBox-LLC/PrintMCP
cd PrintMCP
uv sync                       # creates .venv, installs PrintMCP + the dev group
```

Verify the install and see what's configured:

```bash
printmcp --check              # from source: uv run printmcp --check
```

This reports the setup status of each level (it never prints your secrets). Right after install
it'll show everything as missing — that's expected; you'll configure credentials next. Other handy
commands: `printmcp --version` and `printmcp --help`.

---

## 3. Configure your credentials

PrintMCP reads configuration from environment variables, loaded automatically from a `.env`
file in the project root. Copy the template:

```bash
cp .env.example .env     # Windows: copy .env.example .env
```

Then fill in what you need. For the full list, see **[Configuration](configuration.md)**.

### Thingiverse token (Level 1 — required to search/download)

1. Go to <https://www.thingiverse.com/apps/create> and register an app (the free
   "Desktop/Web" type is fine).
2. Copy its **App Token**.
3. Put it in `.env`:

   ```dotenv
   THINGIVERSE_TOKEN=your-app-token-here
   ```

### OctoPrint (Level 3 — only if you'll print)

```dotenv
OCTOPRINT_URL=http://octopi.local      # or http://<printer-ip>:<port>
OCTOPRINT_API_KEY=your-octoprint-api-key
```

Get the key from OctoPrint → **Settings → API** (global key), or a per-user **Application
Key**. See [Configuration](configuration.md#octoprint-level-3) for details.

> [!WARNING]
> `.env` holds secrets. It's already git-ignored — **never commit it**. Only `.env.example`
> (with empty values) belongs in the repo.

### Cura (Level 2 — usually automatic)

Nothing to configure if Cura is installed in the standard location — PrintMCP auto-detects it.

> [!TIP]
> Downloaded models are saved to your OS Downloads folder by default
> (`~/Downloads` on macOS/Linux, `%USERPROFILE%\Downloads` on Windows).
> Set `PRINTMCP_DOWNLOAD_DIR` in `.env` to use a custom location.
Only set `PRINTMCP_CURA_DIR` if auto-detection fails. See
[Configuration](configuration.md#cura-level-2).

---

## 4. Confirm your configuration

Re-run the check now that you've set up `.env` — the levels you configured should switch to `OK`:

```bash
uv run printmcp --check
```

You can also start the server directly as a sanity check:

```bash
uv run printmcp
```

It will **block silently**, waiting for an MCP client on stdin/stdout. That's correct
behavior — press `Ctrl+C` to stop. Normally you don't run it by hand; the client does.

---

## 5. Register with an MCP client

### Automatic (Windows) — recommended

The setup script detects your installed clients (Claude Code, Claude Desktop, Cursor, Windsurf,
opencode), lets you choose one, and configures it:

```powershell
.\scripts\setup-mcp.ps1
```

> [!IMPORTANT]
> **Quit the client before running it.** MCP clients rewrite their config when they close, so a
> setup applied while the client is open would be lost. If your chosen client is running, the
> script stops and asks you to close it and re-run. Details in
> [scripts/README.md](../scripts/README.md).

Prefer to do it by hand? The manual steps for each client follow.

### Claude Desktop

Add this to `claude_desktop_config.json` (point `--directory` at your clone):

```json
{
  "mcpServers": {
    "printmcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\Sbuss\\Documents\\Software Development\\Projects\\PrintMCP",
        "printmcp"
      ]
    }
  }
}
```

Restart Claude Desktop. PrintMCP's tools will appear in the tool list.

### Claude Code CLI

```bash
claude mcp add printmcp -- uv run --directory "C:\Users\Sbuss\Documents\Software Development\Projects\PrintMCP" printmcp
```

---

## 6. Your first print

Setup's done — from here on, you just **talk to your assistant**. Head to
**[Tutorial 1 · Find Something to Print](tutorials/01-find-and-download.md)** and find your first
model by simply asking for it.

---

## Troubleshooting

Hitting an error? The [Troubleshooting](troubleshooting.md) guide covers the common ones — like
`THINGIVERSE_TOKEN is not set`, Cura not being found, or the printer being unreachable.
