# Changelog

All notable changes to PrintMCP are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

## [0.2.0] - 2026-07-16

### Added

- **Structured output support (MCP 2025-06-18 spec).** Every tool now declares
  an `outputSchema` and, when called with `response_format="json"`, returns a
  Pydantic model instance instead of a `json.dumps` string. This lets FastMCP
  emit `structuredContent` so clients using `structured_output=True` (e.g.
  smolagents `MCPClient`) see the tool's output schema up front, reducing
  wasted agent steps and silencing the smolagents `FutureWarning`.

  New output models:
  - Thingiverse: `SearchResult`, `SearchResultItem`, `ModelDetails`,
    `ModelFile`, `DownloadResult`, `DownloadedFile`, `SkippedFile`.
  - Cura: `SliceResult`, `SliceSettings`, `SliceStats`.
  - OctoPrint: `StatusResult`, `ServerInfo`, `ConnectionInfo`,
    `TemperatureReading`, `FileListResult`, `FileEntry`, `JobResult`,
    `ConnectResult`, `UploadResult`, `StartPrintResult`,
    `ControlJobResult`, `TemperatureResult`, `HomeResult`, `MoveResult`,
    `DryRunPreview`.

  The dry-run path (`confirm=false`) on actuation tools (connect, start print,
  set temperature, home, move, control job) now returns a `DryRunPreview`
  model on the JSON path instead of a descriptive string — so the tool always
  returns the declared type regardless of the confirm flag.

### Changed

- Return type annotations updated from `-> str` to `-> str | <ResultModel>`
  (or `-> str | DryRunPreview | <ResultModel>` for actuation tools) so
  FastMCP generates a schema from the union.

### Backward Compatibility

- The `response_format="markdown"` path (the default) is **unchanged** — it
  still returns a human-readable `str` for all tools.
- Error strings (`"Error: ..."`) are still returned as `str` on all paths.
- The JSON payload shape is identical to before — only the *type* of the
  returned Python object changed (model instance instead of JSON string).
  On the MCP wire, `structuredContent` carries the same fields.

## [0.1.1] - 2026-07-16

### Changed

- **Download directory default now points at the OS Downloads folder.**
  `get_download_dir()` honors `PRINTMCP_DOWNLOAD_DIR` as before, but when unset
  it now resolves to the OS-standard Downloads folder (Windows:
  `%USERPROFILE%\Downloads`; macOS/Linux: `~/Downloads`, honoring
  `XDG_DOWNLOAD_DIR`). If that folder can't be located, it falls back to
  `~/PrintMCP/downloads` as before. Files land in
  `<Downloads>/thing-<id>/...` instead of `~/PrintMCP/downloads/thing-<id>/...`.

### Fixed

- **Sync Docs workflow** now auto-merges via a relax-merge-restore pattern
  using an `ADMIN_TOKEN` secret, instead of the broken `--admin` flag that
  failed under `enforce_admins: true`.
- **Release workflow** switched from OIDC Trusted Publishing (which failed:
  no publisher registered) to a scoped PyPI API token (`PYPI_API_TOKEN`).

## [0.1.0] - 2026-06-01

First release. PrintMCP is an [MCP](https://modelcontextprotocol.io) server that
automates the 3D-printing pipeline across three independent levels — find a
model, slice it, print it — exposed as tools an AI assistant can call.

### Added

- **Level 1 — Source (Thingiverse).** Search for printable models, inspect one
  (including its **license** and file list), and download its files.
  Tools: `thingiverse_search_models`, `thingiverse_get_model`,
  `thingiverse_download_model`.
- **Level 2 — Slice (Cura).** Slice a local model into printer-ready G-code with
  the headless CuraEngine, choosing printer, layer height, infill, supports,
  adhesion, and temperatures; returns the estimated print time and filament use.
  Tool: `cura_slice_model`. CuraEngine is **auto-detected on Windows, macOS, and
  Linux** (overridable via `PRINTMCP_CURA_DIR`, `PRINTMCP_CURAENGINE`, or
  `PRINTMCP_CURA_RESOURCES`).
- **Level 3 — Print (OctoPrint).** Upload G-code, start/monitor/pause/cancel
  prints, and control the printer's connection, heaters, and motors.
  Tools: `octoprint_get_status`, `octoprint_list_files`, `octoprint_get_job`,
  `octoprint_connect`, `octoprint_upload_file`, `octoprint_start_print`,
  `octoprint_control_job`, `octoprint_set_temperature`, `octoprint_home`,
  `octoprint_move`.
- **Safety model.** Every tool that physically actuates the printer requires
  `confirm=true`; without it it returns a dry-run preview and sends nothing.
  Software ceilings on temperatures and movement; readiness checks before a
  print can start.
- **Command-line interface.** `printmcp --version`, `--help`, and `--check`
  (a per-level configuration self-diagnostic that never prints secrets). Running
  with no arguments starts the MCP server over stdio.
- **Setup scripts** that detect installed MCP clients (Claude Code, Claude
  Desktop, Cursor, Windsurf, opencode) and configure one automatically, refusing
  to edit a client that's running: `scripts/setup-mcp.ps1` (Windows) and
  `scripts/setup-mcp.sh` (macOS/Linux).
- **Packaging.** Installable from PyPI (`uv tool install printmcp` / `pipx`), or
  from source with `uv`. Automated release workflow publishes via PyPI Trusted
  Publishing on a version tag.
- **Documentation.** Conversational tutorials, a developer tool reference,
  configuration/safety/troubleshooting/architecture guides, and a releasing
  guide under `docs/`.
- **Quality.** A fully offline test suite (tool registration, input validation,
  the safety gate, and mock-transport HTTP plumbing) plus CI running tests on
  Python 3.10–3.13 and lint/format on every push and PR.

### Security

- API credentials (`THINGIVERSE_TOKEN`, `OCTOPRINT_API_KEY`) are read from the
  environment / `.env`, sent only in the appropriate auth header to their own
  service, and never echoed into tool output or error messages. The CuraEngine
  subprocess environment is scrubbed of these secrets.

[Unreleased]: https://github.com/SourceBox-LLC/PrintMCP/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/SourceBox-LLC/PrintMCP/releases/tag/v0.2.0
[0.1.1]: https://github.com/SourceBox-LLC/PrintMCP/releases/tag/v0.1.1
[0.1.0]: https://github.com/SourceBox-LLC/PrintMCP/releases/tag/v0.1.0
