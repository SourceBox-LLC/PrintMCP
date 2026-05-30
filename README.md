# PrintMCP

An [MCP](https://modelcontextprotocol.io) server that automates the 3D-printing
pipeline, so an agent can take a request like *"I want to print a coffee cup"*
and carry it from model to finished print.

It is built in three levels:

| Level | Scope | Status |
|-------|-------|--------|
| **1. Source** | Search for and download 3D model files | ✅ Implemented (Thingiverse) |
| **2. Slice** | Slice models to G-code via the CuraEngine slicer | ✅ Implemented (Ultimaker Cura) |
| **3. Print** | Manage and control printing (OctoPrint / Moonraker) | ✅ Implemented (OctoPrint) |

This release covers the full pipeline: search Thingiverse, inspect a model
(including its license), download its files, slice a downloaded model into
printer-ready G-code with the headless CuraEngine, then upload it to an
OctoPrint server and drive the print — start, monitor, pause/cancel, and control
the printer's heaters and motors.

### Safety: physical-actuation tools require `confirm=true`

Level 3 drives a real machine. Every tool that physically actuates the printer
(start a print, set a temperature, home/move the head, pause/cancel, connect)
defaults to a **dry run**: it describes what *would* happen and sends nothing.
Pass `confirm=true` to actually act. Read-only tools (status, file list, job
progress) never need it.

## Tools

| Tool | Description |
|------|-------------|
| `thingiverse_search_models` | Keyword search for printable "things". Returns candidate models with IDs. |
| `thingiverse_get_model` | Details for one thing: **license**, description, and the list of downloadable files. |
| `thingiverse_download_model` | Download a thing's files (by default only printable models: `.stl`, `.3mf`, `.obj`, `.step`, `.stp`, `.amf`, `.ply`) into the download directory. |
| `cura_slice_model` | Slice a local model (`.stl`/`.obj`/`.3mf`/`.amf`/`.ply`) into G-code with CuraEngine. Choose printer, layer height, infill, supports, adhesion, and temperatures; returns the G-code path plus estimated print time and filament. |
| `octoprint_get_status` | Printer connection state, operational state, and live tool/bed temperatures. |
| `octoprint_list_files` | List the G-code files stored on the OctoPrint server (with their server-side paths). |
| `octoprint_get_job` | The active job: file, percent complete, elapsed time, and estimated time remaining. |
| `octoprint_connect` | Open/close the printer's serial connection. *(confirm)* |
| `octoprint_upload_file` | Upload a local `.gcode` to the server; optionally select or start it. *(confirm to print)* |
| `octoprint_start_print` | Select a server-side G-code file and begin printing. *(confirm)* |
| `octoprint_control_job` | Pause, resume, or cancel the running job. *(confirm)* |
| `octoprint_set_temperature` | Set a tool (nozzle) or bed target temperature. *(confirm)* |
| `octoprint_home` | Home one or more axes. *(confirm)* |
| `octoprint_move` | Jog the print head by a relative offset. *(confirm)* |

All tools accept `response_format` (`markdown` or `json`). Tools marked
*(confirm)* physically actuate the printer and require `confirm=true`; without
it they return a dry-run preview and send nothing.

## Requirements

- Python ≥ 3.10
- [uv](https://docs.astral.sh/uv/)
- A Thingiverse API token (free) — see [Configuration](#configuration)
- [Ultimaker Cura](https://ultimaker.com/software/ultimaker-cura/) (Level 2
  slicing only) — its bundled CuraEngine is auto-detected on Windows; see
  [Configuration](#configuration) to point at a non-standard install

## Setup

This is a uv-managed project. From the project root:

```bash
uv sync
```

That creates `.venv`, installs PrintMCP (editable) plus the `dev` group, and
writes `uv.lock` for reproducible installs.

## Configuration

PrintMCP reads configuration from environment variables (a `.env` file in the
project root is loaded automatically).

1. Register an app at <https://www.thingiverse.com/apps/create> and copy its
   **App Token**.
2. Create your `.env`:

   ```bash
   cp .env.example .env   # Windows: copy .env.example .env
   ```

3. Set the values:

   | Variable | Required | Default | Purpose |
   |----------|----------|---------|---------|
   | `THINGIVERSE_TOKEN` | Yes (Level 1) | — | Thingiverse REST API App Token |
   | `PRINTMCP_DOWNLOAD_DIR` | No | `~/PrintMCP/downloads` | Where downloaded models are saved |
   | `PRINTMCP_CURA_DIR` | No | auto-detected | Ultimaker Cura install folder (e.g. `C:\Program Files\UltiMaker Cura 5.11.0`). Only needed if auto-detection fails. |
   | `PRINTMCP_CURAENGINE` | No | `<cura>/CuraEngine.exe` | Full path to the CuraEngine executable, if it lives outside the Cura folder. |
   | `OCTOPRINT_URL` | Yes (Level 3) | — | Base URL of your OctoPrint server, e.g. `http://octopi.local` or `http://<ip>:<port>`. |
   | `OCTOPRINT_API_KEY` | Yes (Level 3) | — | OctoPrint API key (Settings → API, or a per-user application key). Sent only in the `X-Api-Key` header to `OCTOPRINT_URL`. |

## Running

PrintMCP is a stdio MCP server:

```bash
uv run printmcp
```

It blocks waiting for an MCP client on stdin/stdout — that is expected. Normally
you don't run it by hand; you register it with a client.

### Register with an MCP client

Add an entry like this to your client's MCP config (e.g. Claude Desktop's
`claude_desktop_config.json`):

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

For the Claude Code CLI:

```bash
claude mcp add printmcp -- uv run --directory "C:\Users\Sbuss\Documents\Software Development\Projects\PrintMCP" printmcp
```

## Example flow

1. `thingiverse_search_models(query="coffee cup")` → pick a result's `id`.
2. `thingiverse_get_model(thing_id=<id>)` → check the **license** and files.
3. `thingiverse_download_model(thing_id=<id>)` → `.stl`/`.3mf` files land in the
   download directory.
4. `cura_slice_model(model_path="<downloaded .stl>")` → a `.gcode` file is
   written next to the model (defaults to a Creality Ender-3 Pro at 0.2 mm,
   20% infill), with the estimated print time and filament usage.
5. `octoprint_get_status()` → confirm the printer is connected and ready
   (`octoprint_connect(confirm=true)` if not).
6. `octoprint_upload_file(gcode_path="<the .gcode>")` → note the returned
   server path.
7. `octoprint_start_print(path="<server path>", confirm=true)` → printing
   begins; watch it with `octoprint_get_job()`.

## Development

```bash
uv run pytest
```

The included tests are offline smoke tests (tool registration,
filename-sanitization, slice-input validation, and stats parsing); they do not
require a token, network, or a Cura install.

## Licensing of downloaded models

Models on Thingiverse carry their own licenses (often Creative Commons, some
non-commercial). `thingiverse_get_model` and `thingiverse_download_model` surface
the license — respect it before reusing, remixing, or selling a print.

## Roadmap

- **More print backends:** a Moonraker/Klipper backend exposing `moonraker_*`
  tools alongside the OctoPrint ones, so non-OctoPrint printers work too.
- **Slicing depth:** more printer profiles and quality presets, and surfacing
  CuraEngine warnings (e.g. model larger than the build volume).
- Additional model sources (Printables, MyMiniFactory) behind a shared search
  interface.
